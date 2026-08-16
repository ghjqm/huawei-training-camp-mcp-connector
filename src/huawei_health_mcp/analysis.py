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
    CollectorSummary,
    DetailCollector,
    DetailSamplePoint,
    FieldStats,
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