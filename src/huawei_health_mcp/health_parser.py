"""恢复状态数据域解析层（Phase 7：睡眠 / 静息心率 / HRV）。

职责：把 client 层取回的 healthRecords / periodStatistics 原始 JSON
解析成标准化模型（SleepRecord / HealthMetricStats）。
与 parser.py 同一套容错哲学：缺失字段 → None，畸形输入不崩溃。

时间契约（浏览器勘探证据确认，见 data_temp/htc探查.txt）：
- 睡眠记录顶层 startTime / endTime：纳秒（复用 parser._ns_to_datetime）
- value 字段里的 go_bed/fall_asleep/wakeup_time：毫秒
  （复用 parser._ms_to_datetime）
- periodStatistics 的 startDay / endDay：YYYYMMDD（int 或 str 两种
  写法都容忍，端点间不混用由 client 层保证）
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Optional

from .models import DailyStat, HealthMetricStats, SleepRecord, StatBlock
from .parser import _ms_to_datetime, _ns_to_datetime

# 睡眠 value 字段名 → 模型字段映射（分组按证据单位）
_MS_TIME_FIELDS = {
    "go_bed_time": "go_bed_time",
    "fall_asleep_time": "fall_asleep_time",
    "wakeup_time": "wakeup_time",
}

_MINUTE_INT_FIELDS = {
    "light_sleep_time": "light_sleep_minutes",
    "deep_sleep_time": "deep_sleep_minutes",
    "dream_time": "dream_sleep_minutes",
    "awake_time": "awake_minutes",
    "all_sleep_time": "total_sleep_minutes",
    "sleep_latency": "sleep_latency_minutes",
}

_PLAIN_INT_FIELDS = {
    "wakeup_count": "wakeup_count",
    "deep_sleep_part": "deep_sleep_part",
    "sleep_score": "sleep_score",
    "sleep_efficiency": "sleep_efficiency_percent",
    "sleep_type": "sleep_type",
}

# value 数组里可能出现的取值键（ fieldName 之外的 *Value ）
_VALUE_KEYS = ("longValue", "integerValue", "floatValue", "doubleValue")


def _value_map(value_list: Any) -> dict[str, Any]:
    """把 healthRecords value 数组压成 {fieldName: 数值} 映射。

    非 dict 元素 / 缺 fieldName 的元素直接跳过，不报错。
    """
    mapping: dict[str, Any] = {}
    if not isinstance(value_list, list):
        return mapping
    for entry in value_list:
        if not isinstance(entry, dict):
            continue
        name = entry.get("fieldName")
        if not isinstance(name, str):
            continue
        for key in _VALUE_KEYS:
            if key in entry:
                mapping[name] = entry[key]
                break
    return mapping


def _to_int(value: Any) -> Optional[int]:
    """宽松 int 转换：bool / 非数值 / NaN 返回 None，不抛异常。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    return None


def _to_float(value: Any) -> Optional[float]:
    """宽松 float 转换：bool / 非数值 / NaN / inf / 超范围整数 → None。

    审计修复（Phase 8 后全程审计）：与 parser._as_float 同一容错契约——
    NaN / inf 是 JSON 非法值（Python json 模块默认接受 NaN / Infinity
    字面量），出现即归 None 不伪造；超大 int 转 float 会 OverflowError，
    同样归 None。与 _as_float 的差异：不接受数字字符串（本域数值
    契约为 JSON 数值，字符串一律 None）。
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        try:
            return float(value)
        except OverflowError:
            return None
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return None


def _day_str(day: Any) -> Optional[str]:
    """YYYYMMDD（int 或 str）→ "YYYY-MM-DD"；无法解析返回 None。

    审计修复：限制 ASCII 数字（unicode 数字如 "٢٠٢٦٠٨١٧" 的
    isdigit() 为 True 但非本域日格式契约，归 None 不伪造）。
    """
    if isinstance(day, bool):
        return None
    text = str(day) if isinstance(day, (int, str)) else None
    if not text or len(text) != 8 or not (text.isascii() and text.isdigit()):
        return None
    return f"{text[0:4]}-{text[4:6]}-{text[6:8]}"


def parse_sleep_records(raw: Any) -> list[SleepRecord]:
    """解析 GET /healthRecords 睡眠响应 → SleepRecord 列表。

    - 顶层 dict 取 healthRecords 数组；顶层 list 直接作为数组
      （与 _extract_activity_list 同一套容忍策略）
    - 按记录自身 startTime 升序排列（缺失的排最前，不报错）
    - fragment 子结构（subData）不解析（v1 不需要分段时间线）
    """
    if isinstance(raw, dict):
        items = raw.get("healthRecords")
    elif isinstance(raw, list):
        items = raw
    else:
        items = None
    if not isinstance(items, list):
        return []

    records: list[SleepRecord] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        values = _value_map(item.get("value"))
        fields: dict[str, Any] = {
            "record_id": item.get("id") if isinstance(item.get("id"), str) else None,
            "start_time": _ns_to_datetime(item.get("startTime")),
            "end_time": _ns_to_datetime(item.get("endTime")),
        }
        for source, target in _MS_TIME_FIELDS.items():
            fields[target] = _ms_to_datetime(_to_int(values.get(source)))
        for source, target in {**_MINUTE_INT_FIELDS, **_PLAIN_INT_FIELDS}.items():
            fields[target] = _to_int(values.get(source))
        records.append(SleepRecord(**fields))

    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    records.sort(key=lambda r: r.start_time or epoch)
    return records


def _stat_block(statistics: Any) -> StatBlock:
    """statistics dict → StatBlock（键缺失 / 类型异常 → None）。"""
    if not isinstance(statistics, dict):
        return StatBlock()
    return StatBlock(
        avg=_to_float(statistics.get("avg")),
        max=_to_float(statistics.get("max")),
        min=_to_float(statistics.get("min")),
        count=_to_int(statistics.get("count")),
    )


def _matching_entries(entries: Any, field_name: Optional[str]) -> list[dict]:
    """从 results / groupStatistics 数组里筛出匹配 fieldName 的 dict 项。

    field_name 为 None 时不筛（静息心率端点由服务端推断 fieldName）；
    筛选后为空且未指定 field_name 时回退到全部 dict 项（保底取第一个）。
    """
    if not isinstance(entries, list):
        return []
    dicts = [e for e in entries if isinstance(e, dict)]
    if field_name is None:
        return dicts
    matched = [e for e in dicts if e.get("fieldName") == field_name]
    return matched


def parse_health_metric_stats(
    raw: Any,
    *,
    data_type: str,
    days: int,
    start_day: str,
    end_day: str,
    field_name: Optional[str] = None,
) -> HealthMetricStats:
    """解析 periodStatistics 响应 → HealthMetricStats。

    - overall 取 results 里第一个匹配 fieldName 的条目
      （未指定 field_name 时取第一个条目，fieldName 以服务端返回为准）
    - daily 汇总所有 groupResults[].groupStatistics[] 里匹配条目，
      按 "YYYY-MM-DD" 升序；同日重复条目保留第一个
    - 服务端没返回任何统计时 overall 为空 StatBlock、daily 为空列表
      （例如窗口内无数据），不视为错误
    """
    overall = StatBlock()
    resolved_field_name = field_name
    daily: list[DailyStat] = []

    if isinstance(raw, dict):
        results = _matching_entries(raw.get("results"), field_name)
        if results:
            # 审计修复：服务端 fieldName 非 str（如数字/嵌套结构）时
            # 不得传入 Optional[str] 模型字段（Pydantic 拒绝 int→str
            # 强转，会抛 ValidationError 炸掉整次解析）——保持调用方
            # 传入的 field_name
            server_field = results[0].get("fieldName")
            if isinstance(server_field, str) and server_field:
                resolved_field_name = server_field
            overall = _stat_block(results[0].get("statistics"))

        seen_days: set[str] = set()
        groups = raw.get("groupResults")
        if isinstance(groups, list):
            pending: list[tuple[str, dict]] = []
            for group in groups:
                if not isinstance(group, dict):
                    continue
                entries = _matching_entries(
                    group.get("groupStatistics"), resolved_field_name
                )
                for entry in entries:
                    day = _day_str(entry.get("startDay"))
                    if day is not None:
                        pending.append((day, entry))
            pending.sort(key=lambda pair: pair[0])
            for day, entry in pending:
                if day in seen_days:
                    continue
                seen_days.add(day)
                daily.append(DailyStat(day=day, stats=_stat_block(entry.get("statistics"))))

    return HealthMetricStats(
        data_type=data_type,
        field_name=resolved_field_name,
        days=days,
        start_day=start_day,
        end_day=end_day,
        overall=overall,
        daily=daily,
    )
