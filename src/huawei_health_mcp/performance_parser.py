"""训练能力域解析层（Phase 8：运动能力评估 / PB 个人纪录）。

职责：把 client 层取回的 athleticPerformance / sportReports 原始
JSON 解析成标准化模型（AthleticPerformance / SportPersonalBests）。
容错哲学与 parser.py / health_parser.py 一致：缺失字段 → None，
畸形输入不崩溃。

时间契约（浏览器勘探证据确认）：
- PB 条目 startTime / endTime：毫秒（复用 parser._ms_to_datetime）
"""

from __future__ import annotations

import math
from typing import Any, Optional

from .models import AthleticPerformance, PersonalBestEntry, SportPersonalBests
from .parser import _ms_to_datetime


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


def parse_athletic_performance(raw: Any) -> AthleticPerformance:
    """解析 GET athleticPerformance/latest 响应。

    - 五项指数缺失 / 类型异常 → None（不伪造 0）
    - predicted_times 值经 _to_float 过滤（垃圾值跳过该键），
      键集合原样保留（不硬编码距离清单）
    - 顶层非 dict（如空 list）→ 全空模型
    """
    if not isinstance(raw, dict):
        return AthleticPerformance()

    predicted: dict[str, float] = {}
    raw_predicted = raw.get("predictedTimes")
    if isinstance(raw_predicted, dict):
        for key, value in raw_predicted.items():
            number = _to_float(value)
            if number is not None:
                predicted[str(key)] = number

    return AthleticPerformance(
        running_ability=_to_float(raw.get("runningAbility")),
        condition=_to_float(raw.get("condition")),
        fitness=_to_float(raw.get("fitness")),
        fatigue=_to_float(raw.get("fatigue")),
        ranking=_to_float(raw.get("ranking")),
        predicted_times=predicted,
    )


def parse_personal_bests(raw: Any, activity_type: str) -> SportPersonalBests:
    """解析 GET sportReports 响应 → 指定运动类型的 PB 容器。

    - 取 sportReports[] 里第一个 activityType 匹配的条目；
      无匹配 / 结构异常 → 空 personal_bests（不视为错误）
    - personalBest 数组逐条解析：name 必须是非空 str（否则跳过
      该条），value / startTime / endTime 宽松转换
    """
    bests = SportPersonalBests(activity_type=activity_type)

    if not isinstance(raw, dict):
        return bests
    reports = raw.get("sportReports")
    if not isinstance(reports, list):
        return bests

    report = next(
        (
            r
            for r in reports
            if isinstance(r, dict) and r.get("activityType") == activity_type
        ),
        None,
    )
    if report is None:
        return bests

    entries = report.get("personalBest")
    if not isinstance(entries, list):
        return bests

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            continue
        start_raw = entry.get("startTime")
        end_raw = entry.get("endTime")
        # 审计修复：bool 是 int 子类，isinstance(True, int) 为 True——
        # 不排除会把 True 当 1ms 时间戳伪造出 1970 年的时间
        start_int = (
            start_raw
            if isinstance(start_raw, int) and not isinstance(start_raw, bool)
            else None
        )
        end_int = (
            end_raw
            if isinstance(end_raw, int) and not isinstance(end_raw, bool)
            else None
        )
        bests.personal_bests.append(
            PersonalBestEntry(
                name=name,
                value=_to_float(entry.get("value")),
                start_time=_ms_to_datetime(start_int),
                end_time=_ms_to_datetime(end_int),
            )
        )
    return bests
