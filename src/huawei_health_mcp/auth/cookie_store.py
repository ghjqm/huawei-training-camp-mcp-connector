"""DPAPI-backed persistence for Huawei login cookies of the dedicated profile.

Session cookies may be lost when the dedicated browser closes. This store
keeps allowlisted Huawei cookies encrypted for the current Windows user so a
later browser launch can restore them while the server-side session is valid.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .credential_store import (
    CredentialStoreError,
    _dpapi_protect,
    _dpapi_unprotect,
    state_directory,
)

# Only cookies for these registrable domains are persisted or restored.
_ALLOWED_DOMAIN_SUFFIXES = ("huawei.com", "dbankcloud.cn", "dbankcloud.com", "hicloud.com")
_COOKIE_KEYS = ("name", "value", "domain", "path", "expires", "httpOnly", "secure", "sameSite")


def is_allowed_cookie(cookie: dict[str, Any]) -> bool:
    """Return whether a Playwright cookie belongs to a Huawei login domain."""
    domain = str(cookie.get("domain") or "").lstrip(".").lower()
    if not domain or not cookie.get("name"):
        return False
    return any(domain == s or domain.endswith("." + s) for s in _ALLOWED_DOMAIN_SUFFIXES)


def filter_cookies(cookies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep allowlisted cookies and only the fields Playwright needs to restore."""
    result = []
    for cookie in cookies:
        if not isinstance(cookie, dict) or not is_allowed_cookie(cookie):
            continue
        result.append({key: cookie[key] for key in _COOKIE_KEYS if key in cookie})
    return result


def cookies_to_restore(cookies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Select session cookies only; persistent ones already live in the profile.

    Re-adding persistent cookies could overwrite newer values that the
    browser profile stored on its own, so they are skipped.
    """
    return [
        cookie for cookie in filter_cookies(cookies)
        if cookie.get("expires", -1) in (-1, None) or float(cookie.get("expires", -1)) < 0
    ]


class SessionCookieStore:
    """Encrypt the dedicated profile's Huawei cookies for this Windows user."""

    def __init__(self, path: str | Path | None = None):
        if path is None:
            path = state_directory() / "session-cookies.dpapi"
        self.path = Path(path)

    def exists(self) -> bool:
        return self.path.is_file()

    def save(self, cookies: list[dict[str, Any]]) -> int:
        """Persist allowlisted cookies; return how many were stored."""
        selected = filter_cookies(cookies)
        if not selected:
            return 0
        payload = json.dumps({"cookies": selected}, ensure_ascii=False,
                             separators=(",", ":")).encode("utf-8")
        encrypted = _dpapi_protect(payload)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=self.path.parent, prefix=".cookies-", delete=False
            ) as handle:
                temp_name = handle.name
                handle.write(encrypted)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
        except OSError as exc:
            if temp_name:
                try:
                    os.unlink(temp_name)
                except OSError:
                    pass
            raise CredentialStoreError("Could not save encrypted session cookies.") from exc
        return len(selected)

    def load(self) -> list[dict[str, Any]]:
        try:
            encrypted = self.path.read_bytes()
        except FileNotFoundError:
            return []
        except OSError as exc:
            raise CredentialStoreError("Could not read encrypted session cookies.") from exc
        try:
            payload = json.loads(_dpapi_unprotect(encrypted).decode("utf-8"))
            cookies = payload["cookies"]
            if not isinstance(cookies, list):
                raise ValueError("invalid cookie list")
            return filter_cookies(cookies)
        except Exception as exc:
            raise CredentialStoreError(
                "Encrypted session cookies are invalid or unavailable to this Windows user."
            ) from exc

    def clear(self) -> None:
        try:
            self.path.unlink(missing_ok=True)
        except OSError as exc:
            raise CredentialStoreError("Could not remove encrypted session cookies.") from exc


async def restore_cookies(context, store: SessionCookieStore | None) -> int:
    """Best-effort: put saved session cookies back into a fresh browser context."""
    if store is None:
        return 0
    try:
        cookies = cookies_to_restore(store.load())
        if cookies:
            await context.add_cookies(cookies)
        return len(cookies)
    except Exception:
        return 0


async def export_cookies(context, store: SessionCookieStore | None) -> int:
    """Best-effort: persist the context's Huawei cookies, session ones included."""
    if store is None:
        return 0
    try:
        return store.save(await context.cookies())
    except Exception:
        return 0
