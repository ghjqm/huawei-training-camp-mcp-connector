"""scripts/test_real_htc.py 纯函数部分的轻量单元测试（无网络依赖）。

只覆盖趋势模式的参数解析与窗口边界格式化；真实请求模式
（list / detail / period / comparison / trend / trend-delta）
由用户带凭证人工执行，不进 pytest（与现有四模式保持一致）。
"""

from __future__ import annotations

import importlib.util
import re
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "test_real_htc.py"


def _load_script():
    """按路径加载脚本模块（scripts/ 不是包；模块层不发起任何网络请求）。"""
    spec = importlib.util.spec_from_file_location("test_real_htc_script", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


script = _load_script()


def test_parse_weeks_default_when_argument_missing():
    # 缺省 4（与 MCP tool 默认一致），trend / trend-delta 两模式同参
    assert script._parse_weeks(["test_real_htc.py", "trend"]) == 4
    assert script._parse_weeks(["test_real_htc.py", "trend-delta"]) == 4


def test_parse_weeks_accepts_integer():
    assert script._parse_weeks(["test_real_htc.py", "trend", "12"]) == 12
    assert script._parse_weeks(["test_real_htc.py", "trend-delta", "1"]) == 1


def test_parse_weeks_rejects_non_integer(capsys):
    # 非整数：打印错误（含原始值）并返回 None，由模式函数转退出码 1
    assert script._parse_weeks(["test_real_htc.py", "trend", "abc"]) is None
    out = capsys.readouterr().out
    assert "Invalid weeks" in out
    assert "'abc'" in out


def test_fmt_local_renders_local_time_format():
    dt = datetime(2026, 8, 16, 4, 30, tzinfo=timezone.utc)
    rendered = script._fmt_local(dt)
    # 展示格式固定（YYYY-MM-DD HH:MM），且确实转成了本地时区
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", rendered)
    assert rendered == dt.astimezone().strftime("%Y-%m-%d %H:%M")
