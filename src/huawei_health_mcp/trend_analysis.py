"""连续周趋势聚合层（Phase 6.10）。

职责：把已解析的 ActivityRecord 按最近 N 个连续 7 天周窗口聚合为
TrainingTrendSummary（每窗口复用 aggregate_activity_period）。

设计原则（与 period_analysis / period_comparison 一致）：
- 纯函数，无 HTTP / MCP 依赖，不修改输入对象
- 滚动 7 天连续窗口（非自然周）：不引入时区 / 周起始日语义，
  复用 Phase 6.9 已确立的连续半开区间语义
- 所有窗口由同一 now 锚点切分，相邻窗口共享边界值但不重叠
- 缺失语义严格继承聚合层：None=无可靠数据，绝不当作 0
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .models import (
    ActivityRecord,
    TrainingTrendDelta,
    TrainingTrendSummary,
    TrainingWeekSummary,
    WeekDeltaTransition,
)
from .period_analysis import aggregate_activity_period
from .period_comparison import compare_metric

# 周窗口长度（天）：滚动窗口，非自然周
WINDOW_DAYS = 7


def build_week_windows(
    weeks: int, now: datetime
) -> list[tuple[datetime, datetime]]:
    """构造 N 个连续 7 天窗口（同一 now 锚点，半开区间，不重叠）。

    返回按时间升序：result[0] 最旧，result[-1] = [now-7d, now)。
    边界规则（与 Phase 6.9 comparison 窗口一致）：
    - 窗口 i = [now - (weeks-i)*7d, now - (weeks-1-i)*7d)
    - 相邻窗口共享边界毫秒值；恰在边界上的活动只归较新窗口
    """
    if weeks < 1:
        raise ValueError(f"weeks 必须为正整数，当前为 {weeks}")
    return [
        (
            now - timedelta(days=(weeks - i) * WINDOW_DAYS),
            now - timedelta(days=(weeks - 1 - i) * WINDOW_DAYS),
        )
        for i in range(weeks)
    ]


def aggregate_weekly_trend(
    records: list[ActivityRecord],
    weeks: int = 4,
    now: datetime | None = None,
) -> TrainingTrendSummary:
    """把记录按连续 7 天周窗口聚合为趋势（纯函数，不修改输入）。

    - 活动按自身 start_time 归属唯一窗口 [start, end)；
      start_time 缺失或落在全部窗口之外的记录被排除
    - 每窗口复用 aggregate_activity_period（None≠0 契约继承）
    - now 缺省取当前时刻；server 层必须传入与请求一致的锚点
    """
    anchor = now or datetime.now(timezone.utc)
    windows = build_week_windows(weeks, anchor)

    buckets: list[list[ActivityRecord]] = [[] for _ in windows]
    for record in records:
        if record.start_time is None:
            continue
        for i, (start, end) in enumerate(windows):
            if start <= record.start_time < end:
                buckets[i].append(record)
                break

    return TrainingTrendSummary(
        anchor=anchor,
        window_days=WINDOW_DAYS,
        weeks=[
            TrainingWeekSummary(
                window_start=start,
                window_end=end,
                summary=aggregate_activity_period(bucket),
            )
            for (start, end), bucket in zip(windows, buckets)
        ],
    )


def compute_weekly_trend_delta(trend: TrainingTrendSummary) -> TrainingTrendDelta:
    """把周趋势转成相邻周环比序列（纯函数，不修改输入）。

    - 只比较相邻两个 7 天滚动窗口：older（baseline）→ newer（current）
    - 数学完全复用 compare_metric（Phase 6.9），零重复实现：
      None≠0 / baseline==0 不除零 / count 类保持 int
    - 窗口边界与顺序直接取自 trend.weeks（继承 Phase 6.10 同一锚点
      半开区间），本函数不重新定义任何窗口语义
    - weeks=1 → transitions=[]（无上一周可比较，最小合理结果）
    - HR 仅对比 average/max（设备 summary 聚合，6.7 来源契约）；
      有效样本数由 trend.weeks[].summary.hr_activity_count 保真
    """
    transitions: list[WeekDeltaTransition] = []
    for older, newer in zip(trend.weeks, trend.weeks[1:]):
        b, c = older.summary, newer.summary
        transitions.append(
            WeekDeltaTransition(
                baseline_window_start=older.window_start,
                baseline_window_end=older.window_end,
                current_window_start=newer.window_start,
                current_window_end=newer.window_end,
                activity_count=compare_metric(
                    b.activity_count, c.activity_count
                ),
                total_distance=compare_metric(
                    b.total_distance, c.total_distance
                ),
                total_calories=compare_metric(
                    b.total_calories, c.total_calories
                ),
                total_steps=compare_metric(b.total_steps, c.total_steps),
                total_ascent=compare_metric(b.total_ascent, c.total_ascent),
                total_descent=compare_metric(
                    b.total_descent, c.total_descent
                ),
                total_duration_seconds=compare_metric(
                    b.total_duration_seconds, c.total_duration_seconds
                ),
                average_heart_rate=compare_metric(
                    b.average_heart_rate, c.average_heart_rate
                ),
                max_heart_rate=compare_metric(
                    b.max_heart_rate, c.max_heart_rate
                ),
            )
        )
    return TrainingTrendDelta(
        anchor=trend.anchor,
        window_days=trend.window_days,
        transitions=transitions,
    )
