"""真实 HTC API 验证脚本（人工运行，不属于 pytest 测试套件）。

pytest 中的全部测试（tests/）仍然使用 MockTransport，不访问真实网络。

运行方式（项目根目录，需先激活虚拟环境）：
    python scripts/test_real_htc.py                  # 列表模式：最近 7 天训练列表 + parser 验证
    python scripts/test_real_htc.py detail           # 详情模式：单次训练高频 detail schema 勘探
    python scripts/test_real_htc.py period           # 周期模式：最近 7 天训练周期聚合验证
    python scripts/test_real_htc.py comparison [N]   # 对比模式：最近 N 天 vs 之前 N 天（默认 7）
    python scripts/test_real_htc.py trend [N]        # 趋势模式：最近 N 个滚动周窗口训练趋势（默认 4）
    python scripts/test_real_htc.py trend-delta [N]  # 环比模式：相邻周 older→newer 环比（默认 4）
    python scripts/test_real_htc.py sleep [N]        # 睡眠模式：最近 N 天睡眠记录 + 自洽恒等式检查（默认 14）
    python scripts/test_real_htc.py resting-hr [N]   # 静息心率模式：最近 N 天 restBpm 统计（默认 28）
    python scripts/test_real_htc.py hrv [N]          # HRV 模式：最近 N 天 avgHrv 统计（默认 28）
    python scripts/test_real_htc.py performance      # 能力模式：最新运动能力评估（跑力/状态/疲劳/预测成绩）
    python scripts/test_real_htc.py pb [type]        # PB 模式：单运动类型个人纪录（默认 running）

前置条件（环境变量 -> HTTP 请求头，映射来自用户确认的浏览器成功请求）：
    HTC_AUTHORIZATION -> Authorization: Bearer <敏感值>
    HTC_CLIENT_ID     -> x-client-id: <敏感值>
    HTC_VERSION       -> x-version: HealthKitRunningGroupPortal_6.26.4.200
    HTC_COOKIE        -> Cookie: <敏感值>

安全原则：
    - 所有值只从本地环境变量读取；未设置的变量不猜测默认值
    - 不登录、不自动获取/刷新 Cookie、不读浏览器数据库
    - 不把任何凭证写入源码 / 文件 / Git
    - 不在终端打印 Authorization / Cookie / Token / 完整 Header / 完整 JSON

详情模式（第六阶段 6.1 schema 勘探）做的事：
    1. 读取列表模式已保存的真实记录 data_temp/real_activity_records.json
       （因此需先成功运行一次列表模式）
    2. 选择一条训练（优先 activityType=56 跑步），逐字回传该记录自身的
       startTime/endTime（毫秒，来自 API 本身，不做单位换算猜测）
    3. 调用现有 query_activity_detail()，只请求 8 个核心 dataType
    4. 保存原始响应到 data_temp/real_activity_detail.json（git-ignored）
    5. 输出安全结构摘要（key 名与计数，不含完整数据）
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

# 让 `python scripts/test_real_htc.py` 也能 import src.huawei_health_mcp
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# 复用现有实现，不重复逻辑：
# - client.py：HTTP 层（HTCClient、异常、URL 常量）
# - parser.py：解析层（parse_activity_record）
# - server.py：时间范围与列表抽取辅助（与 get_recent_activities 同一逻辑）
from src.huawei_health_mcp.client import (  # noqa: E402
    ACTIVITY_RECORD_QUERY_URL,
    ATHLETIC_PERFORMANCE_URL,
    HEALTH_RECORDS_URL,
    HEALTH_RECORD_STATS_URL,
    HTCClient,
    HTCClientError,
    SAMPLE_SET_STATS_URL,
    SPORT_REPORTS_URL,
)
from src.huawei_health_mcp.parser import parse_activity_record  # noqa: E402
from src.huawei_health_mcp.period_analysis import (  # noqa: E402
    aggregate_activity_period,
)
from src.huawei_health_mcp.server import (  # noqa: E402
    _default_time_range,
    _extract_activity_list,
    fetch_athletic_performance,
    fetch_hrv_stats,
    fetch_period_comparison,
    fetch_personal_bests,
    fetch_resting_heart_rate,
    fetch_sleep_records,
    fetch_weekly_trend,
    fetch_weekly_trend_delta,
)

# 列表模式：只查最近 7 天、最多 5 条，不请求过大历史范围
LOOKBACK_DAYS = 7
LIMIT = 5

# 对比模式：两个窗口各取的条数上限（与 MCP tool 默认一致）
MAX_LIST_LIMIT_FOR_COMPARISON = 100

# 趋势模式：周窗口数缺省（与 MCP tool 默认一致）；条数上限同对比模式
DEFAULT_TREND_WEEKS = 4
MAX_LIST_LIMIT_FOR_TREND = 100

# 真实响应保存位置（.gitignore 已忽略 data_temp/，不会进入 Git）
OUTPUT_FILE = PROJECT_ROOT / "data_temp" / "real_activity_records.json"
DETAIL_OUTPUT_FILE = PROJECT_ROOT / "data_temp" / "real_activity_detail.json"
RECORDS_INPUT_FILE = OUTPUT_FILE  # 详情模式从列表模式保存的文件里选训练

# 环境变量 -> HTTP 请求头 映射（键名来自用户确认的浏览器成功请求）
ENV_TO_HEADER = {
    "HTC_AUTHORIZATION": "Authorization",
    "HTC_CLIENT_ID": "x-client-id",
    "HTC_VERSION": "x-version",
    "HTC_COOKIE": "Cookie",
}

# 已确认的认证链路关键变量（Authorization 已被确认为关键请求头）：
# 缺失任何一个都清晰报错退出，绝不猜测默认值
REQUIRED_ENV = ("HTC_AUTHORIZATION", "HTC_CLIENT_ID")

# 缺失不阻塞运行，但对应 Header 会被省略（打印警告）
OPTIONAL_ENV = ("HTC_VERSION", "HTC_COOKIE")

# 详情模式第一轮只请求 8 个核心 dataType（来自用户确认的浏览器请求）
DETAIL_DATA_TYPES = [
    "com.huawei.instantaneous.exercise_heart_rate",
    "com.huawei.recovery_heart_rate",
    "com.huawei.instantaneous.speed",
    "com.huawei.instantaneous.steps.rate",
    "com.huawei.continuous.run.posture",
    "com.huawei.instantaneous.altitude",
    "com.huawei.instantaneous.location.sample",
    "com.huawei.analog_power",
]


def build_headers() -> dict[str, str] | None:
    """从环境变量构造请求 headers。

    - 缺少必要变量时打印缺失的变量名（只打印名字，绝不打印值）并返回 None
    - 可选变量缺失时打印警告，对应 Header 省略
    - 不设置任何默认值，不猜测任何认证机制
    """
    missing = [name for name in REQUIRED_ENV if not os.environ.get(name)]
    if missing:
        print("Missing required environment variable(s): " + ", ".join(missing))
        print("Set them to values captured from your own logged-in browser session, e.g.:")
        for name in missing:
            print(f'  PowerShell:  $env:{name} = "<value>"')
        return None

    headers: dict[str, str] = {}
    for env_name, header_name in ENV_TO_HEADER.items():
        value = os.environ.get(env_name)
        if value:
            headers[header_name] = value
        elif env_name in OPTIONAL_ENV:
            print(f"WARNING: {env_name} not set -> header '{header_name}' will be omitted.")
    return headers


def _walk_sample_containers(node: Any, found: list[dict]) -> None:
    """递归收集所有带 samplePoints 的容器（只用于输出安全摘要）。"""
    if isinstance(node, dict):
        if isinstance(node.get("samplePoints"), list):
            found.append(node)
        for v in node.values():
            _walk_sample_containers(v, found)
    elif isinstance(node, list):
        for item in node:
            _walk_sample_containers(item, found)


def run_detail_mode(headers: dict[str, str]) -> int:
    """第六阶段 6.1：真实 Activity Detail schema 勘探。"""
    if not RECORDS_INPUT_FILE.exists():
        print(f"Saved real records not found: {RECORDS_INPUT_FILE}")
        print("Run the list mode first:  python scripts/test_real_htc.py")
        return 1

    with open(RECORDS_INPUT_FILE, "r", encoding="utf-8") as f:
        records = json.load(f)
    if not isinstance(records, list) or not records:
        print("Saved real records file is empty or not a list; re-run list mode first.")
        return 1

    # 优先选跑步（activityType=56，真实数据已确认为 int）
    rec = next((r for r in records if r.get("activityType") == 56), records[0])
    # 逐字回传记录自身的时间戳（毫秒，来自 API 本身，不做单位换算）
    start_time, end_time = str(rec["startTime"]), str(rec["endTime"])
    activity_type = str(rec["activityType"])
    print("Selected activity:")
    print(f"  id={rec.get('id')} activityType={activity_type}")
    print(f"  startTime={start_time} endTime={end_time} (verbatim from record)")

    client = HTCClient(headers=headers)
    try:
        raw = client.query_activity_detail(
            start_time=start_time,
            end_time=end_time,
            activity_type=activity_type,
            detail_data_types=DETAIL_DATA_TYPES,
            high_freq_details_preferred=True,
        )
    except HTCClientError as exc:
        print("HTC detail request: FAILED")
        print(f"URL: {ACTIVITY_RECORD_QUERY_URL}")
        print(f"Error: {exc}")
        print()
        print("If this fails, retry with fewer detailDataType entries and record")
        print("which dataType caused the failure. This project will not guess schema.")
        return 1
    finally:
        client.close()

    print("HTC detail request: SUCCESS")
    print("HTTP status: 2xx")

    # 先保存原始 JSON：即使后续分析出错，真实响应也已留档
    DETAIL_OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(DETAIL_OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=2)
    print(f"Raw JSON saved to: {DETAIL_OUTPUT_FILE} (git-ignored)")

    # 安全结构摘要：只输出类型 / key 名 / 计数
    print(f"Top-level type: {type(raw).__name__}")
    if isinstance(raw, dict):
        print(f"Top-level keys: {sorted(raw.keys())}")
        for k, v in raw.items():
            print(f"  {k}: {type(v).__name__}" + (f" (len={len(v)})" if isinstance(v, (list, dict)) else ""))

    containers: list[dict] = []
    _walk_sample_containers(raw, containers)
    print(f"samplePoints containers found: {len(containers)}")
    for c in containers:
        sp = c.get("samplePoints") or []
        print(f"  - dataTypeName={c.get('dataTypeName')} samples={len(sp)}")

    return 0


def run_list_mode(headers: dict[str, str]) -> int:
    """第五阶段逻辑：最近训练列表 + 现有 parser 验证（保持不变）。"""
    # 时间范围：最近 7 天 -> 现在（毫秒级字符串，与 server.py 同一实现）
    start_time, end_time = _default_time_range(LOOKBACK_DAYS)

    client = HTCClient(headers=headers)
    try:
        raw = client.query_activity_records(
            start_time=start_time, end_time=end_time, limit=LIMIT
        )
    except HTCClientError as exc:
        # 失败输出只含状态码 / URL / 简短错误，不含任何认证信息
        print("HTC API request: FAILED")
        print(f"URL: {ACTIVITY_RECORD_QUERY_URL}")
        print(f"Error: {exc}")
        print()
        print("If this is an auth failure (401/403), the required headers must be")
        print("confirmed from a successful browser request before retrying.")
        print("This project will not guess or bypass any authentication.")
        return 1
    finally:
        client.close()

    # 走到这里说明 HTTP 2xx 且响应是合法 JSON
    print("HTC API request: SUCCESS")
    print("HTTP status: 2xx")

    # 先保存原始 JSON：即使后续抽取/解析出错，真实响应也已留档便于排查
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=2)
    print(f"Raw JSON saved to: {OUTPUT_FILE} (git-ignored)")

    records = _extract_activity_list(raw)
    print(f"Records returned: {len(records)}")

    # 只输出 key 名（不输出值），用于安全诊断真实结构
    if records and isinstance(records[0], dict):
        print(f"First record top-level keys: {sorted(records[0].keys())}")

    if not records:
        print("Parser: SKIPPED (no records in response)")
        if isinstance(raw, dict):
            print(f"Response top-level keys: {sorted(raw.keys())}")
        else:
            print(f"Response top-level type: {type(raw).__name__}")
        return 0

    # 用现有 parser 逐条解析真实数据，只输出安全摘要
    parsed_ok = 0
    for item in records:
        try:
            rec = parse_activity_record(item)
            parsed_ok += 1
            key_fields = [
                rec.activity_id,
                rec.start_time,
                rec.end_time,
                rec.distance,
                rec.avg_heart_rate,
                rec.vo2_max,
            ]
            present = sum(1 for v in key_fields if v is not None)
            samples = len(rec.heart_rate_samples)
            print(
                f"  - activity_id={rec.activity_id} "
                f"(key fields present: {present}/6, hr_samples: {samples})"
            )
        except Exception as exc:  # noqa: BLE001 - 验证脚本需要报告任何解析失败
            print(f"  - parse FAILED: {type(exc).__name__}: {exc}")

    print(f"Parser: {'SUCCESS' if parsed_ok == len(records) else 'PARTIAL FAILURE'} "
          f"({parsed_ok}/{len(records)})")
    return 0 if parsed_ok == len(records) else 1


def _fmt_local(dt: datetime) -> str:
    """窗口边界展示格式：UTC 锚点切分的边界转本地时区（只含时间，无位置）。"""
    return dt.astimezone().strftime("%Y-%m-%d %H:%M")


def _fmt_delta(v: float | int | None) -> str:
    """delta 展示格式（Phase 6.13 浮点噪声修复）。

    - None → N/A（沿用 pct 的缺失语义）
    - int 原样输出（计数类 delta 保持整数）
    - float 固定两位小数（消除减法产生的长尾噪声，如
      -0.19999999999998863 → -0.20；带符号风格与 pct 的 +.1f% 一致）
    """
    if v is None:
        return "N/A"
    if isinstance(v, int):
        return str(v)
    return f"{v:+.2f}"


def _print_period_summary(label: str, summary) -> None:
    """输出周期聚合的安全摘要（只含统计数字，无凭证/位置数据）。"""
    print(f"[{label}]")
    print(f"  activity_count={summary.activity_count} "
          f"(distance valid: {summary.distance_activity_count}, "
          f"hr valid: {summary.hr_activity_count})")
    print(f"  total_distance={summary.total_distance} m, "
          f"total_duration={summary.total_duration_seconds:.0f} s, "
          f"total_calories={summary.total_calories} kcal")
    print(f"  total_steps={summary.total_steps}, "
          f"ascent={summary.total_ascent} m / descent={summary.total_descent} m")
    # Phase 6.13：周期活跃时间（毫秒→秒唯一换算点）与周期配速（设备口径）
    active_s = summary.total_active_time_seconds
    print(f"  total_active_time="
          f"{active_s:.0f} s (valid: {summary.active_time_activity_count})"
          if active_s is not None
          else "  total_active_time=N/A (no activeTime data)")
    pace = summary.average_pace_seconds_per_km
    print(f"  average_pace="
          f"{pace:.1f} s/km (valid: {summary.pace_activity_count})"
          if pace is not None
          else "  average_pace=N/A (no distance+activeTime pair)")
    if summary.average_heart_rate is not None:
        print(f"  average_heart_rate={summary.average_heart_rate:.1f} bpm, "
              f"max_heart_rate={summary.max_heart_rate:.0f} bpm")
    for t in summary.activity_types:
        print(f"  activity_type={t.activity_type}: count={t.count} "
              f"distance={t.total_distance} m duration={t.total_duration_seconds:.0f} s")


def run_period_mode(headers: dict[str, str]) -> int:
    """周期模式：真实请求最近 LOOKBACK_DAYS 天 → 周期聚合离线验证。"""
    start_time, end_time = _default_time_range(LOOKBACK_DAYS)
    client = HTCClient(headers=headers)
    try:
        raw = client.query_activity_records(
            start_time=start_time, end_time=end_time, limit=LIMIT
        )
    except HTCClientError as exc:
        print("HTC API request: FAILED")
        print(f"URL: {ACTIVITY_RECORD_QUERY_URL}")
        print(f"Error: {exc}")
        return 1
    finally:
        client.close()

    print("HTC API request: SUCCESS (HTTP 2xx)")
    from src.huawei_health_mcp.parser import parse_activity_record

    records = [parse_activity_record(item) for item in _extract_activity_list(raw)]
    _print_period_summary(f"last {LOOKBACK_DAYS} days", aggregate_activity_period(records))
    return 0


def run_comparison_mode(headers: dict[str, str], argv: list[str]) -> int:
    """对比模式：真实请求两个连续窗口（最近 N 天 vs 之前 N 天）。

    同时验证 Known Issue：历史窗口（endTime 在过去）的真实 API 行为。
    基线为 0 条时无法区分“该周期无训练”与“服务端不支持历史区间”，
    输出中会提示用更大窗口重试确认。
    """
    try:
        window_days = int(argv[2]) if len(argv) > 2 else LOOKBACK_DAYS
    except ValueError:
        print(f"Invalid window_days: {argv[2]!r} (expected an integer)")
        return 1

    client = HTCClient(headers=headers)
    try:
        comparison = fetch_period_comparison(
            client, window_days=window_days, limit=MAX_LIST_LIMIT_FOR_COMPARISON
        )
    except HTCClientError as exc:
        print("HTC API request: FAILED")
        print(f"URL: {ACTIVITY_RECORD_QUERY_URL}")
        print(f"Error: {exc}")
        return 1
    finally:
        client.close()

    print("HTC API request: SUCCESS (both windows, HTTP 2xx)")
    print(f"window_days={window_days} "
          f"(baseline=[now-{2 * window_days}d, now-{window_days}d), "
          f"current=[now-{window_days}d, now))")
    _print_period_summary("baseline", comparison.baseline_summary)
    _print_period_summary("current", comparison.current_summary)

    b = comparison.baseline_summary
    c = comparison.current_summary
    print("[comparison]")
    for name in (
        "activity_count", "total_distance", "total_calories", "total_steps",
        "total_duration_seconds", "average_heart_rate", "max_heart_rate",
    ):
        m = getattr(comparison, name)
        pct = f"{m.percentage_change:+.1f}%" if m.percentage_change is not None else "N/A"
        print(f"  {name}: {m.baseline} -> {m.current} (delta={m.delta}, pct={pct})")

    if b.activity_count == 0:
        print()
        print("NOTE: baseline window returned 0 activities. This cannot distinguish")
        print("'no training in that period' from 'server ignoring historical range'.")
        print(f"Retry with a larger window (e.g. comparison {max(window_days * 2, 14)})")
        print("to check whether older activities appear.")
    if b.activity_count == 0 and c.activity_count == 0:
        print("Both windows empty: also check auth (401/403) would have raised already.")
    return 0


def _parse_weeks(argv: list[str]) -> int | None:
    """解析趋势模式的可选 weeks 参数（缺省 4；非整数打印错误并返回 None）。"""
    if len(argv) <= 2:
        return DEFAULT_TREND_WEEKS
    try:
        return int(argv[2])
    except ValueError:
        print(f"Invalid weeks: {argv[2]!r} (expected an integer)")
        return None


def run_trend_mode(headers: dict[str, str], argv: list[str]) -> int:
    """趋势模式：最近 N 个连续 7 天滚动周窗口的训练量趋势。

    100% 复用 fetch_weekly_trend（单次列表请求整个跨度后本地归窗），
    脚本不重新实现任何窗口切分/聚合逻辑；打印总跨度、每周窗口边界
    （本地时间起止）与完整周期聚合摘要；某周 0 条活动时明确打印
    （真实数据下这本身就是有价值的验收信息）。
    """
    weeks = _parse_weeks(argv)
    if weeks is None:
        return 1

    client = HTCClient(headers=headers)
    try:
        trend = fetch_weekly_trend(
            client, weeks=weeks, limit=MAX_LIST_LIMIT_FOR_TREND
        )
    except ValueError as exc:
        print(f"Invalid weeks: {exc}")
        return 1
    except HTCClientError as exc:
        print("HTC API request: FAILED")
        print(f"URL: {ACTIVITY_RECORD_QUERY_URL}")
        print(f"Error: {exc}")
        return 1
    finally:
        client.close()

    print("HTC API request: SUCCESS (single list request over the whole span, HTTP 2xx)")
    print(f"weeks={weeks} window_days={trend.window_days}")
    print(f"total span: ({_fmt_local(trend.weeks[0].window_start)} .. {_fmt_local(trend.anchor)})")

    for i, week in enumerate(trend.weeks, start=1):
        _print_period_summary(
            f"week {i}/{weeks} ({_fmt_local(week.window_start)} .. {_fmt_local(week.window_end)})",
            week.summary,
        )
        if week.summary.activity_count == 0:
            print("  (no activities fell into this window)")
    return 0


def run_trend_delta_mode(headers: dict[str, str], argv: list[str]) -> int:
    """环比模式：相邻周 older→newer 环比（transitions 按时间升序）。

    100% 复用 fetch_weekly_trend_delta（单次请求 → 一次聚合 → 环比），
    脚本不重新实现任何对比数学；每对打印两窗边界（本地时间起止）与
    7 项总量指标 + HR avg/max 的 baseline→current (delta=..., pct=...)，
    pct 为 None 时打印 N/A；weeks=1 时 transitions 为空，打印说明。
    """
    weeks = _parse_weeks(argv)
    if weeks is None:
        return 1

    client = HTCClient(headers=headers)
    try:
        delta = fetch_weekly_trend_delta(
            client, weeks=weeks, limit=MAX_LIST_LIMIT_FOR_TREND
        )
    except ValueError as exc:
        print(f"Invalid weeks: {exc}")
        return 1
    except HTCClientError as exc:
        print("HTC API request: FAILED")
        print(f"URL: {ACTIVITY_RECORD_QUERY_URL}")
        print(f"Error: {exc}")
        return 1
    finally:
        client.close()

    print("HTC API request: SUCCESS (single list request over the whole span, HTTP 2xx)")
    print(f"weeks={weeks} window_days={delta.window_days} "
          f"transitions={len(delta.transitions)}")
    if not delta.transitions:
        print("weeks=1 -> only one week window, no adjacent pair to compare "
              "(transitions is empty by contract).")
        return 0

    for i, t in enumerate(delta.transitions, start=1):
        print(f"[transition {i}/{len(delta.transitions)}]")
        print(f"  baseline (older): ({_fmt_local(t.baseline_window_start)} .. {_fmt_local(t.baseline_window_end)})")
        print(f"  current  (newer): ({_fmt_local(t.current_window_start)} .. {_fmt_local(t.current_window_end)})")
        for name in (
            "activity_count", "total_distance", "total_duration_seconds",
            "total_calories", "total_steps", "total_ascent", "total_descent",
            "average_heart_rate", "max_heart_rate",
        ):
            m = getattr(t, name)
            pct = f"{m.percentage_change:+.1f}%" if m.percentage_change is not None else "N/A"
            print(f"  {name}: {m.baseline} -> {m.current} "
                  f"(delta={_fmt_delta(m.delta)}, pct={pct})")
    return 0


def _parse_days(argv: list[str], default: int) -> int | None:
    """解析可选的天数参数（恢复状态模式共用；1~730 与 MCP 校验一致）。"""
    if len(argv) <= 2:
        return default
    try:
        days = int(argv[2])
    except ValueError:
        print(f"Invalid days: {argv[2]!r} (expected an integer 1~730)")
        return None
    if not 1 <= days <= 730:
        print(f"days out of range: {days} (expected 1~730)")
        return None
    return days


def run_sleep_mode(headers: dict[str, str], argv: list[str]) -> int:
    """睡眠模式：最近 N 天睡眠记录真实链路验证（默认 14）。

    100% 复用 fetch_sleep_records（GET /healthRecords + parser），
    每晚打印入睡/醒来时刻、分段分钟与得分；对每条记录做两条
    自洽恒等式检查（分段和 = 总睡眠；入睡→醒来跨度 = 总睡眠+清醒）。
    """
    days = _parse_days(argv, default=14)
    if days is None:
        return 1

    client = HTCClient(headers=headers)
    try:
        records = fetch_sleep_records(client, days=days)
    except ValueError as exc:
        print(f"Invalid days: {exc}")
        return 1
    except HTCClientError as exc:
        print("HTC API request: FAILED")
        print(f"URL: {HEALTH_RECORDS_URL}")
        print(f"Error: {exc}")
        return 1
    finally:
        client.close()

    print("HTC API request: SUCCESS (GET /healthRecords, HTTP 2xx)")
    print(f"days={days} records={len(records)}")
    for i, r in enumerate(records, start=1):
        print(f"[night {i}/{len(records)}]")
        print(f"  fall_asleep={_fmt_local(r.fall_asleep_time)} wakeup={_fmt_local(r.wakeup_time)}")
        parts = [
            f"light={r.light_sleep_minutes}", f"deep={r.deep_sleep_minutes}",
            f"dream={r.dream_sleep_minutes}", f"awake={r.awake_minutes}",
            f"total={r.total_sleep_minutes}",
        ]
        print("  " + ", ".join(p + "min" if not p.endswith("None") else p for p in parts))
        print(f"  score={r.sleep_score} efficiency={r.sleep_efficiency_percent}% "
              f"latency={r.sleep_latency_minutes}min wakeups={r.wakeup_count}")
        # 自洽恒等式（真实数据已验证成立；不成立即契约漂移信号）
        seg = [r.light_sleep_minutes, r.deep_sleep_minutes, r.dream_sleep_minutes,
               r.total_sleep_minutes]
        if all(v is not None for v in seg):
            ok = seg[0] + seg[1] + seg[2] == seg[3]
            print(f"  self-check stages_sum==total: {'OK' if ok else 'MISMATCH!'}")
        if None not in (r.fall_asleep_time, r.wakeup_time, r.total_sleep_minutes,
                        r.awake_minutes):
            span = (r.wakeup_time - r.fall_asleep_time).total_seconds() / 60
            ok2 = abs(span - (r.total_sleep_minutes + r.awake_minutes)) <= 1
            print(f"  self-check span==total+awake: {'OK' if ok2 else 'MISMATCH!'}")
    if not records:
        print("(no sleep records in window — check device sync / wear history)")
    return 0


def _run_stats_mode(
    label: str,
    headers: dict[str, str],
    fetch_fn,
    url: str,
    argv: list[str],
    default_days: int,
) -> int:
    """静息心率 / HRV 统计模式共用 runner（服务端聚合，本地不计算）。"""
    days = _parse_days(argv, default=default_days)
    if days is None:
        return 1

    client = HTCClient(headers=headers)
    try:
        stats = fetch_fn(client, days=days)
    except ValueError as exc:
        print(f"Invalid days: {exc}")
        return 1
    except HTCClientError as exc:
        print("HTC API request: FAILED")
        print(f"URL: {url}")
        print(f"Error: {exc}")
        return 1
    finally:
        client.close()

    print(f"HTC API request: SUCCESS ({label}, HTTP 2xx)")
    print(f"days={days} window=[{stats.start_day} .. {stats.end_day}] "
          f"field_name={stats.field_name}")
    o = stats.overall
    print(f"[overall] avg={o.avg} max={o.max} min={o.min} count={o.count}")
    print(f"[daily] entries={len(stats.daily)}")
    for d in stats.daily:
        s = d.stats
        extras = "".join(
            f" {k}={v}" for k, v in (("max", s.max), ("min", s.min), ("count", s.count))
            if v is not None
        )
        print(f"  {d.day}: avg={s.avg}{extras}")
    if not stats.daily and o.avg is None:
        print("(no data in window — metric may be missing for this account)")
    return 0


def run_resting_hr_mode(headers: dict[str, str], argv: list[str]) -> int:
    """静息心率模式：最近 N 天 restBpm 统计（默认 28，sampleSet 端点）。"""
    return _run_stats_mode(
        "sampleSet periodStatistics", headers, fetch_resting_heart_rate,
        SAMPLE_SET_STATS_URL, argv, default_days=28,
    )


def run_hrv_mode(headers: dict[str, str], argv: list[str]) -> int:
    """HRV 模式：最近 N 天 avgHrv 统计（默认 28，healthRecords 端点）。"""
    return _run_stats_mode(
        "healthRecords periodStatistics", headers, fetch_hrv_stats,
        HEALTH_RECORD_STATS_URL, argv, default_days=28,
    )


def run_performance_mode(headers: dict[str, str]) -> int:
    """能力模式：最新运动能力评估真实链路验证（Phase 8）。

    100% 复用 fetch_athletic_performance（GET athleticPerformance/latest
    + parser），打印五项指数与全部预测成绩（服务端口径，本地不换算）。
    """
    client = HTCClient(headers=headers)
    try:
        perf = fetch_athletic_performance(client)
    except HTCClientError as exc:
        print("HTC API request: FAILED")
        print(f"URL: {ATHLETIC_PERFORMANCE_URL}")
        print(f"Error: {exc}")
        return 1
    finally:
        client.close()

    print("HTC API request: SUCCESS (athleticPerformance/latest, HTTP 2xx)")
    print(f"running_ability={perf.running_ability} condition={perf.condition} "
          f"fitness={perf.fitness} fatigue={perf.fatigue} ranking={perf.ranking}")
    print(f"predicted_times entries={len(perf.predicted_times)}")
    for name, seconds in sorted(perf.predicted_times.items()):
        print(f"  {name}: {seconds:.0f}s ({seconds / 60:.1f}min)")
    if perf.running_ability is None and not perf.predicted_times:
        print("(no data in response — metric may be missing for this account)")
    return 0


def run_pb_mode(headers: dict[str, str], argv: list[str]) -> int:
    """PB 模式：单运动类型个人纪录真实链路验证（Phase 8，默认 running）。

    100% 复用 fetch_personal_bests（GET sportReports + parser），
    逐条打印 name/value 与达成时间段（本地时间）；value 单位由
    华为定义（时间为秒、距离为米），脚本不换算不命名。
    """
    activity_type = argv[2] if len(argv) > 2 else "running"
    if not activity_type.strip():
        print("Invalid activity_type: empty string")
        return 1

    client = HTCClient(headers=headers)
    try:
        bests = fetch_personal_bests(client, activity_type=activity_type)
    except HTCClientError as exc:
        print("HTC API request: FAILED")
        print(f"URL: {SPORT_REPORTS_URL}")
        print(f"Error: {exc}")
        return 1
    finally:
        client.close()

    print("HTC API request: SUCCESS (sportReports, HTTP 2xx)")
    print(f"activity_type={bests.activity_type} personal_bests={len(bests.personal_bests)}")
    for b in bests.personal_bests:
        start = _fmt_local(b.start_time) if b.start_time else "N/A"
        end = _fmt_local(b.end_time) if b.end_time else "N/A"
        print(f"  {b.name}: value={b.value} achieved=[{start} .. {end}]")
    if not bests.personal_bests:
        print(f"(no personalBest entries for activityType={activity_type!r} "
              "— try another type or check account data)")
    return 0


def main() -> int:
    headers = build_headers()
    if headers is None:
        return 1

    argv = sys.argv
    mode = argv[1] if len(argv) > 1 else "list"
    if mode == "detail":
        return run_detail_mode(headers)
    if mode == "period":
        return run_period_mode(headers)
    if mode == "comparison":
        return run_comparison_mode(headers, argv)
    if mode == "trend":
        return run_trend_mode(headers, argv)
    if mode == "trend-delta":
        return run_trend_delta_mode(headers, argv)
    if mode == "sleep":
        return run_sleep_mode(headers, argv)
    if mode == "resting-hr":
        return run_resting_hr_mode(headers, argv)
    if mode == "hrv":
        return run_hrv_mode(headers, argv)
    if mode == "performance":
        return run_performance_mode(headers)
    if mode == "pb":
        return run_pb_mode(headers, argv)
    return run_list_mode(headers)


if __name__ == "__main__":
    raise SystemExit(main())