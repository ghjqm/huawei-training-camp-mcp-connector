"""Credential sources for Huawei Training Camp authentication."""

from __future__ import annotations

import asyncio
import os

from .browser_session import capture_from_context, profile_path
from .cookie_store import SessionCookieStore, export_cookies, restore_cookies
from .credential_store import CredentialStore, StoredCredentials

_ENV_TO_HEADER = {
    "HTC_AUTHORIZATION": "Authorization",
    "HTC_CLIENT_ID": "x-client-id",
    "HTC_VERSION": "x-version",
    "HTC_COOKIE": "Cookie",
}


class EnvironmentProvider:
    """Backward-compatible environment-variable credential source."""

    def get_headers(self) -> dict[str, str]:
        return {
            header: value
            for env_name, header in _ENV_TO_HEADER.items()
            if (value := os.environ.get(env_name))
        }


class BrowserSessionProvider:
    """Refresh credentials from a normal successful Training Camp browser call.

    Saved session cookies are restored into the dedicated profile first, so the
    Huawei ID login survives browser restarts. A silent headless attempt runs
    before falling back to a visible window where the user can log in.
    """

    def __init__(
        self,
        store: CredentialStore,
        *,
        timeout_seconds: float = 120.0,
        headless_timeout_seconds: float = 40.0,
        try_headless: bool = True,
        cookie_store: SessionCookieStore | None = None,
    ):
        self.store = store
        self.timeout_seconds = timeout_seconds
        self.headless_timeout_seconds = headless_timeout_seconds
        self.try_headless = try_headless
        if cookie_store is None:
            try:
                cookie_store = SessionCookieStore(store.path.parent / "session-cookies.dpapi")
            except Exception:
                cookie_store = None
        self.cookie_store = cookie_store

    def refresh(self, start_url: str | None) -> StoredCredentials | None:
        """Reopen the isolated Edge profile at the last known app route and listen."""
        if not start_url:
            return None
        if self.try_headless:
            try:
                result = asyncio.run(self._capture(
                    start_url, headless=True, timeout_seconds=self.headless_timeout_seconds
                ))
            except Exception:
                result = None
            if result is not None:
                return result
        return asyncio.run(self._capture(
            start_url, headless=False, timeout_seconds=self.timeout_seconds
        ))

    async def _capture(
        self, start_url: str, *, headless: bool = False, timeout_seconds: float | None = None
    ) -> StoredCredentials | None:
        from playwright.async_api import async_playwright

        profile = profile_path()
        profile.mkdir(parents=True, exist_ok=True)
        async with async_playwright() as playwright:
            context = await playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile), headless=headless, channel="msedge"
            )
            try:
                await restore_cookies(context, self.cookie_store)
                headers, page_url = await capture_from_context(
                    context,
                    timeout_seconds=timeout_seconds or self.timeout_seconds,
                    start_url=start_url,
                )
                await export_cookies(context, self.cookie_store)
                return StoredCredentials(headers=headers, page_url=page_url or start_url)
            except asyncio.TimeoutError:
                return None
            finally:
                await context.close()
