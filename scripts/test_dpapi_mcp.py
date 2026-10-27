"""Verify the local MCP service can query Training Camp using DPAPI only.

Output is limited to tool count and pass/fail; workout data and credentials are
never printed. This is an opt-in live API check, not part of pytest.
"""

from __future__ import annotations

import asyncio
import os
import sys
import traceback
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from src.huawei_health_mcp.auth.credential_store import CredentialStore

PROJECT_ROOT = Path(__file__).resolve().parent.parent
EXPECTED_TOOL_COUNT = 14


def _exception_shape(exc: BaseException) -> str:
    """Summarize exception classes only; omit all messages and request values."""
    if isinstance(exc, BaseExceptionGroup):
        children = ",".join(_exception_shape(child) for child in exc.exceptions)
        return f"{type(exc).__name__}[{children}]"
    frames = traceback.extract_tb(exc.__traceback__)
    origin = "/".join(f"{Path(frame.filename).name}:{frame.name}" for frame in frames[-3:])
    return f"{type(exc).__name__}@{origin}"


async def main() -> int:
    try:
        credentials = CredentialStore().load()
    except Exception:
        print("DPAPI_MCP_CHECK=FAIL (local credentials unavailable)")
        return 1
    if credentials is None:
        print("DPAPI_MCP_CHECK=FAIL (run scripts/auth_login.py first)")
        return 1

    child_env = dict(os.environ)
    for name in ("HTC_AUTHORIZATION", "HTC_CLIENT_ID", "HTC_VERSION", "HTC_COOKIE"):
        child_env.pop(name, None)
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "src.huawei_health_mcp.server"],
        cwd=str(PROJECT_ROOT),
        env=child_env,
    )
    phase = "start MCP client"
    try:
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                phase = "list MCP tools"
                tools = (await session.list_tools()).tools
                if len(tools) != EXPECTED_TOOL_COUNT:
                    print("DPAPI_MCP_CHECK=FAIL (unexpected MCP tool count)")
                    return 1
                phase = "call recent activities tool"
                result = await session.call_tool(
                    "get_recent_activities", {"limit": 1, "lookback_days": 30}
                )
                phase = "inspect tool status"
                if result.is_error:
                    print("DPAPI_MCP_CHECK=FAIL (MCP data query returned an error)")
                    return 1
                print("DPAPI_MCP_CHECK=SUCCESS (14 tools; authenticated data query succeeded)")
                return 0
    except Exception as exc:
        print(f"DPAPI_MCP_CHECK=FAIL ({phase}; {_exception_shape(exc)})")
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
