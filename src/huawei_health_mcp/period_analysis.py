"""多次训练的周期聚合层（Phase 6.7）。

职责：把已解析的多个 ActivityRecord 聚合成 TrainingPeriodSummary——
活动次数 / 距离 / 历时 / HR / 按 activityType 分组的基础统计。

设计原则（与 analysis.py 一致）：
- 纯函数，无 HTTP / MCP 依赖，不修改输入对象
- 只做确定性聚合，不引入任何训练业务标准（zone / load / 强度分类等
  属 Manager 决策，见 PROJECT_STATE）
- 不重新处理时间戳：只消费 parser 产出的 datetime 字段（契约见 models）
- 距离只聚合设备权威 summary 值，绝不积分采样（Phase 6.6 契约）
"""

from __future__ import annotations

from .models import ActivityRecord, ActivityTypeSummary, TrainingPeriodSummary


def _duration_of(record: ActivityRecord) -> float | None:
    """单条记录的历时（秒）：end_time - start_time。

    任一时间缺失返回 None（不计入聚合）。
    语义注意：总历时含间歇，非纯运动时间（纯运动时长由 active_time_ms
    聚合，Phase 6.13 已确认 activeTime 单位为毫秒）。
    """
    if record.start_time is None or record.end_time is None:
        return None
    return (record.end_time - record.start_time).total_seconds()


def aggregate_activity_period(records: list[ActivityRecord]) -> TrainingPeriodSummary:
    """把一段周期内的 ActivityRecord 列表聚合成 TrainingPeriodSummary。

    - 空列表合法：返回全零/全 None 的空 summary
    - 距离/HR 缺失的活动不计入对应聚合，*_activity_count 标记有效样本数
    - activityType 原样分组（int），不命名语义
    - Phase 6.13：total_active_time_seconds = Σ(active_time_ms/1000)（唯一
      单位换算点），全缺 → None；average_pace_seconds_per_km =
      ΣactiveTime(秒) / Σdistance(公里)，只在双值活动子集上计算（设备
      口径；配速是比率，禁止对 per-activity 配速做算术平均）
    """
    distances: list[float] = []
    calories_list: list[float] = []
    ascents: list[float] = []
    descents: list[float] = []
    steps_list: list[int] = []
    durations: list[float] = []
    active_ms_values: list[int] = []
    pace_active_seconds: list[float] = []  # 与 pace_distance_km 一一对应（双值活动）
    pace_distance_km: list[float] = []
    avg_hrs: list[float] = []
    max_hrs: list[float] = []
    starts: list = []
    ends: list = []
    type_buckets: dict[int, dict] = {}

    for record in records:
        if record.start_time is not None:
            starts.append(record.start_time)
        if record.end_time is not None:
            ends.append(record.end_time)

        if record.distance is not None:
            distances.append(float(record.distance))
        if record.calories is not None:
            calories_list.append(float(record.calories))
        if record.ascent is not None:
            ascents.append(float(record.ascent))
        if record.descent is not None:
            descents.append(float(record.descent))
        if record.steps is not None:
            steps_list.append(int(record.steps))
        if record.active_time_ms is not None:
            active_ms_values.append(int(record.active_time_ms))
        if record.active_time_ms is not None and record.distance is not None:
            pace_active_seconds.append(record.active_time_ms / 1000.0)
            pace_distance_km.append(record.distance / 1000.0)

        duration = _duration_of(record)
        if duration is not None:
            durations.append(duration)

        if record.avg_heart_rate is not None:
            avg_hrs.append(float(record.avg_heart_rate))
        if record.max_heart_rate is not None:
            max_hrs.append(float(record.max_heart_rate))

        if record.activity_type is not None:
            bucket = type_buckets.setdefault(
                record.activity_type,
                {
                    "count": 0,
                    "distances": [],
                    "durations": [],
                    "active_ms": [],
                    "pace_active_seconds": [],
                    "pace_distance_km": [],
                },
            )
            bucket["count"] += 1
            if record.distance is not None:
                bucket["distances"].append(float(record.distance))
            if duration is not None:
                bucket["durations"].append(duration)
            if record.active_time_ms is not None:
                bucket["active_ms"].append(int(record.active_time_ms))
            if record.active_time_ms is not None and record.distance is not None:
                bucket["pace_active_seconds"].append(record.active_time_ms / 1000.0)
                bucket["pace_distance_km"].append(record.distance / 1000.0)

    pace_total_km = sum(pace_distance_km)
    activity_types = [
        ActivityTypeSummary(
            activity_type=activity_type,
            count=bucket["count"],
            total_distance=(
                sum(bucket["distances"]) if bucket["distances"] else None
            ),
            total_duration_seconds=sum(bucket["durations"]),
            total_active_time_seconds=(
                sum(bucket["active_ms"]) / 1000.0
                if bucket["active_ms"]
                else None
            ),
            active_time_activity_count=len(bucket["active_ms"]),
            average_pace_seconds_per_km=(
                sum(bucket["pace_active_seconds"]) / sum(bucket["pace_distance_km"])
                if bucket["pace_active_seconds"] and sum(bucket["pace_distance_km"]) > 0
                else None
            ),
            pace_activity_count=len(bucket["pace_active_seconds"]),
        )
        for activity_type, bucket in sorted(type_buckets.items())
    ]

    return TrainingPeriodSummary(
        activity_count=len(records),
        period_start=min(starts) if starts else None,
        period_end=max(ends) if ends else None,
        total_distance=sum(distances) if distances else None,
        distance_activity_count=len(distances),
        total_calories=sum(calories_list) if calories_list else None,
        calories_activity_count=len(calories_list),
        total_ascent=sum(ascents) if ascents else None,
        total_descent=sum(descents) if descents else None,
        ascent_activity_count=len(ascents),
        total_steps=sum(steps_list) if steps_list else None,
        steps_activity_count=len(steps_list),
        total_duration_seconds=sum(durations),
        duration_activity_count=len(durations),
        total_active_time_seconds=(
            sum(active_ms_values) / 1000.0 if active_ms_values else None
        ),
        active_time_activity_count=len(active_ms_values),
        average_pace_seconds_per_km=(
            sum(pace_active_seconds) / pace_total_km
            if pace_active_seconds and pace_total_km > 0
            else None
        ),
        pace_activity_count=len(pace_active_seconds),
        average_distance=(
            sum(distances) / len(distances) if distances else None
        ),
        average_duration_seconds=(
            sum(durations) / len(durations) if durations else None
        ),
        max_distance=max(distances) if distances else None,
        max_duration_seconds=max(durations) if durations else None,
        average_heart_rate=(
            sum(avg_hrs) / len(avg_hrs) if avg_hrs else None
        ),
        max_heart_rate=max(max_hrs) if max_hrs else None,
        hr_activity_count=len(avg_hrs),
        activity_types=activity_types,
    )