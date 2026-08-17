"""Huawei Training Camp MCP —— 服务入口与 MCP 工具。

本文件负责：
- 创建 FastMCP 服务
- 注册 MCP 工具（health_check、get_recent_activities、get_activity_detail、get_training_summary）
- 把 MCP 工具接到现有的 HTCClient（数据获取层）与 parser（解析层）

设计原则：
- 复用现有 client.py / parser.py，不重复实现 HTTP 或解析逻辑。
- 认证信息（Authorization / x-client-id 等）只从环境变量读取，绝不写进代码 / 日志 / 测试。
- 不实现登录、不猜测认证机制、不绕过任何访问控制。
- 不连接数据库、不做 AI 分析、不做 Dashboard。
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

from fastmcp import FastMCP

from .analysis import compute_session_metrics, summarize_activity_detail
from .period_analysis import aggregate_activity_period
from .period_comparison import compare_training_periods
from .trend_analysis import aggregate_weekly_trend, compute_weekly_trend_delta
from .client import (
    RESTING_HR_DATA_TYPE,
    SLEEP_RECORD_DATA_TYPE,
    HTCClient,
    HTCClientError,
)
from .health_parser import parse_health_metric_stats, parse_sleep_records
from .performance_parser import parse_athletic_performance, parse_personal_bests
from .models import (
    ActivityDetail,
    ActivityRecord,
    AthleticPerformance,
    HealthMetricStats,
    SleepRecord,
    SportPersonalBests,
    TrainingPeriodComparison,
    TrainingSummary,
    TrainingTrendDelta,
    TrainingTrendSummary,
)
from .parser import parse_activity_detail, parse_activity_record

# 创建一个 MCP 服务实例。
# 这个名字会展示给调用方（AI 客户端），方便识别这是哪个服务。
mcp = FastMCP("Huawei Training Camp MCP")

# 默认查询最近多少天的训练记录
DEFAULT_LOOKBACK_DAYS = 30

# lookback_days 允许的最大值（约两年）：防止过大窗口拖慢查询或触发服务端限制
MAX_LOOKBACK_DAYS = 730

# limit 允许的最大值：单次列表查询的防御性上限（MCP 入口校验；
# 内部 DETAIL_LOOKUP_LIMIT 定位调用不受影响）
MAX_LIST_LIMIT = 100

# 周期对比单个窗口的最大天数（Phase 6.9）：两个连续窗口的总跨度
# （2 * window_days）不得超过 MAX_LOOKBACK_DAYS 上限
MAX_COMPARISON_WINDOW_DAYS = MAX_LOOKBACK_DAYS // 2  # 365

# 周趋势最大窗口数（Phase 6.10）：weeks * 7 天总跨度不得超过
# MAX_LOOKBACK_DAYS 上限（104 * 7 = 728 <= 730）
MAX_TREND_WEEKS = MAX_LOOKBACK_DAYS // 7  # 104

# 查询活动详情时，在最近训练列表里定位目标活动最多扫描多少条记录
DETAIL_LOOKUP_LIMIT = 50

# 详情请求默认携带的高频数据类型（Phase 6.1 浏览器成功请求确认的 8 个核心类型；
# 真实 API 已证明请求的类型不保证全部返回，缺失即对应 collector 不出现）
DEFAULT_DETAIL_DATA_TYPES = [
    "com.huawei.instantaneous.exercise_heart_rate",
    "com.huawei.recovery_heart_rate",
    "com.huawei.instantaneous.speed",
    "com.huawei.instantaneous.steps.rate",
    "com.huawei.continuous.run.posture",
    "com.huawei.instantaneous.altitude",
    "com.huawei.instantaneous.location.sample",
    "com.huawei.analog_power",
]


# 环境变量 -> HTTP 请求头 映射（与 scripts/test_real_htc.py 同一契约，
# 键名来自用户确认的浏览器成功请求）
_ENV_TO_HEADER = {
    "HTC_AUTHORIZATION": "Authorization",
    "HTC_CLIENT_ID": "x-client-id",
    "HTC_VERSION": "x-version",
    "HTC_COOKIE": "Cookie",
}

# 缺失即拒绝构造 headers 的必填变量（Phase 6.14 修复：此前只映射了
# HTC_COOKIE，导致 MCP 服务发出的请求不带 Authorization，真实调用
# 恒 401 "Invalid Credentials"，且误导排查方向）
_REQUIRED_ENV = ("HTC_AUTHORIZATION", "HTC_CLIENT_ID")


def build_htc_headers() -> dict[str, str]:
    """从环境变量构造请求 headers。

    HTC_AUTHORIZATION / HTC_CLIENT_ID 必填：缺失时抛 ValueError 并
    指明缺失变量名（清晰本地报错，优于发出注定 401 的请求）；
    HTC_VERSION / HTC_COOKIE 可选：缺失时对应 Header 省略。
    不猜测任何认证机制，不写入任何真实认证信息。
    """
    missing = [name for name in _REQUIRED_ENV if not os.environ.get(name)]
    if missing:
        raise ValueError(
            "Missing required environment variable(s): " + ", ".join(missing)
            + ". Set them from your own logged-in browser session"
            + " (see README '环境变量' section)."
        )
    headers: dict[str, str] = {}
    for env_name, header_name in _ENV_TO_HEADER.items():
        value = os.environ.get(env_name)
        if value:
            headers[header_name] = value
    return headers


def create_htc_client(timeout: float = 30.0) -> HTCClient:
    """根据本地配置（环境变量）创建一个 HTCClient。

    测试时会被替换为返回 mock 客户端的版本，从而不访问真实网络。
    """
    return HTCClient(headers=build_htc_headers(), timeout=timeout)


def _validate_lookback_days(lookback_days: int) -> int:
    """校验查询窗口天数（Phase 6.4）。

    允许 1 ~ MAX_LOOKBACK_DAYS：过小无意义，过大可能拖慢查询或触发
    服务端限制。非法值抛 ValueError，调用方（MCP 客户端）直接看到
    可理解的错误信息。
    """
    if not 1 <= lookback_days <= MAX_LOOKBACK_DAYS:
        raise ValueError(
            f"lookback_days 必须在 1~{MAX_LOOKBACK_DAYS} 之间，当前为 {lookback_days}"
        )
    return lookback_days

def _validate_limit(limit: int) -> int:
    """校验列表查询条数（Phase 6.5）。

    允许 1 ~ MAX_LIST_LIMIT：0 / 负数无意义，过大可能拖慢查询或触发
    服务端限制。非法值抛 ValueError，调用方直接看到可理解的错误。
    """
    if not 1 <= limit <= MAX_LIST_LIMIT:
        raise ValueError(
            f"limit 必须在 1~{MAX_LIST_LIMIT} 之间，当前为 {limit}"
        )
    return limit

def _validate_window_days(window_days: int) -> int:
    """校验周期对比的单窗口天数（Phase 6.9）。

    1 ~ MAX_COMPARISON_WINDOW_DAYS（365）：两个连续窗口总跨度
    （2 x window_days）不得超过 MAX_LOOKBACK_DAYS 上限。
    """
    if not 1 <= window_days <= MAX_COMPARISON_WINDOW_DAYS:
        raise ValueError(
            f"window_days 必须在 1~{MAX_COMPARISON_WINDOW_DAYS} 之间，"
            f"当前为 {window_days}"
        )
    return window_days

def _validate_weeks(weeks: int) -> int:
    """校验周趋势窗口数（Phase 6.10）。

    1 ~ MAX_TREND_WEEKS（104）：weeks * 7 天总跨度不得超过
    MAX_LOOKBACK_DAYS（730）上限。
    """
    if not 1 <= weeks <= MAX_TREND_WEEKS:
        raise ValueError(
            f"weeks 必须在 1~{MAX_TREND_WEEKS} 之间，当前为 {weeks}"
        )
    return weeks

def _default_time_range(lookback_days: int = DEFAULT_LOOKBACK_DAYS) -> tuple[str, str]:
    """返回 (start_time, end_time) 毫秒级字符串时间戳。

    HTC 列表接口需要时间范围；默认取“最近 N 天”到“现在”。
    """
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=lookback_days)
    return (str(int(start.timestamp() * 1000)), str(int(now.timestamp() * 1000)))


def _window_bounds(
    window_days: int, offset_days: int
) -> tuple[datetime, datetime]:
    """返回以“现在”为锚的窗口边界：[now - offset - N, now - offset)。

    offset_days=0 → 当前窗口 [now-N, now)；
    offset_days=window_days → 基线窗口 [now-2N, now-N)。半开区间，
    相邻窗口共享边界值但不重叠。
    """
    end = datetime.now(timezone.utc) - timedelta(days=offset_days)
    start = end - timedelta(days=window_days)
    return start, end


def _to_ms(dt: datetime) -> str:
    """datetime → HTC 约定的毫秒级字符串时间戳。"""
    return str(int(dt.timestamp() * 1000))


def fetch_period_comparison(
    client: HTCClient,
    window_days: int = 7,
    limit: int = 100,
) -> TrainingPeriodComparison:
    """取两个连续窗口并对比：基线 [now-2N, now-N) / 当前 [now-N, now)。

    窗口语义由本层保证：对返回记录按其自身 start_time 做半开区间
    [start, end) 过滤——历史窗口（endTime 在过去）的真实 API 行为
    尚未用真实凭证验证，过滤确保即使服务端对过去区间行为宽松，
    也不会把当前期记录混入基线（此时过滤为无操作）。
    start_time 缺失的记录无法归入任何时间窗口，从两期都排除。
    两次请求共用同一 now 锚点，窗口边界精确连续。
    """
    _validate_window_days(window_days)
    _validate_limit(limit)

    now = datetime.now(timezone.utc)
    current_start = now - timedelta(days=window_days)
    windows = [
        (current_start - timedelta(days=window_days), current_start),  # baseline
        (current_start, now),                                          # current
    ]

    summaries = []
    for start_dt, end_dt in windows:
        raw = client.query_activity_records(
            start_time=_to_ms(start_dt), end_time=_to_ms(end_dt), limit=limit
        )
        records = [
            parse_activity_record(item) for item in _extract_activity_list(raw)
        ]
        windowed = [
            r for r in records
            if r.start_time is not None and start_dt <= r.start_time < end_dt
        ]
        summaries.append(aggregate_activity_period(windowed))

    return compare_training_periods(summaries[0], summaries[1])


def _extract_activity_list(raw: Any) -> list[dict]:
    """从 activityRecord:query 的列表响应里稳健地取出活动数组。

    2026-08 真实请求已确认：顶层结构就是 list（此前 dict 假设导致
    AttributeError）。为兼容 Mock 测试与可能的包裹写法，同时支持：
    - 顶层是 list：直接作为活动数组返回
    - 顶层是 dict：尝试 activityRecords/records/activityRecordList/list 包裹字段
    拿不到就返回空列表（不会报错）。
    """
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        for key in ("activityRecords", "records", "activityRecordList", "list"):
            value = raw.get(key)
            if isinstance(value, list):
                return value
    return []


def fetch_recent_activities(
    client: HTCClient,
    limit: int = 5,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> list[ActivityRecord]:
    """核心逻辑：查询最近训练列表并解析为标准化 ActivityRecord。

    不依赖 MCP，便于直接测试。返回 ActivityRecord 列表。
    """
    _validate_lookback_days(lookback_days)
    start_time, end_time = _default_time_range(lookback_days)
    raw = client.query_activity_records(
        start_time=start_time, end_time=end_time, limit=limit
    )
    records = _extract_activity_list(raw)
    return [parse_activity_record(item) for item in records]


def _find_activity_record(records: list[dict], activity_id: str) -> dict | None:
    """在活动列表里按 activity_id 定位原始记录 dict。

    真实列表记录的键名是 id（部分旧写法是 activityId），两者都兼容。
    """
    for item in records:
        if isinstance(item, dict) and (item.get("activityId") or item.get("id")) == activity_id:
            return item
    return None


def fetch_session_data(
    client: HTCClient,
    activity_id: str,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    detail_data_types: list[str] | None = None,
) -> tuple[ActivityRecord, ActivityDetail]:
    """核心逻辑：一次定位，同时返回 ActivityRecord（含设备权威统计）与 ActivityDetail。

    真实 HTC detail API 不支持按 id 直接查询（Phase 6.1 确认的请求契约是
    startTime/endTime/activityType），因此分两步：
    1. 在最近训练列表里定位该活动，逐字取其毫秒 startTime/endTime 与 activityType
    2. 用这些值调用 query_activity_detail，再交给 parse_activity_detail

    找不到活动抛 ValueError（提示先调用 get_recent_activities）；
    HTTP / 认证错误由 HTCClient 异常族向上传播。
    """
    if not activity_id:
        raise ValueError("activity_id 不能为空")
    _validate_lookback_days(lookback_days)

    start_time, end_time = _default_time_range(lookback_days)
    raw_records = client.query_activity_records(
        start_time=start_time, end_time=end_time, limit=DETAIL_LOOKUP_LIMIT
    )
    record = _find_activity_record(_extract_activity_list(raw_records), activity_id)
    if record is None:
        raise ValueError(
            f"最近 {lookback_days} 天的训练记录里找不到 activity_id={activity_id}"
            "；可先调用 get_recent_activities 确认该 id 是否存在"
        )

    start_ms = record.get("startTime")
    end_ms = record.get("endTime")
    activity_type = record.get("activityType")
    if start_ms is None or end_ms is None or activity_type is None:
        raise ValueError(
            f"activity_id={activity_id} 的记录缺少 startTime/endTime/activityType，"
            "无法构造详情查询"
        )

    raw_detail = client.query_activity_detail(
        start_time=str(start_ms),  # 逐字回传记录自身的毫秒时间戳，不做换算
        end_time=str(end_ms),
        activity_type=str(activity_type),
        detail_data_types=list(detail_data_types or DEFAULT_DETAIL_DATA_TYPES),
        high_freq_details_preferred=True,
    )
    # record 同时解析：activitySummary 里的设备权威统计
    # （心率/距离/配速/VO2max 等，Phase 6.6 起可从真实数据解析）
    return parse_activity_record(record), parse_activity_detail(raw_detail)


def fetch_activity_detail(
    client: HTCClient,
    activity_id: str,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    detail_data_types: list[str] | None = None,
) -> ActivityDetail:
    """按 activity_id 查询单次活动详情（fetch_session_data 的薄包装）。

    兼容既有调用方：只需 ActivityDetail 时使用本函数；
    同时需要设备权威统计（ActivityRecord）时用 fetch_session_data。
    """
    return fetch_session_data(
        client, activity_id, lookback_days=lookback_days,
        detail_data_types=detail_data_types,
    )[1]

def fetch_weekly_trend(
    client: HTCClient,
    weeks: int = 4,
    limit: int = 100,
) -> TrainingTrendSummary:
    """取最近 N 个连续 7 天周窗口的趋势：单次列表请求整个跨度后本地归窗。

    - 一次请求 [now - weeks*7d, now)，同一 now 同时用于请求时间范围
      与窗口切分（零边界漂移）；归窗过滤保证窗口语义即使服务端
      对范围行为宽松也不受影响
    - 每窗口复用 aggregate_activity_period（完整周期聚合契约继承）
    - start_time 缺失或落在跨度之外的记录被排除
    """
    _validate_weeks(weeks)
    _validate_limit(limit)

    now = datetime.now(timezone.utc)
    span_start = now - timedelta(days=weeks * 7)
    raw = client.query_activity_records(
        start_time=_to_ms(span_start), end_time=_to_ms(now), limit=limit
    )
    records = [
        parse_activity_record(item) for item in _extract_activity_list(raw)
    ]
    return aggregate_weekly_trend(records, weeks=weeks, now=now)


def fetch_weekly_trend_delta(
    client: HTCClient,
    weeks: int = 4,
    limit: int = 100,
) -> TrainingTrendDelta:
    """取最近 N 个连续周窗口并计算相邻周环比。

    单次列表请求整个跨度 → aggregate_weekly_trend（一次聚合）→
    compute_weekly_trend_delta（复用 Phase 6.9 compare_metric）。
    无重复请求、无重复聚合；weeks=1 时 transitions=[]。
    """
    trend = fetch_weekly_trend(client, weeks=weeks, limit=limit)
    return compute_weekly_trend_delta(trend)


# 恢复状态统计的 HRV 字段名（浏览器勘探证据：dataType=sleep 记录
# 上按 fieldNames=["avgHrv"] 查询，服务端按同名 fieldName 返回）
HRV_FIELD_NAME = "avgHrv"

# 恢复状态工具默认查询天数
DEFAULT_SLEEP_DAYS = 14
DEFAULT_RECOVERY_DAYS = 28


def _local_midnight() -> datetime:
    """本地时区今天的 00:00（统计端点的自然日语义由服务端按账号
    时区解释；这里只负责生成与浏览器一致的本地日窗口）。"""
    return datetime.now().astimezone().replace(
        hour=0, minute=0, second=0, microsecond=0
    )


def _local_tz_offset() -> str:
    """本地时区 → "+HHMM" 格式（浏览器请求 timeZone 参数的证据格式）。"""
    return datetime.now().astimezone().strftime("%z")


def _inclusive_day_window(days: int) -> tuple[str, str, str, str]:
    """返回包含今天的 N 个自然日窗口（两端含）。

    返回 (start_ymd, end_ymd, start_iso, end_iso)。证据契约：
    startDay=20260721..endDay=20260817 的 28 天窗口 count=28，
    即两端均为闭区间。
    """
    today = _local_midnight()
    start = today - timedelta(days=days - 1)
    return (
        start.strftime("%Y%m%d"),
        today.strftime("%Y%m%d"),
        start.strftime("%Y-%m-%d"),
        today.strftime("%Y-%m-%d"),
    )


def fetch_sleep_records(client: HTCClient, days: int = DEFAULT_SLEEP_DAYS) -> list[SleepRecord]:
    """查询最近 N 天睡眠记录（Phase 7，GET /healthRecords）。

    查询窗口为尾随窗口 [now - N天, now]（本地时区，纳秒时间戳）。
    证据依据：浏览器 1 天窗口 [capture-24h, capture]（边界为
    capture 时刻本身），不与任何时区的自然日对齐——尾随窗口对
    自然日对齐语义同样稳健（同长度的自然日窗口必被包含）。
    服务端按何种规则纳入记录无公开契约，本层不本地过滤、
    原样返回解析结果（按记录 startTime 升序）。
    """
    _validate_lookback_days(days)
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=days)
    raw = client.query_sleep_records(
        start_time_ns=int(start.timestamp()) * 1_000_000_000,
        end_time_ns=int(now.timestamp()) * 1_000_000_000,
    )
    return parse_sleep_records(raw)


def fetch_resting_heart_rate(
    client: HTCClient, days: int = DEFAULT_RECOVERY_DAYS
) -> HealthMetricStats:
    """查询最近 N 天静息心率统计（Phase 7，sampleSet stats 端点）。

    一次 POST 同时携带 reqs（全窗口汇总）与 groupReqs（按天分组）；
    startDay/endDay 为数字写法（端点契约）。fieldName 由服务端推断
    （真实响应为 restBpm）。全部统计为服务端口径，本地不换算。
    """
    _validate_lookback_days(days)
    start_ymd, end_ymd, start_iso, end_iso = _inclusive_day_window(days)
    raw = client.query_sample_set_stats(
        start_day=int(start_ymd), end_day=int(end_ymd),
        timezone=_local_tz_offset(),
    )
    return parse_health_metric_stats(
        raw,
        data_type=RESTING_HR_DATA_TYPE,
        days=days,
        start_day=start_iso,
        end_day=end_iso,
        field_name=None,  # 服务端推断（真实响应 fieldName=restBpm）
    )


def fetch_hrv_stats(
    client: HTCClient, days: int = DEFAULT_RECOVERY_DAYS
) -> HealthMetricStats:
    """查询最近 N 天 HRV 统计（Phase 7，healthRecords stats 端点）。

    dataType 为睡眠记录、fieldNames=["avgHrv"]、startDay/endDay 为
    字符串写法（端点契约，与 sampleSet 的数字写法区分）。
    avgHrv 数值原样返回（华为未声明单位，不擅自换算）。
    """
    _validate_lookback_days(days)
    start_ymd, end_ymd, start_iso, end_iso = _inclusive_day_window(days)
    raw = client.query_health_record_stats(
        field_names=[HRV_FIELD_NAME], start_day=start_ymd, end_day=end_ymd,
        timezone=_local_tz_offset(),
    )
    return parse_health_metric_stats(
        raw,
        data_type=SLEEP_RECORD_DATA_TYPE,
        days=days,
        start_day=start_iso,
        end_day=end_iso,
        field_name=HRV_FIELD_NAME,
    )


@mcp.tool()
def health_check() -> dict:
    """健康检查：确认 MCP 服务是否正常运行。

    返回一个包含状态与版本信息的字典，方便调用方在接入时先做连通性验证。
    """
    return {
        "status": "ok",
        "service": "huawei-training-camp-mcp",
        "version": "0.1.0",
    }


@mcp.tool()
def get_recent_activities(
    limit: int = 5, lookback_days: int = DEFAULT_LOOKBACK_DAYS
) -> list[dict]:
    """获取用户最近的训练记录列表。

    通过 HTC activityRecord:query 接口查询最近若干次运动，
    并把每条原始记录解析成结构化的训练摘要后返回。

    参数：
        limit: 最多返回多少条训练记录（默认 5）。
        lookback_days: 查询窗口天数（默认 30，范围 1~730）。
            要查更久以前的训练时主动调大，例如 90 或 365。

    返回：
        训练记录列表，每条包含 activity_id、起止时间、类型、距离、
        平均/最大/最小心率、配速、VO2max、恢复时间、训练负荷等字段。
        缺失字段为 None 或空列表。

    说明：
        认证所需的请求头（Authorization / x-client-id 等）从环境变量
        读取（见 build_htc_headers）；本项目不实现登录，
        也不猜测或绕过任何认证机制。
    """
    _validate_limit(limit)
    client = create_htc_client()
    try:
        records = fetch_recent_activities(
            client, limit=limit, lookback_days=lookback_days
        )
    finally:
        # 无论成功还是报错，都确保底层 httpx 客户端被关闭
        client.close()
    # 转成 JSON 友好的 dict，避免 datetime 等类型在 MCP 传输时出问题
    return [r.model_dump(mode="json") for r in records]


@mcp.tool()
def get_activity_detail(
    activity_id: str, lookback_days: int = DEFAULT_LOOKBACK_DAYS
) -> dict:
    """获取单次训练的高频采样详情（心率/速度/步频/海拔/位置/跑姿等）。

    通过 activity_id 定位一次训练：先在最近 lookback_days 天训练列表里
    找到该活动，再用其起止时间向 HTC 查询高频采样数据并解析成标准化结构。

    参数：
        activity_id: 训练记录 id，来自 get_recent_activities 返回的
            activity_id 字段。
        lookback_days: 定位活动时的查询窗口天数（默认 30，范围 1~730）。
            目标训练早于 30 天时需要调大（例如 90 或 365）。

    返回：
        包含 activity_id、activity_type、起止时间、active_time、time_zone
        与 details 数组的对象；details[i] 对应一个数据采集器
        （data_collector_id 含数据类型与设备信息），其 sample_points[j]
        是采样点：timestamp + data_type_name + values（字段名到数值的映射，
        例如心率 {"bpm": 142.0}，位置是多字段
        latitude/longitude/precision/altitude/coordinate）。
        请求的数据类型不保证全部返回：details 或某个采集器的
        sample_points 可能为空，属正常情况。

    错误：
        找不到该 activity_id 时抛 ValueError；认证失效或 HTTP 错误
        以 HTCClientError 族异常清晰报告。

    说明：
        认证信息从环境变量读取；本项目不实现登录，
        也不猜测或绕过任何认证机制。
    """
    client = create_htc_client()
    try:
        detail = fetch_activity_detail(
            client, activity_id=activity_id, lookback_days=lookback_days
        )
    finally:
        # 无论成功还是报错，都确保底层 httpx 客户端被关闭
        client.close()
    # 转成 JSON 友好的 dict，与 get_recent_activities 的序列化风格一致
    return detail.model_dump(mode="json")

@mcp.tool()
def get_training_summary(
    activity_id: str, lookback_days: int = DEFAULT_LOOKBACK_DAYS
) -> dict:
    """获取单次训练的统计摘要（确定性聚合，无 AI 推断）。

    按 activity_id 定位训练并获取高频采样后，对每个数据采集器输出：
    样本数、时间范围，以及每个数值字段（如心率 bpm、速度 speed、
    位置 latitude/longitude 等）的最小值 / 最大值 / 平均值 /
    首值 / 末值。适合快速了解一次训练的强度与数据覆盖情况，
    无需拉取全部原始采样点。

    参数：
        activity_id: 训练记录 id，来自 get_recent_activities。
        lookback_days: 定位活动时的查询窗口天数（默认 30，范围 1~730）。
            目标训练早于 30 天时需要调大（例如 90 或 365）。

    返回：
        TrainingSummary 结构：活动元信息 + collectors[] 统计数组。
        某个采集器无数据时其 fields 为空数组，属正常情况。

    错误：
        找不到该 activity_id 时抛 ValueError；认证失效或 HTTP 错误
        以 HTCClientError 族异常清晰报告。

    说明：
        认证信息从环境变量读取；本项目不实现登录，
        也不猜测或绕过任何认证机制。
    """
    client = create_htc_client()
    try:
        detail = fetch_activity_detail(
            client, activity_id=activity_id, lookback_days=lookback_days
        )
    finally:
        # 无论成功还是报错，都确保底层 httpx 客户端被关闭
        client.close()
    summary: TrainingSummary = summarize_activity_detail(detail)
    return summary.model_dump(mode="json")

@mcp.tool()
def get_session_metrics(
    activity_id: str, lookback_days: int = DEFAULT_LOOKBACK_DAYS
) -> dict:
    """获取单次训练的核心指标（纯数学聚合，无业务标准）。

    返回时长、平均/最大/最小心率、平均/最大速度、平均/最佳配速、
    距离等会话级指标。每个指标带 source 标记（device_summary=设备
    权威统计 / samples=高频采样聚合），便于判断数据可信度。

    参数：
        activity_id: 训练记录 id，来自 get_recent_activities。
        lookback_days: 定位活动时的查询窗口天数（默认 30，范围 1~730）。

    说明：
        - 不包含心率区间/强度分区等业务分析（标准待定）
        - active_time_raw 为原始 activeTime 值，单位存疑（见项目文档）

    错误：
        找不到该 activity_id 时抛 ValueError；认证/HTTP 错误以
        HTCClientError 族异常清晰报告。
    """
    client = create_htc_client()
    try:
        record, detail = fetch_session_data(
            client, activity_id=activity_id, lookback_days=lookback_days
        )
    finally:
        client.close()
    metrics = compute_session_metrics(detail, record)
    return metrics.model_dump(mode="json")

@mcp.tool()
def get_training_period_summary(
    lookback_days: int = 7, limit: int = 100
) -> dict:
    """获取一段周期内多次训练的聚合统计（纯数学，无业务标准）。

    统计 lookback_days 天内最多 limit 条训练：活动次数、总距离、
    总历时、平均/最大单次距离与时长、平均/最大心率（带有效样本数
    标记），以及按 activityType 原样分组的数量/距离/历时统计
    （类型值原样保留，如 56/90，不自行命名运动名）。

    参数：
        lookback_days: 周期窗口天数（默认 7，范围 1~730）。
        limit: 最多聚合多少条训练（默认 100，范围 1~100）。

    语义说明：
        - duration 为每条记录起止时间差的总历时（含间歇），非纯运动
          时间（activeTime 单位存疑，见项目文档）
        - 距离只取设备权威统计，缺失活动不计入并由
          distance_activity_count 标记有效数
        - 不提供周期平均配速（纯运动总时长缺失，语义无法保证）

    错误：
        非法 lookback_days / limit 抛 ValueError；认证/HTTP 错误以
        HTCClientError 族异常清晰报告。
    """
    _validate_limit(limit)
    _validate_lookback_days(lookback_days)
    client = create_htc_client()
    try:
        records = fetch_recent_activities(
            client, limit=limit, lookback_days=lookback_days
        )
    finally:
        client.close()
    summary = aggregate_activity_period(records)
    return summary.model_dump(mode="json")

@mcp.tool()
def get_training_period_comparison(window_days: int = 7, limit: int = 100) -> dict:
    """对比两个连续训练周期：最近 N 天 vs 之前 N 天（纯数学，无业务标准）。

    例如 window_days=7 表示“本周 vs 上周”：当前窗口 [now-7d, now)、
    基线窗口 [now-14d, now-7d)，两窗相邻不重叠。每个对比指标输出
    baseline / current / delta / percentage_change。

    参数：
        window_days: 单个周期窗口天数（默认 7，范围 1~365；
            两窗口总跨度不得超 730 天上限）。
        limit: 每个窗口最多聚合多少条训练（默认 100，范围 1~100）。

    返回：
        baseline_summary / current_summary（完整周期聚合，含各类
        *_activity_count 与 activity_types 分组）+ 各指标的
        MetricComparison（活动次数 / 总距离 / 总卡路里 / 爬升 /
        下降 / 步数 / 总历时 / 平均与最大心率），以及两个查询窗口
        的起止时间。

    语义说明：
        - delta = current - baseline；任一侧缺失(None) → delta=None
          （缺失≠0，不伪造基线）
        - percentage_change 在 baseline 缺失或为 0 时为 None（不除零）
        - duration 为每条记录起止时间差的总历时（含间歇），继承
          周期聚合契约
        - 不提供周期配速对比（纯运动总时长缺失，语义无法保证）
        - 心率只对比设备 summary 聚合值，hr_activity_count 随
          内嵌 summary 保真

    错误：
        非法 window_days / limit 抛 ValueError；认证/HTTP 错误以
        HTCClientError 族异常清晰报告。
    """
    _validate_window_days(window_days)
    _validate_limit(limit)
    client = create_htc_client()
    try:
        comparison = fetch_period_comparison(
            client, window_days=window_days, limit=limit
        )
    finally:
        client.close()

    result = comparison.model_dump(mode="json")
    # 查询窗口仅作展示参考（与 fetch 共用同一锚点规则；展示值与
    # 实际请求可能存在毫秒级时钟差，不影响数据过滤语义）
    now = datetime.now(timezone.utc)
    current_start = now - timedelta(days=window_days)
    baseline_end = current_start
    baseline_start = baseline_end - timedelta(days=window_days)
    result["window_days"] = window_days
    result["baseline_window"] = {
        "start": baseline_start.isoformat(),
        "end": baseline_end.isoformat(),
    }
    result["current_window"] = {
        "start": current_start.isoformat(),
        "end": now.isoformat(),
    }
    return result


@mcp.tool()
def get_training_weekly_trend(weeks: int = 4, limit: int = 100) -> dict:
    """获取最近 N 个连续周窗口的训练量趋势（纯聚合，无业务标准）。

    把最近 weeks x 7 天切成 N 个连续、不重叠的滚动周窗口
    （[now-2w, now-w) ... [now-7d, now)，半开区间，同一时间锚点），
    每个窗口输出完整周期聚合：活动次数 / 总距离 / 总历时（含间歇）/
    总卡路里 / 步数 / 累计爬升下降 / 平均与最大心率（设备 summary
    来源，hr_activity_count 标记有效数）/ 按 activityType 原样分组。

    参数：
        weeks: 周窗口数（默认 4，范围 1~104；总跨度不超 730 天上限）。
        limit: 整个跨度最多查询多少条训练（默认 100，范围 1~100）。
            跨度内活动超过 limit 时较早的记录会被截断（与列表接口
            契约一致）。

    返回：
        anchor（统一时间锚点）/ window_days=7 / weeks 数组（时间升序，
        weeks[0] 最旧），每周含 window_start / window_end 与完整
        TrainingPeriodSummary。

    语义说明：
        - 周窗口为滚动 7 天连续区间（非自然周）：不引入时区 /
          周起始日语义；相邻窗口共享边界值但不重叠
        - 活动按自身开始时间归属唯一窗口；恰在边界上的活动归较新窗口
        - duration 为每条记录起止时间差的总历时（含间歇）
        - 缺失字段 None（不伪造 0），各 *_activity_count 标记有效数
        - 不提供周期配速；不解释 activityType 数值的运动名称

    错误：
        非法 weeks / limit 抛 ValueError；认证/HTTP 错误以
        HTCClientError 族异常清晰报告。
    """
    _validate_weeks(weeks)
    _validate_limit(limit)
    client = create_htc_client()
    try:
        trend = fetch_weekly_trend(client, weeks=weeks, limit=limit)
    finally:
        client.close()
    return trend.model_dump(mode="json")


@mcp.tool()
def get_training_weekly_trend_delta(weeks: int = 4, limit: int = 100) -> dict:
    """获取最近 N 个连续周窗口的相邻周环比变化（纯数学，无业务标准）。

    与 get_training_weekly_trend 使用完全相同的窗口切分（同一时间
    锚点、滚动 7 天半开区间、不重叠），对每对相邻周 older → newer
    输出各指标的 MetricComparison（baseline / current / delta /
    percentage_change）。

    参数：
        weeks: 周窗口数（默认 4，范围 1~104；环比最多 weeks-1 对）。
        limit: 整个跨度最多查询多少条训练（默认 100，范围 1~100）。

    返回：
        anchor / window_days=7 / transitions 数组（时间升序，最旧的
        相邻对在前），每个 transition 含 baseline 与 current 窗口的
        起止时间，以及以下指标的对比：活动次数 / 总距离 / 总历时 /
        总卡路里 / 步数 / 累计爬升 / 累计下降 / 平均与最大心率。
        weeks=1 时 transitions 为空数组（无上一周可比较）。

    语义说明：
        - delta = current - baseline；任一侧缺失(None) → delta=None
          （缺失≠0，不伪造数据）
        - percentage_change 在 baseline 缺失或为 0 时为 None（不除零）
        - count 类 delta 保持 int（0=真实零，恒可计算）
        - 心率只对比设备 summary 聚合值；每周有效样本数由
          get_training_weekly_trend 的 hr_activity_count 提供
        - duration 为每条记录起止时间差的总历时（含间歇）
        - 不提供周期配速；不解释 activityType 数值的运动名称

    错误：
        非法 weeks / limit 抛 ValueError；认证/HTTP 错误以
        HTCClientError 族异常清晰报告。
    """
    _validate_weeks(weeks)
    _validate_limit(limit)
    client = create_htc_client()
    try:
        delta = fetch_weekly_trend_delta(client, weeks=weeks, limit=limit)
    finally:
        client.close()
    return delta.model_dump(mode="json")


@mcp.tool()
def get_sleep_records(days: int = DEFAULT_SLEEP_DAYS) -> list[dict]:
    """获取最近 N 晚的睡眠记录（服务端解析口径，无本地推断）。

    通过 HTC /healthRecords 接口查询 dataType=sleep 的健康记录，
    每晚一条：上床 / 入睡 / 醒来时间，浅睡 / 深睡 / 快速眼动 / 清醒
    分段时长（分钟），总睡眠时长、睡眠得分、睡眠效率、入睡用时、
    夜醒次数等。

    参数：
        days: 查询最近多少天的睡眠记录（默认 14，范围 1~730）。
            窗口为本地时区自然日推广（含昨晚）。

    返回：
        SleepRecord 列表（按入睡时间升序），每条包含：
        - go_bed_time / fall_asleep_time / wakeup_time：时刻
        - light/deep/dream/awake_*_minutes：分段时长（分钟）
        - total_sleep_minutes：总睡眠（真实数据自洽验证：
          199+165+106=470）
        - sleep_score / sleep_efficiency_percent / sleep_latency_minutes /
          wakeup_count
        - deep_sleep_part / sleep_type：华为语义未公开，原样保留
        缺失字段为 None；无睡眠数据的夜晚不生成记录。

    说明：
        认证信息从环境变量读取；本项目不实现登录，
        也不猜测或绕过任何认证机制。
    """
    _validate_lookback_days(days)
    client = create_htc_client()
    try:
        records = fetch_sleep_records(client, days=days)
    finally:
        client.close()
    return [r.model_dump(mode="json") for r in records]


@mcp.tool()
def get_resting_heart_rate(days: int = DEFAULT_RECOVERY_DAYS) -> dict:
    """获取最近 N 天的静息心率统计（服务端聚合，无本地计算）。

    通过 HTC sampleSet 统计端点查询 dataType=
    com.huawei.instantaneous.resting_heart_rate：全窗口汇总统计 +
    按天分组明细（自然日语义由服务端按账号时区解释）。

    参数：
        days: 统计窗口天数（默认 28，范围 1~730；含今天，两端闭区间）。

    返回：
        HealthMetricStats 结构：
        - field_name：服务端返回的字段名（真实响应为 restBpm）
        - overall：全窗口 {avg, max, min, count}（bpm）
        - daily：按天 {day: "YYYY-MM-DD", stats} 列表（升序）；
          没有测量数据的日期不出现（静息心率非每日必有）
        - days / start_day / end_day：请求窗口

    语义说明：
        - 全部统计为华为服务端口径（每天一次测量按 count=1 计）
        - 缺失统计为 None（不伪造 0）；窗口内无数据时 overall 与
          daily 为空结构

    说明：
        认证信息从环境变量读取；本项目不实现登录，
        也不猜测或绕过任何认证机制。
    """
    _validate_lookback_days(days)
    client = create_htc_client()
    try:
        stats = fetch_resting_heart_rate(client, days=days)
    finally:
        client.close()
    return stats.model_dump(mode="json")


def fetch_athletic_performance(client: HTCClient) -> AthleticPerformance:
    """查询最新运动能力评估（Phase 8，GET athleticPerformance/latest）。

    timeZone 参数从本地系统推导（与浏览器 "+0800" 同格式）。
    五项指数与预测成绩全部为服务端口径，本地不换算。
    """
    raw = client.query_athletic_performance(timezone=_local_tz_offset())
    return parse_athletic_performance(raw)


def fetch_personal_bests(
    client: HTCClient, activity_type: str = "running"
) -> SportPersonalBests:
    """查询单运动类型 PB（Phase 8，GET sportReports）。

    activity_type 证据值 "running"（其他取值未经验证）。
    解析取 sportReports[] 中 activityType 匹配的第一个条目，
    personalBest 数组原样逐条解析。
    """
    raw = client.query_sport_reports(activity_type=activity_type)
    return parse_personal_bests(raw, activity_type=activity_type)


@mcp.tool()
def get_athletic_performance() -> dict:
    """获取最新运动能力评估（服务端口径，无本地计算）。

    通过 HTC athleticPerformance/latest 接口查询：跑力指数
    （runningAbility）、状态（condition，正=状态好负=疲劳累积）、
    健康（fitness）、疲劳（fatigue）、排名（ranking，百分位口径）
    与各距离预测成绩（predictedTimes，秒）。

    返回：
        - running_ability / condition / fitness / fatigue / ranking：
          华为指数口径（无量纲），缺失为 None
        - predicted_times：{距离键: 秒}，键集合原样保留
          （km1 / km3 / km5 / km10 / halfMarathon / marathon）

    语义说明：
        五项指数与预测成绩全部为华为模型服务端口径，本地不做
        任何换算或重新计算；各指数量纲华为未公开文档，原样返回。

    说明：
        认证信息从环境变量读取；本项目不实现登录，
        也不猜测或绕过任何认证机制。
    """
    client = create_htc_client()
    try:
        performance = fetch_athletic_performance(client)
    finally:
        client.close()
    return performance.model_dump(mode="json")


@mcp.tool()
def get_personal_bests(activity_type: str = "running") -> dict:
    """获取个人纪录（PB，服务端口径，无本地计算）。

    通过 HTC sportReports 接口查询指定运动类型的个人纪录列表
    （最远距离、最好分段成绩等）。

    参数：
        activity_type: 运动类型标识（默认 "running"；该值经真实
            请求验证，其他取值未验证）。

    返回：
        - activity_type：请求的运动类型
        - personal_bests：列表，每条含 name（纪录名，语义由华为
          定义，如 bestRunDistance / bestRunPartTime10KM，原样
          保留）、value（单位与 name 耦合：*Time* → 秒，
          *Distance* → 米）、start_time / end_time（达成时段）
          无纪录时为空列表；缺失字段为 None。

    说明：
        认证信息从环境变量读取；本项目不实现登录，
        也不猜测或绕过任何认证机制。
    """
    if not isinstance(activity_type, str) or not activity_type.strip():
        raise ValueError(
            "activity_type must be a non-empty string (e.g. 'running')"
        )
    activity_type = activity_type.strip()
    client = create_htc_client()
    try:
        bests = fetch_personal_bests(client, activity_type=activity_type)
    finally:
        client.close()
    return bests.model_dump(mode="json")


@mcp.tool()
def get_hrv_stats(days: int = DEFAULT_RECOVERY_DAYS) -> dict:
    """获取最近 N 天的 HRV 统计（服务端聚合，无本地计算）。

    通过 HTC healthRecords 统计端点按 fieldNames=["avgHrv"] 查询
    （dataType 为睡眠记录，即夜间 HRV）：全窗口汇总统计 + 按天分组
    明细（自然日语义由服务端按账号时区解释）。

    参数：
        days: 统计窗口天数（默认 28，范围 1~730；含今天，两端闭区间）。

    返回：
        HealthMetricStats 结构：
        - field_name："avgHrv"（服务端返回）
        - overall：全窗口 {avg, max, min, count}
        - daily：按天 {day: "YYYY-MM-DD", stats} 列表（升序）；
          没有佩戴/睡眠数据的日期不出现
        - days / start_day / end_day：请求窗口

    语义说明：
        - avgHrv 数值原样返回：华为未在响应中声明单位（典型 R-R
          间期毫秒量级），本项目不擅自换算或推断算法口径
          （如 RMSSD / SDNN 的区分未公开）
        - 缺失统计为 None（不伪造 0）；窗口内无数据时 overall 与
          daily 为空结构

    说明：
        认证信息从环境变量读取；本项目不实现登录，
        也不猜测或绕过任何认证机制。
    """
    _validate_lookback_days(days)
    client = create_htc_client()
    try:
        stats = fetch_hrv_stats(client, days=days)
    finally:
        client.close()
    return stats.model_dump(mode="json")


if __name__ == "__main__":
    # 默认以 stdio（标准输入输出）方式启动，这是 MCP 的标准通信方式。
    # 启动后它会一直等待 MCP 客户端（例如 Claude Desktop）连接，
    # 所以在终端里直接运行时会“看起来卡住”，这是正常现象。
    mcp.run()
