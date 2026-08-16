"""两个训练周期之间的确定性对比层（Phase 6.9）。

职责：把两个 TrainingPeriodSummary（baseline / current）对比成
TrainingPeriodComparison——每个总量与 HR 指标输出
baseline / current / delta / percentage_change。

设计原则（与 analysis.py / period_analysis.py 一致）：
- 纯函数，无 HTTP / MCP 依赖，不修改输入对象
- 只做确定性算术，不引入任何训练业务标准（zone / 强度分类等属
  Manager 决策，见 PROJECT_STATE）
- None 语义严格继承聚合层：None=无可靠数据，绝不当作 0 参与运算；
  baseline==0 时不制造除零（percentage_change=None）
- 不做加权 / 跨源补全；HR 只对比设备 summary 聚合值（Phase 6.7 契约）
"""

from __future__ import annotations

from .models import (
    MetricComparison,
    TrainingPeriodComparison,
    TrainingPeriodSummary,
)


def compare_metric(
    baseline: float | int | None, current: float | int | None
) -> MetricComparison:
    """对比单一指标（纯函数）。

    数学契约（见 models.MetricComparison）：
    - delta = current - baseline；任一侧为 None → delta=None
      （None=无可靠数据，不得当作 0；无法证明基线是 0 时不出数字）
    - percentage_change = (current - baseline) / baseline * 100；
      baseline 为 None 或 ==0 → None（不制造除零）
    - int 输入的 delta 保持 int（count 类 0=真实零，delta 恒可计算）
    """
    if baseline is None or current is None:
        return MetricComparison(
            baseline=baseline,
            current=current,
            delta=None,
            percentage_change=None,
        )
    delta = current - baseline
    percentage = (delta / baseline) * 100 if baseline != 0 else None
    return MetricComparison(
        baseline=baseline,
        current=current,
        delta=delta,
        percentage_change=percentage,
    )


def compare_training_periods(
    baseline: TrainingPeriodSummary, current: TrainingPeriodSummary
) -> TrainingPeriodComparison:
    """对比两个周期的聚合结果（纯函数，不修改输入）。

    - 两个完整 summary 原样内嵌：各类 *_activity_count（含
      hr_activity_count）与 activity_types 分组随之保真
    - 对比层只覆盖总量与 HR 指标；average/max 单次值不单独对比
    - 不包含周期配速对比（周期配速语义未决，见 PROJECT_STATE）
    """
    return TrainingPeriodComparison(
        baseline_summary=baseline,
        current_summary=current,
        activity_count=compare_metric(
            baseline.activity_count, current.activity_count
        ),
        total_distance=compare_metric(
            baseline.total_distance, current.total_distance
        ),
        total_calories=compare_metric(
            baseline.total_calories, current.total_calories
        ),
        total_ascent=compare_metric(
            baseline.total_ascent, current.total_ascent
        ),
        total_descent=compare_metric(
            baseline.total_descent, current.total_descent
        ),
        total_steps=compare_metric(
            baseline.total_steps, current.total_steps
        ),
        total_duration_seconds=compare_metric(
            baseline.total_duration_seconds, current.total_duration_seconds
        ),
        average_heart_rate=compare_metric(
            baseline.average_heart_rate, current.average_heart_rate
        ),
        max_heart_rate=compare_metric(
            baseline.max_heart_rate, current.max_heart_rate
        ),
    )
