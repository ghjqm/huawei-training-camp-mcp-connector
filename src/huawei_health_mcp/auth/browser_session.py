"""Stage-1 feasibility probe for a user-operated persistent Huawei browser session.

This module deliberately does not expose or persist request headers. It only
reports whether a successful activity query carried the two header names that
the existing connector requires.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from urllib.parse import urlsplit

_TARGET_HOST = "hihealthbase-drcn.things.dbankcloud.cn"
_TARGET_PATH = "/healthrunninggroup/v1/activityRecord:query"
_REQUIRED_HEADERS = {"authorization", "x-client-id"}
_CAPTURED_HEADERS = _REQUIRED_HEADERS | {"cookie", "x-version"}


def is_target_request(url: str) -> bool:
    """Return whether a URL is the exact known training activity endpoint."""
    try:
        parsed = urlsplit(url)
        return (
            parsed.scheme == "https"
            and parsed.hostname == _TARGET_HOST
            and parsed.path == _TARGET_PATH
        )
    except (TypeError, ValueError):
        return False


def has_required_header_names(header_names: object) -> bool:
    """Check header names only; callers must never log header values."""
    try:
        names = {str(name).lower() for name in header_names}  # type: ignore[union-attr]
    except TypeError:
        return False
    return _REQUIRED_HEADERS <= names


def extract_auth_headers(headers: dict[str, str]) -> dict[str, str] | None:
    """Copy only connector-supported authentication headers from a request."""
    normalized = {str(key).lower(): str(value) for key, value in headers.items()}
    if not _REQUIRED_HEADERS <= normalized.keys():
        return None
    if not normalized.get("authorization") or not normalized.get("x-client-id"):
        return None
    mapping = {"authorization": "Authorization", "x-client-id": "x-client-id",
               "x-version": "x-version", "cookie": "Cookie"}
    return {mapping[name]: normalized[name] for name in _CAPTURED_HEADERS if normalized.get(name)}


def safe_page_url(url: str) -> str | None:
    """Keep only an HTTPS page origin/path; discard query and fragment data."""
    try:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            return None
        host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
        port = f":{parsed.port}" if parsed.port else ""
        return f"https://{host}{port}{parsed.path or '/'}"
    except (TypeError, ValueError):
        return None


def profile_path() -> Path:
    """Return the dedicated, user-local Edge profile directory."""
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        raise RuntimeError("LOCALAPPDATA is unavailable; browser recovery is Windows-only.")
    return Path(base) / "HuaweiTrainingCampMCP" / "browser-profile"


async def capture_from_context(context, *, timeout_seconds: float, start_url: str | None = None):
    """Capture one credential-bearing 200 request without exposing its values."""
    page = context.pages[0] if context.pages else await context.new_page()
    loop = asyncio.get_running_loop()
    result = loop.create_future()
    pending: set[asyncio.Task] = set()

    async def inspect_response(response) -> None:
        if result.done() or response.status != 200 or not is_target_request(response.url):
            return
        try:
            request = response.request
            headers = await request.all_headers()
            auth_headers = extract_auth_headers(headers)
            if auth_headers:
                try:
                    page_url = safe_page_url(request.frame.url)
                except Exception:
                    page_url = None
                result.set_result((auth_headers, page_url))
        except Exception:
            return

    def on_response(response) -> None:
        if result.done():
            return
        task = asyncio.create_task(inspect_response(response))
        pending.add(task)
        task.add_done_callback(pending.discard)

    context.on("response", on_response)
    if start_url:
        try:
            await page.goto(start_url, wait_until="domcontentloaded")
        except Exception:
            # The request listener remains active; a user may navigate manually.
            pass
    try:
        return await asyncio.wait_for(result, timeout=timeout_seconds)
    finally:
        if pending:
            await asyncio.gather(*tuple(pending), return_exceptions=True)
