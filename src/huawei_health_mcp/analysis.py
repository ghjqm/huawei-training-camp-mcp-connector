"""训练数据确定性分析层（Phase 6.3）。

职责：把 parser 产出的 ActivityDetail 聚合成 TrainingSummary——
每个 dataCollector 的样本数 / 时间范围 / 各 fieldName 的
min / max / avg / first / last 统计。

设计原则：
- 纯函数，不做网络、不依赖 MCP，便于直接单测（与 client/parser 同风格）
- 只做确定性聚合，不做 AI 推断、不猜业务语义（心率区间/配速分区等属后续阶段）
- 不修改、不重算任何原始契约数据（时间戳单位、value 类型原样沿用）
"""

from __future__ import annotations

from .models import (
    ActivityDetail,
    ActivityRecord,
    CollectorSummary,
    DetailCollector,
    DetailSamplePoint,
    FieldStats,
    SessionMetrics,
    TrainingSummary,
)


def _summarize_fields(sample_points: list[DetailSamplePoint]) -> list[FieldStats]:
    """按 fieldName 聚合采样值，输出确定性统计（保持字段首次出现顺序）。"""
    series: dict[str, list[float | int]] = {}
    for sp in sample_points:
        for field_name, value in sp.values.items():
            series.setdefault(field_name, []).append(value)

    stats: list[FieldStats] = []
    for field_name, values in series.items():
        stats.append(
            FieldStats(
                field_name=field_name,
                count=len(values),
                min_value=min(values),
                max_value=max(values),
                avg_value=sum(values) / len(values),
                first_value=values[0],
                last_value=values[-1],
            )
        )
    return stats


def _summarize_collector(collector: DetailCollector) -> CollectorSummary:
    """聚合单个 collector；时间范围缺失时退回首末 sample timestamp。"""
    samples = collector.sample_points
    start = collector.start_time
    end = collector.end_time
    if samples:
        if start is None:
            start = samples[0].timestamp
        if end is None:
            end = samples[-1].timestamp
    return CollectorSummary(
        data_collector_id=collector.data_collector_id,
        data_type_name=samples[0].data_type_name if samples else None,
        sample_count=len(samples),
        start_time=start,
        end_time=end,
        fields=_summarize_fields(samples),
    )


def summarize_activity_detail(detail: ActivityDetail) -> TrainingSummary:
    """把 ActivityDetail 聚合成 TrainingSummary（确定性统计，无 AI）。"""
    return TrainingSummary(
        activity_id=detail.activity_id,
        activity_type=detail.activity_type,
        start_time=detail.start_time,
        end_time=detail.end_time,
        active_time=detail.active_time,
        time_zone=detail.time_zone,
        collectors=[_summarize_collector(c) for c in detail.details],
    )


# ---------------------------------------------------------------------------
# 会话级指标（Phase 6.6）：纯数学聚合，不引入任何训练强度分区标准
# （HR zones / pace zones 属 Manager 决策，见 PROJECT_STATE）
# ---------------------------------------------------------------------------

HR_TYPE = "com.huawei.instantaneous.exercise_heart_rate"
SPEED_TYPE = "com.huawei.instantaneous.speed"


def _field_values(detail: ActivityDetail, data_type: str, field_name: str) -> list[float | int]:
    """从 detail 采样中按 dataType + fieldName 收集数值序列（保持时间顺序）。"""
    values: list[float | int] = []
    for collector in detail.details:
        for sp in collector.sample_points:
            if sp.data_type_name == data_type and field_name in sp.values:
                values.append(sp.values[field_name])
    return values


def compute_session_metrics(
    detail: ActivityDetail, record: ActivityRecord | None = None
) -> SessionMetrics:
    """计算单次训练的会话级指标。

    双源策略：
    - 心率 / 配速 / 距离：优先 record 的设备权威统计
      （activitySummary，Phase 6.6 起可从真实数据解析），
      缺失时回退 detail 高频采样聚合
    - 速度：来自 detail 采样聚合（ActivityRecord 未存速度本身）
    - duration：detail 顶层 start/end 时间差（纯数学，毫秒契约）

    所有指标缺失即 None，绝不制造数据；不做任何业务标准推断。
    """
    duration: float | None = None
    if detail.start_time is not None and detail.end_time is not None:
        duration = (detail.end_time - detail.start_time).total_seconds()

    # ---- 心率（bpm）：record 权威 → 采样聚合 ----
    avg_hr = max_hr = min_hr = None
    hr_source: str | None = None
    if record is not None and record.avg_heart_rate is not None:
        avg_hr, max_hr, min_hr = (
            record.avg_heart_rate,
            record.max_heart_rate,
            record.min_heart_rate,
        )
        hr_source = "device_summary"
    else:
        hr_values = _field_values(detail, HR_TYPE, "bpm")
        if hr_values:
            avg_hr = sum(hr_values) / len(hr_values)
            max_hr = float(max(hr_values))
            min_hr = float(min(hr_values))
            hr_source = "samples"

    # ---- 速度（m/s）：detail 采样聚合 ----
    speed_values = _field_values(detail, SPEED_TYPE, "speed")
    avg_speed = max_speed = None
    speed_source: str | None = None
    if speed_values:
        avg_speed = sum(speed_values) / len(speed_values)
        max_speed = float(max(speed_values))
        speed_source = "samples"

    # ---- 配速（秒/公里）：record 权威（paceSummary）→ 采样换算 ----
    avg_pace = best_pace = None
    pace_source: str | None = None
    if record is not None and record.avg_pace is not None:
        avg_pace, best_pace = record.avg_pace, record.best_pace
        pace_source = "device_summary"
    elif avg_speed is not None and avg_speed > 0:
        avg_pace = 1000.0 / avg_speed
        if max_speed is not None and max_speed > 0:
            best_pace = 1000.0 / max_speed
        pace_source = "samples"

    # ---- 距离（米）：record 权威；不积分采样（采样间隔不均匀，积分会失真）----
    distance = record.distance if record is not None else None
    distance_source = "device_summary" if distance is not None else None

    return SessionMetrics(
        activity_id=detail.activity_id,
        duration_seconds=duration,
        active_time_raw=detail.active_time,
        avg_heart_rate=avg_hr,
        max_heart_rate=max_hr,
        min_heart_rate=min_hr,
        heart_rate_source=hr_source,
        avg_speed=avg_speed,
        max_speed=max_speed,
        speed_source=speed_source,
        avg_pace=avg_pace,
        best_pace=best_pace,
        pace_source=pace_source,
        distance=distance,
        distance_source=distance_source,
    )
