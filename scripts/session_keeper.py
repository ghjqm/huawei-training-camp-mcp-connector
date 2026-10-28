"""Keep the Huawei Training Camp login alive and credentials fresh.

Holds the dedicated Edge profile open so Huawei ID session cookies never get
dropped, periodically reopens the Training Camp page, captures the fresh
request credentials into the DPAPI store, and answers immediate refresh
requests from MCP processes that hit a 401.

Usage (from the project root, with the same HTC_STATE_DIR as the MCP server):
    python -m scripts.session_keeper [--interval-minutes 15] [--minimized] [--headless]

Credential and cookie values are never printed.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import time
from datetime import datetime

from playwright.async_api import async_playwright

from src.huawei_health_mcp.auth.browser_session import (
    attach_credential_listener,
    profile_path,
)
from src.huawei_health_mcp.auth.cookie_store import (
    SessionCookieStore,
    export_cookies,
    restore_cookies,
)
from src.huawei_health_mcp.auth.credential_store import (
    CredentialStore,
    StoredCredentials,
    state_directory,
)
from src.huawei_health_mcp.auth.keeper import KeeperChannel

DEFAULT_START_URL = "https://health.cloud.huawei.com/TrainingCamp"
CAPTURE_GRACE_SECONDS = 60
TICK_SECONDS = 2


def log(message: str) -> None:
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}", flush=True)


class Keeper:
    def __init__(self, interval_minutes: float, headless: bool, minimized: bool):
        self.interval = max(1.0, interval_minutes) * 60
        self.headless = headless
        self.minimized = minimized
        self.store = CredentialStore()
        self.cookie_store = SessionCookieStore()
        self.channel = KeeperChannel(state_directory())
        self.last_capture = 0.0
        self.last_saved_auth: str | None = None
        self.start_url = DEFAULT_START_URL
        self.closed = asyncio.Event()

    def _load_existing(self) -> None:
        try:
            current = self.store.load()
        except Exception:
            current = None
        if current is not None:
            self.last_saved_auth = current.headers.get("Authorization")
            if current.page_url:
                self.start_url = current.page_url

    async def on_capture(self, headers: dict[str, str], page_url: str | None) -> None:
        self.last_capture = time.time()
        if headers.get("Authorization") == self.last_saved_auth:
            return
        try:
            self.store.save(StoredCredentials(headers=headers, page_url=page_url or self.start_url))
            self.last_saved_auth = headers.get("Authorization")
            log("已捕获并加密保存新的训练营凭证。")
        except Exception as exc:
            log(f"保存凭证失败（{type(exc).__name__}）。")

    async def open_page(self, context):
        page = context.pages[0] if context.pages else await context.new_page()
        try:
            await page.goto(self.start_url, wait_until="domcontentloaded")
            try:
                login_btn = page.locator("button.login-button")
                await login_btn.wait_for(state="visible", timeout=3000)
                await login_btn.click(timeout=2000)
            except Exception:
                pass
        except Exception as exc:
            log(f"打开训练营页面失败（{type(exc).__name__}），稍后重试。")
        return page

    async def run(self) -> int:
        self._load_existing()
        if self.channel.is_alive():
            log("已有一个保活进程在运行，本进程退出。")
            return 1
        profile = profile_path()
        profile.mkdir(parents=True, exist_ok=True)
        args = ["--start-minimized"] if self.minimized and not self.headless else []
        async with async_playwright() as playwright:
            context = await playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile), headless=self.headless,
                channel="msedge", args=args,
            )
            context.on("close", lambda *_: self.closed.set())
            restored = await restore_cookies(context, self.cookie_store)
            log(f"专用浏览器已启动，恢复了 {restored} 个登录会话 Cookie。")
            attach_credential_listener(context, self.on_capture)
            self.channel.beat()
            try:
                await self.loop(context)
            finally:
                await export_cookies(context, self.cookie_store)
                self.channel.stop()
                try:
                    await context.close()
                except Exception:
                    pass
        log("保活进程已退出。")
        return 0

    async def loop(self, context) -> None:
        await self.open_page(context)
        last_open = time.time()
        warned_for = 0.0
        exported_for = 0.0
        while not self.closed.is_set():
            self.channel.beat()
            now = time.time()
            requested = self.channel.take_request()
            if requested or now - last_open >= self.interval:
                if requested:
                    log("收到 MCP 的刷新请求，立即刷新页面。")
                await self.open_page(context)
                last_open = time.time()
            elif (
                now - last_open >= CAPTURE_GRACE_SECONDS
                and self.last_capture < last_open
                and warned_for != last_open
            ):
                warned_for = last_open
                if self.headless:
                    log("未捕获到训练请求，登录可能已失效：请去掉 --headless 重新启动并在窗口中登录。")
                else:
                    log("未捕获到训练请求，登录可能已失效：请在专用浏览器窗口中登录并打开训练记录。")
            if self.last_capture > exported_for:
                # Save cookies once after each new successful capture.
                exported_for = self.last_capture
                await export_cookies(context, self.cookie_store)
            try:
                await asyncio.wait_for(self.closed.wait(), timeout=TICK_SECONDS)
            except asyncio.TimeoutError:
                pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--interval-minutes", type=float, default=15.0,
                        help="定期刷新训练营页面的间隔（分钟），默认 15")
    parser.add_argument("--minimized", action="store_true", help="启动时最小化浏览器窗口")
    parser.add_argument("--headless", action="store_true",
                        help="无窗口运行（仅在登录会话有效时使用，无法在其中手动登录）")
    args = parser.parse_args()
    keeper = Keeper(args.interval_minutes, args.headless, args.minimized)
    try:
        return asyncio.run(keeper.run())
    except KeyboardInterrupt:
        keeper.channel.stop()
        log("已手动停止。")
        return 0
    except Exception as exc:
        keeper.channel.stop()
        message = re.sub(r"https?://\S+", "<url>", str(exc))
        message = re.sub(r"[A-Za-z]:\\[^\s\"']+", "<local-path>", message)
        log(f"保活进程异常退出（{type(exc).__name__}）：{message[:500]}")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
