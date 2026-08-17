"""华为 Training Camp 数据解析层（ActivityRecord + ActivityDetail）。

职责：把华为原始 API Response JSON 转换成 models.ActivityRecord /
models.ActivityDetail。
这一层完全独立于 MCP server —— server 以后只需要调用 parse_activity_record() / parse_activity_detail()。

本文件不做任何网络请求、登录、Cookie 或认证；
只读取传入的 dict / 本地文件，并把缺失字段填成 None 或空列表。
"""

from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from .models import (
    ActivityDetail,
    ActivityRecord,
    DetailCollector,
    DetailSamplePoint,
    SamplePoint,
)

# 华为时间戳是纳秒级 Unix 时间戳（19 位，如 1786706479000000000）
_NANOSECONDS_PER_SECOND = 1_000_000_000
# 时间契约（由真实 HTC 响应逐字确认，禁止按位数自动猜单位）：
# - ActivityRecord 记录级 startTime/endTime：13 位毫秒
# - ActivityDetail 顶层 startTime/endTime：13 位毫秒（6.2-C 真实数据修正，
#   顶层 1786706473000 与同一活动列表记录的毫秒 startTime 完全一致）
# - ActivityDetail 的 details[i] 与 samplePoints[]：19 位纳秒
_MILLISECONDS_PER_SECOND = 1_000


def _ms_to_datetime(ms: Optional[int]) -> Optional[datetime]:
    """毫秒级 Unix 时间戳 → 带 UTC 时区的 datetime（ActivityRecord 契约）。

    审计修复（健壮性压力测试证实）：类型异常（字符串/dict/float）或
    超出 datetime 表示范围的值（负值/过大，fromtimestamp 会抛
    OverflowError/OSError/ValueError）一律返回 None——单条毒时间戳
    不得让整批列表解析崩溃。
    """
    if ms is None:
        return None
    try:
        ms_int = int(ms)
        seconds = ms_int // _MILLISECONDS_PER_SECOND
        micros = (ms_int % _MILLISECONDS_PER_SECOND) * 1000
        return datetime.fromtimestamp(seconds, tz=timezone.utc).replace(microsecond=micros)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _ns_to_datetime(ns: Optional[int]) -> Optional[datetime]:
    """把纳秒级 Unix 时间戳转成带 UTC 时区的 datetime。

    用整数运算避免浮点精度误差。容错契约同 _ms_to_datetime
    （类型异常 / 超界 → None，不抛错）。
    """
    if ns is None:
        return None
    try:
        ns_int = int(ns)
        seconds = ns_int // _NANOSECONDS_PER_SECOND
        micros = (ns_int % _NANOSECONDS_PER_SECOND) // 1000
        return datetime.fromtimestamp(seconds, tz=timezone.utc).replace(microsecond=micros)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _value_of(entry: dict) -> Any:
    """从 value 数组的一个元素里取出真正的值。

    华为的 value 元素形如 {"fieldName": "bpm", "floatValue": 93.0}，
    实际可能是 floatValue / integerValue / longValue / doubleValue / stringValue 之一。
    """
    for key in ("floatValue", "integerValue", "longValue", "doubleValue", "stringValue"):
        if key in entry and entry[key] is not None:
            return entry[key]
    return None


def _summary_map(data_summary: Any) -> dict[str, dict]:
    """把 dataSummary 整理成 {dataTypeName: {fieldName: value}}。

    兼容两种写法：
    - dict：键就是 dataTypeName，值是带 value 数组的对象
    - list：每个元素带 dataTypeName 字段
    """
    result: dict[str, dict] = {}
    if isinstance(data_summary, dict):
        for name, item in data_summary.items():
            if not isinstance(item, dict):
                continue
            result[name] = _fields_of(item)
    elif isinstance(data_summary, list):
        for item in data_summary:
            if not isinstance(item, dict):
                continue
            name = item.get("dataTypeName")
            if not name:
                continue
            result[name] = _fields_of(item)
    return result


def _fields_of(item: dict) -> dict:
    """从一个带 value 数组的对象里抽出 {fieldName: value}。"""
    fields: dict[str, Any] = {}
    for v in item.get("value", []) or []:
        if isinstance(v, dict) and "fieldName" in v:
            fields[v["fieldName"]] = _value_of(v)
    return fields


def _find_field(fields: dict, *names: str) -> Any:
    """在统计字段里按多个候选 fieldName 找值（兼容不同命名写法）。"""
    for n in names:
        if n in fields and fields[n] is not None:
            return fields[n]
    return None


def _iter_sample_points(detail_data: Any, data_type: str) -> list[SamplePoint]:
    """从 detailData 里取出指定 dataType 的高频采样点，按时间排序。"""
    samples: list[SamplePoint] = []
    if not isinstance(detail_data, list):
        return samples
    for collector in detail_data:
        if not isinstance(collector, dict):
            continue
        for sp in collector.get("samplePoints", []) or []:
            if not isinstance(sp, dict):
                continue
            if sp.get("dataTypeName") != data_type:
                continue
            t = _ns_to_datetime(sp.get("startTime"))
            if t is None:
                continue
            val = None
            for v in sp.get("value", []) or []:
                if isinstance(v, dict) and "fieldName" in v:
                    val = _value_of(v)
                    break
            value = _as_float(val)
            if value is None:
                continue
            samples.append(SamplePoint(time=t, value=value))
    samples.sort(key=lambda s: s.time)
    return samples


def _as_float(v: Any) -> Optional[float]:
    """容错数值转换：不可解析（垃圾字符串/dict/list 等）→ None。

    审计修复（健壮性压力测试证实）：此前裸 float()/int() 会让单条
    malformed 记录炸掉整批 parse。数字字符串按数值接受（宽松策略）；
    NaN/inf 为 JSON 非法值，出现即异常，归 None 不伪造。
    """
    if v is None:
        return None
    try:
        result = float(v)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _as_int(v: Any) -> Optional[int]:
    """容错整数转换：不可解析 → None（契约同 _as_float）。"""
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError, OverflowError):
        return None


def parse_activity_record(raw: dict) -> ActivityRecord:
    """把一次华为 ActivityRecord 的原始 JSON（dict）解析成标准 ActivityRecord。

    任何缺失字段都填 None 或空列表，不会抛错。

    统计字段位置契约（Phase 6.6 真实数据确认）：
    真实响应把 dataSummary / performanceSummary / paceSummary 嵌套在
    activitySummary 下（此前 fixture 假设的顶层写法作为兼容路径保留，
    嵌套写法优先）。
    """
    activity_summary = raw.get("activitySummary") or {}
    if not isinstance(activity_summary, dict):
        activity_summary = {}
    summary = _summary_map(
        activity_summary.get("dataSummary") or raw.get("dataSummary")
    )
    perf = (
        activity_summary.get("performanceSummary")
        or raw.get("performanceSummary")
        or {}
    )
    # 审计修复：performanceSummary 为非 dict（如字符串）时按缺失处理
    if not isinstance(perf, dict):
        perf = {}
    pace_summary = activity_summary.get("paceSummary") or {}

    # 心率统计（不同导出可能用 averageHeartRate / avg 等命名，逐个尝试）
    hr = summary.get("com.huawei.continuous.heart_rate.statistics", {})
    avg_hr = _find_field(hr, "averageHeartRate", "avgHeartRate", "average", "avg", "mean")
    max_hr = _find_field(hr, "maxHeartRate", "max")
    min_hr = _find_field(hr, "minHeartRate", "min")

    # 距离
    dist = summary.get("com.huawei.continuous.distance.total", {})
    distance = _find_field(dist, "distance", "totalDistance", "value", "sum")

    # 配速（由速度推导：pace 秒/公里 = 1000 / 速度(米/秒)）
    speed = summary.get("com.huawei.continuous.speed.statistics", {})
    avg_speed = _as_float(_find_field(speed, "averageSpeed", "avgSpeed", "average", "avg"))
    max_speed = _as_float(_find_field(speed, "maxSpeed", "max"))

    # Phase 6.8：周期聚合候选字段（真实数据 5/5 记录稳定提供）
    cal = summary.get("com.huawei.continuous.calories.burnt.total", {})
    calories = _find_field(cal, "calories_total", "caloriesTotal", "totalCalories")
    alt = summary.get("com.huawei.continuous.altitude.statistics", {})
    ascent = _find_field(alt, "ascent_total", "ascentTotal")
    descent = _find_field(alt, "descent_total", "descentTotal")
    steps_stat = summary.get("com.huawei.continuous.steps.total", {})
    steps = _find_field(steps_stat, "steps", "stepCount", "totalSteps")
    # 配速：设备 paceSummary 的权威值优先（单位：秒/公里，Phase 6.6 真实
    # 数据交叉验证：avgPace 351.5 ≈ 1000/avgSpeed 351.1）；缺失时由速度推导
    auth_avg_pace = _as_float(pace_summary.get("avgPace")) if isinstance(pace_summary, dict) else None
    auth_best_pace = _as_float(pace_summary.get("bestPace")) if isinstance(pace_summary, dict) else None
    # 0 / 负配速视为无效标记（与 posture statistics 的 -1 标记同风格），
    # 回退速度推导；推导同样要求速度为正的有限数值
    avg_pace = (
        auth_avg_pace
        if auth_avg_pace is not None and auth_avg_pace > 0
        else ((1000.0 / avg_speed) if avg_speed and avg_speed > 0 else None)
    )
    best_pace = (
        auth_best_pace
        if auth_best_pace is not None and auth_best_pace > 0
        else ((1000.0 / max_speed) if max_speed and max_speed > 0 else None)
    )

    return ActivityRecord(
        activity_id=raw.get("activityId") or raw.get("id"),
        start_time=_ms_to_datetime(raw.get("startTime")),  # 契约：记录级毫秒
        end_time=_ms_to_datetime(raw.get("endTime")),      # 契约：记录级毫秒
        activity_type=raw.get("activityType") or raw.get("type"),
        active_time=_as_int(raw.get("activeTime")),
        # Phase 6.13：记录级顶层 activeTime = 毫秒（真实数据确认），
        # 与 active_time 同源同值，作为显式单位命名的规范字段
        active_time_ms=_as_int(raw.get("activeTime")),
        distance=_as_float(distance),
        calories=_as_float(calories),
        ascent=_as_float(ascent),
        descent=_as_float(descent),
        steps=_as_int(steps),
        avg_heart_rate=_as_float(avg_hr),
        max_heart_rate=_as_float(max_hr),
        min_heart_rate=_as_float(min_hr),
        avg_pace=avg_pace,
        best_pace=best_pace,
        pace_map=[],  # 本步未解析速度采样点；后续步骤补充
        vo2_max=_as_float(perf.get("vo2Max")),
        recovery_time=_as_int(perf.get("recoveryTime")),
        training_load=_as_float(perf.get("aerobicTrainingStress")),
        heart_rate_samples=_iter_sample_points(
            raw.get("detailData"), "com.huawei.instantaneous.exercise_heart_rate"
        ),
        cadence_samples=_iter_sample_points(
            raw.get("detailData"), "com.huawei.instantaneous.steps.rate"
        ),
    )


def parse_activity_record_file(path: str | os.PathLike) -> ActivityRecord:
    """从本地 JSON 文件解析一次 ActivityRecord。

    只读本地文件，不做任何网络请求。
    """
    with open(path, "r", encoding="utf-8") as f:
        return parse_activity_record(json.load(f))

# ---------------------------------------------------------------------------
# ActivityDetail 解析（Phase 6.2-C）
#
# 真实 schema（Phase 6.1 已验证）：
#   detail 对象
#   ├── id / activityType / startTime / endTime / activeTime / timeZone
#   └── details[]                      # dataCollector 列表
#       ├── startTime / endTime        # 纳秒
#       ├── dataCollectorId
#       └── samplePoints[]
#           ├── startTime / endTime    # 纳秒
#           ├── dataTypeName
#           └── value[]                # {fieldName, floatValue|integerValue}
#
# 时间契约（禁止按位数猜单位，单位只由真实 schema 决定）：
# - detail 顶层 startTime/endTime：毫秒（在 parse_activity_detail 内用 _ms_to_datetime）
# - details[i] 与 samplePoints[]：纳秒（_ns_to_datetime）
# ---------------------------------------------------------------------------


def _parse_sample_values(value: Any) -> dict[str, float | int]:
    """把 samplePoint 的 value[] 转成 {fieldName: float|int}。

    真实 detail 响应只出现 floatValue 与 integerValue 两种（6.1 已确认），
    本函数按严格规则处理，malformed 条目直接跳过、不静默制造错误数据：
    - 非 dict 条目：跳过
    - 缺 fieldName（或非字符串）：跳过
    - floatValue 与 integerValue 同时存在：视为 malformed，跳过
    - 两者都缺失：跳过
    - 值无法转为 float/int（如 None / 非数字字符串）：跳过
    """
    values: dict[str, float | int] = {}
    if not isinstance(value, list):
        return values
    for item in value:
        if not isinstance(item, dict):
            continue
        field_name = item.get("fieldName")
        if not isinstance(field_name, str) or not field_name:
            continue
        has_float = "floatValue" in item
        has_int = "integerValue" in item
        if has_float and has_int:
            continue  # 同时存在两种值定义，无法判断真实意图
        if not has_float and not has_int:
            continue  # 没有任何值定义
        if has_float:
            try:
                values[field_name] = float(item["floatValue"])
            except (TypeError, ValueError):
                continue
        else:
            try:
                values[field_name] = int(item["integerValue"])
            except (TypeError, ValueError):
                continue
    return values


def _parse_detail_collector(collector: Any) -> DetailCollector:
    """把 details[i]（单个 dataCollector）解析成 DetailCollector。

    容错规则与项目既有风格一致（缺失即 None / 空列表，不抛错）：
    - 非 dict 输入 → 空 DetailCollector
    - samplePoint 缺 startTime 或 dataTypeName → 跳过该采样点
      （模型中两者是必填，缺失则采样点无法定位与分类）
    """
    if not isinstance(collector, dict):
        return DetailCollector()

    sample_points: list[DetailSamplePoint] = []
    for sp in collector.get("samplePoints") or []:
        if not isinstance(sp, dict):
            continue
        timestamp = _ns_to_datetime(sp.get("startTime"))  # 契约：detail 纳秒
        data_type_name = sp.get("dataTypeName")
        if timestamp is None or not isinstance(data_type_name, str) or not data_type_name:
            continue
        sample_points.append(
            DetailSamplePoint(
                timestamp=timestamp,
                data_type_name=data_type_name,
                values=_parse_sample_values(sp.get("value")),
            )
        )

    return DetailCollector(
        data_collector_id=collector.get("dataCollectorId"),
        start_time=_ns_to_datetime(collector.get("startTime")),  # 契约：纳秒
        end_time=_ns_to_datetime(collector.get("endTime")),      # 契约：纳秒
        sample_points=sample_points,
    )


def parse_activity_detail(raw: Any) -> ActivityDetail:
    """把一次华为 Activity Detail 的原始 JSON 解析成标准 ActivityDetail。

    输入兼容两种真实形态（Phase 6.1 已验证，client 契约返回顶层 list）：
    - dict：detail 对象本身
    - list：client.query_activity_detail() 的顶层返回（含 1 个 detail 对象），
      取第一个元素；空列表返回空 ActivityDetail（details=[] 合法，真实 API
      已证明请求的 dataType 不保证返回）

    顶层字段映射：id/activityType/startTime/endTime/activeTime/timeZone；
    name/desc/deviceInfo/appInfo/activitySummary 等暂不进入 domain model。
    任何缺失字段都填 None 或空列表，不会抛错。
    """
    if isinstance(raw, list):
        raw = raw[0] if raw else {}
    if not isinstance(raw, dict):
        return ActivityDetail()

    return ActivityDetail(
        activity_id=raw.get("id") or raw.get("activityId"),
        activity_type=_as_int(raw.get("activityType")),
        # 契约修正（真实数据三重交叉验证）：detail 顶层是毫秒，与同一活动
        # 列表记录的 startTime 毫秒值完全一致；collector / sample 才是纳秒。
        start_time=_ms_to_datetime(raw.get("startTime")),  # 契约：顶层毫秒
        end_time=_ms_to_datetime(raw.get("endTime")),      # 契约：顶层毫秒
        active_time=_as_int(raw.get("activeTime")),
        time_zone=raw.get("timeZone") if isinstance(raw.get("timeZone"), str) else None,
        details=[
            _parse_detail_collector(collector)
            for collector in raw.get("details") or []
        ],
    )