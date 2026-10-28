"""Authentication state and bounded browser recovery for HTC API calls."""

from __future__ import annotations

import time
from enum import Enum
from threading import Lock

from .credential_store import CredentialStore, CredentialStoreError, StoredCredentials
from .keeper import KeeperChannel
from .providers import BrowserSessionProvider, EnvironmentProvider


class AuthStatus(str, Enum):
    VALID = "VALID"
    EXPIRED = "EXPIRED"
    RECOVERING = "RECOVERING"
    REAUTH_REQUIRED = "REAUTH_REQUIRED"
    ERROR = "ERROR"


class AuthUnavailableError(RuntimeError):
    """No usable local or environment credentials are available."""


class AuthManager:
    """Resolve current credentials and perform at most one bounded recovery.

    Recovery order:
    1. Another process (the session keeper or another MCP call) already saved
       newer credentials -> use them without opening a browser.
    2. The session keeper is alive -> ask it to refresh and wait for the result
       (it owns the dedicated browser profile, so no second browser is opened).
    3. Otherwise open the dedicated browser (silent headless first, then visible).
    """

    def __init__(
        self,
        store: CredentialStore,
        browser_provider: BrowserSessionProvider | None = None,
        environment_provider: EnvironmentProvider | None = None,
        keeper: KeeperChannel | None = None,
        keeper_wait_seconds: float = 90.0,
    ):
        self.store = store
        self.browser_provider = browser_provider or BrowserSessionProvider(store)
        self.environment_provider = environment_provider or EnvironmentProvider()
        if keeper is None and isinstance(store, CredentialStore):
            # The keeper shares the credential file's state directory.
            keeper = KeeperChannel(store.path.parent)
        self.keeper = keeper
        self.keeper_wait_seconds = keeper_wait_seconds
        self.status = AuthStatus.VALID
        self._last_authorization: str | None = None
        self._lock = Lock()

    def get_headers(self) -> dict[str, str]:
        try:
            stored = self.store.load()
        except CredentialStoreError:
            stored = None
        if stored and _has_required_headers(stored.headers):
            self.status = AuthStatus.VALID
            self._last_authorization = stored.headers.get("Authorization")
            return dict(stored.headers)
        env_headers = self.environment_provider.get_headers()
        if _has_required_headers(env_headers):
            self.status = AuthStatus.VALID
            return env_headers
        self.status = AuthStatus.REAUTH_REQUIRED
        raise AuthUnavailableError(
            "Huawei Training Camp credentials are unavailable; run scripts/auth_login.py."
        )

    def mark_expired(self) -> None:
        self.status = AuthStatus.EXPIRED

    def recover(self) -> bool:
        """Capture and persist one fresh successful browser request, if possible."""
        with self._lock:
            self.status = AuthStatus.RECOVERING
            try:
                current = self._load_quietly()
                if current is None:
                    self.status = AuthStatus.REAUTH_REQUIRED
                    return False
                current_auth = current.headers.get("Authorization")
                if self._last_authorization and current_auth != self._last_authorization:
                    # Someone else refreshed the store after we read it.
                    self.status = AuthStatus.VALID
                    return True
                if self.keeper is not None and self.keeper.is_alive():
                    return self._wait_for_keeper(current_auth)
                replacement = self.browser_provider.refresh(current.page_url)
                if replacement is None or not _has_required_headers(replacement.headers):
                    self.status = AuthStatus.REAUTH_REQUIRED
                    return False
                self.store.save(replacement)
                self.status = AuthStatus.VALID
                return True
            except Exception:
                self.status = AuthStatus.ERROR
                return False

    def _wait_for_keeper(self, stale_auth: str | None) -> bool:
        assert self.keeper is not None
        self.keeper.request_refresh()
        deadline = time.monotonic() + self.keeper_wait_seconds
        while time.monotonic() < deadline:
            time.sleep(1.0)
            latest = self._load_quietly()
            if (
                latest is not None
                and _has_required_headers(latest.headers)
                and latest.headers.get("Authorization") != stale_auth
            ):
                self.status = AuthStatus.VALID
                return True
        self.status = AuthStatus.REAUTH_REQUIRED
        return False

    def _load_quietly(self) -> StoredCredentials | None:
        try:
            return self.store.load()
        except CredentialStoreError:
            return None


def _has_required_headers(headers: dict[str, str]) -> bool:
    return bool(headers.get("Authorization") and headers.get("x-client-id"))
