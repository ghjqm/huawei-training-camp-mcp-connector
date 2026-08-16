"""Huawei Training Camp MCP —— 服务入口与 MCP 工具。

本文件负责：
- 创建 FastMCP 服务
- 注册 MCP 工具（health_check、get_recent_activities、get_activity_detail、get_training_summary）
- 把 MCP 工具接到现有的 HTCClient（数据获取层）与 parser（解析层）

设计原则：
- 复用现有 client.py / parser.py，不重复实现 HTTP 或解析逻辑。
- 认证信息（Cookie）只从环境变量读取，绝不写进代码 / 日志 / 测试。
- 不实现登录、不猜测认证机制、不绕过任何访问控制。
- 不连接数据库、不做 AI 分析、不做 Dashboard。
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

from fastmcp import FastMCP

from .analysis import summarize_activity_detail
from .client import HTCClient, HTCClientError
from .models import ActivityDetail, ActivityRecord, TrainingSummary
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


def build_htc_headers() -> dict[str, str]:
    """从环境变量构造请求 headers。

    只读取调用方显式提供的 HTC_COOKIE；不猜测任何认证字段，
    也不写入任何真实认证信息。若未设置环境变量，返回空 headers
    （此时真实请求会因缺少认证而失败，由上层清晰报错）。
    """
    headers: dict[str, str] = {}
    cookie = os.environ.get("HTC_COOKIE")
    if cookie:
        headers["Cookie"] = cookie
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

def _default_time_range(lookback_days: int = DEFAULT_LOOKBACK_DAYS) -> tuple[str, str]:
    """返回 (start_time, end_time) 毫秒级字符串时间戳。

    HTC 列表接口需要时间范围；默认取“最近 N 天”到“现在”。
    """
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=lookback_days)
    return (str(int(start.timestamp() * 1000)), str(int(now.timestamp() * 1000)))


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


def fetch_activity_detail(
    client: HTCClient,
    activity_id: str,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    detail_data_types: list[str] | None = None,
) -> ActivityDetail:
    """核心逻辑：按 activity_id 查询单次活动详情并解析为 ActivityDetail。

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
    return parse_activity_detail(raw_detail)

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
        认证所需的 Cookie 从环境变量 HTC_COOKIE 读取；
        本项目不实现登录，也不猜测或绕过任何认证机制。
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

if __name__ == "__main__":
    # 默认以 stdio（标准输入输出）方式启动，这是 MCP 的标准通信方式。
    # 启动后它会一直等待 MCP 客户端（例如 Claude Desktop）连接，
    # 所以在终端里直接运行时会“看起来卡住”，这是正常现象。
    mcp.run()
