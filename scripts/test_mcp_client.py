"""真实 MCP 客户端验收脚本（stdio 出站链路，人工运行，不属于 pytest）。

以真实 MCP 客户端身份（mcp 库，与服务端同源协议）拉起本项目的
FastMCP 服务，走完整 JSON-RPC 出站链路：initialize → list_tools →
逐个调用全部 14 个 tool。这是「真实 MCP 客户端验收」的脚本化路径；
等价的人工路径是按 README 接入 Claude Desktop / Cherry Studio 等。

运行方式（项目根目录，凭证环境变量需已设置，与 test_real_htc.py 相同）：
    .\\.venv\\Scripts\\python.exe scripts\\test_mcp_client.py

安全原则：与 test_real_htc.py 一致——凭证只从环境变量继承给子进程，
不打印凭证/Header；输出只含工具调用结果摘要（自己的运动数据）。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 与 server.py 注册的工具一一对应（14 个）
EXPECTED_TOOLS = [
    "health_check",
    "get_recent_activities",
    "get_activity_detail",
    "get_training_summary",
    "get_session_metrics",
    "get_training_period_summary",
    "get_training_period_comparison",
    "get_training_weekly_trend",
    "get_training_weekly_trend_delta",
    "get_sleep_records",
    "get_resting_heart_rate",
    "get_hrv_stats",
    "get_athletic_performance",
    "get_personal_bests",
]

REQUIRED_ENV = ("HTC_AUTHORIZATION", "HTC_CLIENT_ID")


def _first_activity_id(obj: object) -> str | None:
    """递归找第一个 activity_id（不假设返回 JSON 的具体层级）。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "activity_id" and isinstance(v, str):
                return v
            found = _first_activity_id(v)
            if found:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _first_activity_id(item)
            if found:
                return found
    return None


def _result_text(result) -> str:
    """提取 CallToolResult 的文本内容（容错：无 text block 时返回空串）。"""
    parts = [
        getattr(block, "text", "")
        for block in (result.content or [])
        if getattr(block, "type", "") == "text"
    ]
    return "\n".join(p for p in parts if p)


def _brief(text: str, limit: int = 200) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


async def call(session: ClientSession, name: str, args: dict) -> tuple[bool, str, str]:
    """调用单个工具，返回 (成功, 完整文本, 打印用摘要)。"""
    result = await session.call_tool(name, args)
    text = _result_text(result)
    if result.isError:
        return False, text, _brief(text, 300)
    return True, text, _brief(text)


async def main() -> int:
    missing = [n for n in REQUIRED_ENV if not os.environ.get(n)]
    if missing:
        print("WARNING: env not set -> credential-dependent tools will fail: "
              + ", ".join(missing))
        print("Set them in this terminal first (same as test_real_htc.py).")
        print()

    # 以当前解释器（.venv python）拉起服务。
    # 注意：StdioServerParameters 不传 env 时，MCP SDK 会用"安全默认
    # 环境"（仅 PATH/APPDATA 等系统变量）启动子进程，HTC_* 凭证变量
    # 不会继承——必须显式传入完整父环境（Phase 6.14 修复）。
    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "src.huawei_health_mcp.server"],
        cwd=str(PROJECT_ROOT),
        env=dict(os.environ),
    )

    results: list[tuple[str, bool, str]] = []
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print("MCP initialize: OK")

            tools = sorted(t.name for t in (await session.list_tools()).tools)
            print(f"list_tools: {len(tools)} tools -> {tools}")
            absent = [t for t in EXPECTED_TOOLS if t not in tools]
            if absent:
                print(f"MISSING expected tools: {absent}")
            print()

            # 1) 无参数工具
            ok, _, brief = await call(session, "health_check", {})
            results.append(("health_check", ok, brief))

            # 2) 列表 → 从完整结果提取真实 activity_id 供 detail 族使用
            ok, full, brief = await call(session, "get_recent_activities", {"limit": 3})
            results.append(("get_recent_activities", ok, brief))
            activity_id: str | None = None
            if ok:
                try:
                    activity_id = _first_activity_id(json.loads(full))
                except Exception:
                    activity_id = None
            if not activity_id:
                print("NOTE: no activity_id obtained; detail-family tools will fail.")
                print()

            # 3-5) 依赖 activity_id 的三个工具
            for name in ("get_activity_detail", "get_training_summary", "get_session_metrics"):
                if activity_id:
                    ok, _, brief = await call(session, name, {"activity_id": activity_id})
                else:
                    ok, brief = False, "skipped: no activity_id from get_recent_activities"
                results.append((name, ok, brief))

            # 6-9) 周期 / 趋势族（默认参数，均只需一次列表请求）
            for name, args in (
                ("get_training_period_summary", {}),
                ("get_training_period_comparison", {}),
                ("get_training_weekly_trend", {"weeks": 4}),
                ("get_training_weekly_trend_delta", {"weeks": 4}),
            ):
                ok, _, brief = await call(session, name, args)
                results.append((name, ok, brief))

            # 10-12) 恢复状态族（Phase 7：睡眠 / 静息心率 / HRV）
            for name, args in (
                ("get_sleep_records", {"days": 7}),
                ("get_resting_heart_rate", {"days": 28}),
                ("get_hrv_stats", {"days": 28}),
            ):
                ok, _, brief = await call(session, name, args)
                results.append((name, ok, brief))

            # 13-14) 训练能力域（Phase 8：运动能力评估 / PB 个人纪录）
            ok, _, brief = await call(session, "get_athletic_performance", {})
            results.append(("get_athletic_performance", ok, brief))
            ok, _, brief = await call(session, "get_personal_bests", {"activity_type": "running"})
            results.append(("get_personal_bests", ok, brief))

    print("=== ACCEPTANCE SUMMARY ===")
    passed = sum(1 for _, ok, _ in results if ok)
    for name, ok, text in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {text}")
    print(f"tools: {passed}/{len(EXPECTED_TOOLS)} passed; "
          f"declared: {len(tools)}; missing: {absent or 'none'}")
    return 0 if passed == len(EXPECTED_TOOLS) and not absent else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
