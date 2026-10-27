"""Capture a normal successful Training Camp request into Windows DPAPI storage.

The user operates the dedicated Edge profile manually. Credential values are
never printed; only the allowlisted headers from a successful activity request
are encrypted and saved locally.
"""

from __future__ import annotations

import argparse
import asyncio
import re

from playwright.async_api import async_playwright

from src.huawei_health_mcp.auth.browser_session import (
    capture_from_context,
    profile_path,
)
from src.huawei_health_mcp.auth.credential_store import CredentialStore, StoredCredentials

_PHASE = "initialization"


async def run(round_number: int) -> int:
    global _PHASE
    _PHASE = "prepare profile"
    profile = profile_path()
    profile.mkdir(parents=True, exist_ok=True)
    captured = None

    async with async_playwright() as playwright:
        _PHASE = "launch dedicated Chromium profile"
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            headless=False,
            # This machine's bundled Chromium cannot start because its Windows
            # side-by-side runtime is unavailable. Use installed Edge with this
            # dedicated data directory, never the user's normal Edge profile.
            channel="msedge",
        )
        page = context.pages[0] if context.pages else await context.new_page()

        _PHASE = "wait for the user to open the Training Camp page"
        print(f"第 {round_number} 轮：请在专用浏览器中打开你已确认可访问数据的 Huawei Training Camp 页面。")
        print("然后自行登录并查看训练记录；不要打开 health.huawei.com，探测器不会替你猜入口。")
        capture_task = asyncio.create_task(
            capture_from_context(context, timeout_seconds=180)
        )
        print("页面数据加载后回到此窗口按 Enter；成功时凭证会直接用 DPAPI 加密保存，不会显示。")
        await asyncio.to_thread(input)
        done, _ = await asyncio.wait({capture_task}, timeout=3)
        if done:
            captured = capture_task.result()
        else:
            capture_task.cancel()
            await asyncio.gather(capture_task, return_exceptions=True)
        await context.close()

    if captured:
        headers, page_url = captured
        CredentialStore().save(StoredCredentials(headers=headers, page_url=page_url))
        print("本轮通过：已捕获成功训练请求，并把认证信息加密保存到当前 Windows 用户配置。")
        return 0
    print("本轮未通过：未观察到符合条件的目标请求；没有保存认证信息。")
    return 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--round", type=int, choices=(1, 2), default=1)
    args = parser.parse_args()
    try:
        return asyncio.run(run(args.round))
    except Exception as exc:
        # Exception text from browser/network libraries can contain URLs; keep
        # diagnostics useful while removing URLs and local filesystem paths.
        message = str(exc)
        message = re.sub(r"https?://\S+", "<url>", message)
        message = re.sub(r"[A-Za-z]:\\[^\s\"']+", "<local-path>", message)
        print(f"浏览器探测在“{_PHASE}”阶段失败（{type(exc).__name__}）：{message[:500]}")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
