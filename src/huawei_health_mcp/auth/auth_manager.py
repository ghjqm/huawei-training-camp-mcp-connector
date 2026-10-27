"""Authentication state and bounded browser recovery for HTC API calls."""

from __future__ import annotations

from enum import Enum
from threading import Lock

from .credential_store import CredentialStore, CredentialStoreError, StoredCredentials
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
    """Resolve current credentials and perform at most one bounded recovery."""

    def __init__(
        self,
        store: CredentialStore,
        browser_provider: BrowserSessionProvider | None = None,
        environment_provider: EnvironmentProvider | None = None,
    ):
        self.store = store
        self.browser_provider = browser_provider or BrowserSessionProvider(store)
        self.environment_provider = environment_provider or EnvironmentProvider()
        self.status = AuthStatus.VALID
        self._lock = Lock()

    def get_headers(self) -> dict[str, str]:
        try:
            stored = self.store.load()
        except CredentialStoreError:
            stored = None
        if stored and _has_required_headers(stored.headers):
            self.status = AuthStatus.VALID
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
                current = self.store.load()
                if current is None:
                    self.status = AuthStatus.REAUTH_REQUIRED
                    return False
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


def _has_required_headers(headers: dict[str, str]) -> bool:
    return bool(headers.get("Authorization") and headers.get("x-client-id"))
