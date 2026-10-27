"""Credential sources for Huawei Training Camp authentication."""

from __future__ import annotations

import asyncio
import os

from .browser_session import capture_from_context, profile_path
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
    """Refresh credentials from a normal successful Training Camp browser call."""

    def __init__(self, store: CredentialStore, *, timeout_seconds: float = 120.0):
        self.store = store
        self.timeout_seconds = timeout_seconds

    def refresh(self, start_url: str | None) -> StoredCredentials | None:
        """Reopen the isolated Edge profile at the last known app route and listen."""
        if not start_url:
            return None
        return asyncio.run(self._capture(start_url))

    async def _capture(self, start_url: str) -> StoredCredentials | None:
        from playwright.async_api import async_playwright

        profile = profile_path()
        profile.mkdir(parents=True, exist_ok=True)
        async with async_playwright() as playwright:
            context = await playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile), headless=False, channel="msedge"
            )
            try:
                headers, page_url = await capture_from_context(
                    context,
                    timeout_seconds=self.timeout_seconds,
                    start_url=start_url,
                )
                return StoredCredentials(headers=headers, page_url=page_url or start_url)
            except asyncio.TimeoutError:
                return None
            finally:
                await context.close()
