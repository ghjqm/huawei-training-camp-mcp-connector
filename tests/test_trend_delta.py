"""周趋势环比测试（Phase 6.11）：compute_weekly_trend_delta + MCP tool。

全部 synthetic 或真实 fixture 离线计算，不联网、无真实凭证。
真实 fixture 仅做"真实数据解析 / 归窗 / 环比结构"验证（一次 1.43 天
快照不伪装成真实多周趋势），跨真实周变化由 synthetic 验证并标记。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from src.huawei_health_mcp.client import HTCClient
from src.huawei_health_mcp.models import ActivityRecord
from src.huawei_health_mcp.period_comparison import compare_metric
from src.huawei_health_mcp.server import (
    MAX_TREND_WEEKS,
    get_training_weekly_trend_delta,
)
from src.huawei_health_mcp.trend_analysis import (
    aggregate_weekly_trend,
    compute_weekly_trend_delta,
)

REAL_RECORDS = Path(__file__).parent.parent / "data_temp" / "real_activity_records.json"

NOW = datetime(2026, 8, 16, 12, 0, 0, tzinfo=timezone.utc)


class _FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):  # noqa: ARG003
        return NOW


def _at(days_ago: float) -> datetime:
    return NOW - timedelta(days=days_ago)


def _rec(
    rid: str, days_ago: float, hours: float = 1.0, activity_type: int = 56,
    distance: float | None = None, avg_hr: float | None = None,
    max_hr: float | None = None, calories: float | None = None,
    ascent: float | None = None, descent: float | None = None,
    steps: int | None = None,
) -> ActivityRecord:
    return ActivityRecord(
        activity_id=rid, activity_type=activity_type,
        start_time=_at(days_ago), end_time=_at(days_ago) + timedelta(hours=hours),
        distance=distance, avg_heart_rate=avg_hr, max_heart_rate=max_hr,
        calories=calories, ascent=ascent, descent=descent, steps=steps,
    )


# ---------------- 基础结构 ----------------

def test_weeks1_empty_transitions():
    """weeks=1：没有上一周可比较 → transitions=[]，不抛错。"""
    trend = aggregate_weekly_trend([_rec("a", 1, distance=3000.0)], weeks=1, now=NOW)
    delta = compute_weekly_trend_delta(trend)
    assert delta.transitions == []
    assert delta.anchor == NOW and delta.window_days == 7


def test_weeks2_single_transition_direction():
    """weeks=2：恰好 1 对环比，方向 older → newer（距离从旧周到新周）。"""
    recs = [
        _rec("old", days_ago=10, distance=5000.0),
        _rec("new", days_ago=2, distance=3000.0),
    ]
    delta = compute_weekly_trend_delta(
        aggregate_weekly_trend(recs, weeks=2, now=NOW)
    )
    assert len(delta.transitions) == 1
    t = delta.transitions[0]
    # older（baseline）是更早的窗口
    assert t.baseline_window_end == t.current_window_start  # 相邻连续
    assert t.total_distance.baseline == 5000.0
    assert t.total_distance.current == 3000.0
    assert t.total_distance.delta == -2000.0
    assert t.total_distance.percentage_change == -40.0


def test_weeks4_three_transitions_ordered():
    """weeks=4：3 对环比，按时间升序（最旧相邻对在前），窗口连续。"""
    recs = [
        _rec("w4", days_ago=25, distance=1000.0),
        _rec("w3", days_ago=15, distance=4000.0),
        _rec("w2", days_ago=10, distance=5000.0),
        _rec("w1", days_ago=2, distance=3000.0),
    ]
    delta = compute_weekly_trend_delta(
        aggregate_weekly_trend(recs, weeks=4, now=NOW)
    )
    assert len(delta.transitions) == 3
    ts = delta.transitions
    # 升序：每对 current 窗口起点依次变新
    starts = [t.current_window_start for t in ts]
    assert starts == sorted(starts)
    # 窗口连续：上一对的 current == 下一对的 baseline
    for i in range(2):
        assert ts[i].current_window_start == ts[i + 1].baseline_window_start
    # 第一对 baseline 是最旧窗（1000 → 4000），最后一对新周（5000 → 3000）
    assert ts[0].total_distance.baseline == 1000.0
    assert ts[0].total_distance.current == 4000.0
    assert ts[2].total_distance.baseline == 5000.0
    assert ts[2].total_distance.current == 3000.0


def test_all_metric_deltas():
    """七项必选指标 + HR 全部正确计算。"""
    recs = [
        # 旧周：1 条全字段
        _rec("old", days_ago=10, hours=2.0, distance=5000.0,
             calories=400.0, ascent=30.0, descent=25.0, steps=6000,
             avg_hr=150.0, max_hr=170.0),
        # 新周：2 条，其中 1 条仅距离
        _rec("new1", days_ago=3, hours=1.0, distance=3000.0,
             calories=200.0, ascent=10.0, descent=8.0, steps=3000,
             avg_hr=160.0, max_hr=180.0),
        _rec("new2", days_ago=4, hours=0.5, distance=1000.0),
    ]
    t = compute_weekly_trend_delta(
        aggregate_weekly_trend(recs, weeks=2, now=NOW)
    ).transitions[0]
    assert t.activity_count.delta == 1 and isinstance(t.activity_count.delta, int)
    assert t.total_distance.delta == -1000.0
    assert t.total_duration_seconds.delta == 5400.0 - 7200.0  # (1h+0.5h) - 2h
    assert t.total_calories.delta == -200.0
    assert t.total_steps.delta == -3000
    assert t.total_ascent.delta == -20.0 and t.total_descent.delta == -17.0
    # HR：new2 无 HR，avg 仍按有 HR 活动聚合（160）→ +10
    assert t.average_heart_rate.delta == 10.0
    assert t.max_heart_rate.delta == 10.0


def test_pct_positive_negative_zero():
    """percentage_change 正 / 负 / 零三种情形。"""
    recs = [
        _rec("o1", days_ago=10, distance=2000.0),
        _rec("n1", days_ago=3, distance=3000.0),   # +50%
    ]
    recs2 = [
        _rec("o1", days_ago=10, distance=3000.0),
        _rec("n1", days_ago=3, distance=3000.0),   # 0%
    ]
    up = compute_weekly_trend_delta(
        aggregate_weekly_trend(recs, weeks=2, now=NOW)
    ).transitions[0]
    assert up.total_distance.delta == 1000.0
    assert up.total_distance.percentage_change == 50.0
    flat = compute_weekly_trend_delta(
        aggregate_weekly_trend(recs2, weeks=2, now=NOW)
    ).transitions[0]
    assert flat.total_distance.delta == 0
    assert flat.total_distance.percentage_change == 0.0
    down = compute_weekly_trend_delta(
        aggregate_weekly_trend(recs, weeks=2, now=NOW)
    ).transitions[0]
    # 用同一数据反向构造负例已在 test_weeks2 验证 -40%
    assert up.total_distance.percentage_change > 0
    assert flat.total_distance.percentage_change == 0.0


def test_baseline_empty_week():
    """baseline 是空周：count 0=真实零 → delta 可算、pct=None；
    距离 None → delta=None。两种语义并存且区分。"""
    recs = [_rec("only", days_ago=2, distance=3000.0)]  # 只在新周
    t = compute_weekly_trend_delta(
        aggregate_weekly_trend(recs, weeks=2, now=NOW)
    ).transitions[0]
    assert t.activity_count.baseline == 0
    assert t.activity_count.delta == 1
    assert t.activity_count.percentage_change is None  # baseline==0 不除零
    assert t.total_distance.baseline is None
    assert t.total_distance.delta is None  # 缺失≠0
    # duration 契约：空周聚合为 0.0（真实零）→ delta 可算、pct=None
    assert t.total_duration_seconds.baseline == 0.0
    assert t.total_duration_seconds.delta == 3600.0
    assert t.total_duration_seconds.percentage_change is None


def test_current_empty_week():
    """current 是空周：count delta=-1；距离 delta=None。"""
    recs = [_rec("only", days_ago=10, distance=3000.0)]  # 只在旧周
    t = compute_weekly_trend_delta(
        aggregate_weekly_trend(recs, weeks=2, now=NOW)
    ).transitions[0]
    assert t.activity_count.delta == -1
    assert t.total_distance.delta is None


def test_middle_empty_week():
    """中间空周：两侧各自成对，均正确（空周 count=0 真实零语义）。"""
    recs = [
        _rec("old", days_ago=18, distance=5000.0),
        _rec("new", days_ago=2, distance=3000.0),
    ]
    delta = compute_weekly_trend_delta(
        aggregate_weekly_trend(recs, weeks=3, now=NOW)
    )
    assert len(delta.transitions) == 2
    t1, t2 = delta.transitions
    assert t1.total_distance.baseline == 5000.0 and t1.total_distance.current is None
    assert t1.activity_count.delta == -1
    assert t2.total_distance.baseline is None and t2.total_distance.current == 3000.0
    assert t2.activity_count.delta == 1


def test_hr_none_semantics():
    """HR 任一侧 None → delta=None；两侧有值 → 正常计算。"""
    recs = [
        _rec("old-hr", days_ago=10, avg_hr=150.0, max_hr=170.0),
        _rec("new-no-hr", days_ago=3),
    ]
    t = compute_weekly_trend_delta(
        aggregate_weekly_trend(recs, weeks=2, now=NOW)
    ).transitions[0]
    assert t.average_heart_rate.delta is None
    assert t.max_heart_rate.delta is None
    assert t.average_heart_rate.current is None  # 新周 HR 全缺


def test_count_int_preserved():
    """count 类 delta / baseline / current 保持 int。"""
    recs = [
        _rec("o", days_ago=10),
        _rec("n1", days_ago=3),
        _rec("n2", days_ago=4),
    ]
    t = compute_weekly_trend_delta(
        aggregate_weekly_trend(recs, weeks=2, now=NOW)
    ).transitions[0]
    assert t.activity_count.baseline == 1
    assert t.activity_count.current == 2
    assert t.activity_count.delta == 1
    assert isinstance(t.activity_count.delta, int)


def test_cross_validate_with_compare_metric():
    """与 Phase 6.9 compare_metric 逐指标交叉验证（零数学重复的实现保证）。"""
    recs = [
        _rec("o", days_ago=10, distance=5000.0, calories=400.0, steps=6000),
        _rec("n", days_ago=3, distance=6000.0, calories=500.0, steps=5000),
    ]
    trend = aggregate_weekly_trend(recs, weeks=2, now=NOW)
    t = compute_weekly_trend_delta(trend).transitions[0]
    b, c = trend.weeks[0].summary, trend.weeks[1].summary
    assert t.total_distance == compare_metric(b.total_distance, c.total_distance)
    assert t.total_calories == compare_metric(b.total_calories, c.total_calories)
    assert t.total_steps == compare_metric(b.total_steps, c.total_steps)
    assert t.activity_count == compare_metric(b.activity_count, c.activity_count)


def test_boundary_activity_inherits_trend_windows():
    """边界活动归属继承 6.10：恰在共享边界的活动归较新窗，环比随之正确。"""
    edge = NOW - timedelta(days=7)
    recs = [ActivityRecord(
        activity_id="edge", activity_type=56,
        start_time=edge, end_time=edge + timedelta(hours=1), distance=1000.0,
    )]
    t = compute_weekly_trend_delta(
        aggregate_weekly_trend(recs, weeks=2, now=NOW)
    ).transitions[0]
    # edge 归新窗（>= 新窗 start），旧窗空
    assert t.activity_count.baseline == 0 and t.activity_count.current == 1


def test_same_anchor_and_window_semantics():
    """窗口边界完全继承 trend（同一锚点半开区间），不重新定义。"""
    trend = aggregate_weekly_trend([], weeks=3, now=NOW)
    delta = compute_weekly_trend_delta(trend)
    assert delta.anchor == trend.anchor == NOW
    for t in delta.transitions:
        assert t.baseline_window_start < t.baseline_window_end
        assert t.current_window_start < t.current_window_end
        assert t.baseline_window_end == t.current_window_start  # 连续不重叠


def test_input_not_mutated():
    recs = [_rec("a", days_ago=2, distance=3000.0)]
    trend = aggregate_weekly_trend(recs, weeks=2, now=NOW)
    before = trend.model_dump()
    compute_weekly_trend_delta(trend)
    assert trend.model_dump() == before
    before_rec = recs[0].model_dump()
    compute_weekly_trend_delta(aggregate_weekly_trend(recs, weeks=2, now=NOW))
    assert recs[0].model_dump() == before_rec


# ---------------- MCP tool ----------------

def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _raw(rid: str, days_ago: float, distance: float | None = None) -> dict:
    rec: dict[str, Any] = {
        "id": rid, "activityType": 56,
        "startTime": _ms(NOW - timedelta(days=days_ago)),
        "endTime": _ms(NOW - timedelta(days=days_ago) + timedelta(hours=1)),
    }
    if distance is not None:
        rec["activitySummary"] = {"dataSummary": [
            {"dataTypeName": "com.huawei.continuous.distance.total",
             "value": [{"fieldName": "distance", "floatValue": distance}]},
        ]}
    return rec


def _mock_client(body: Any, captured: list | None = None) -> HTCClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if captured is not None:
            captured.append(json.loads(request.content))
        return httpx.Response(200, json=body)

    return HTCClient(
        headers={},
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_tool_full_chain():
    """tool 全链路：单次请求 → 一次聚合 → 环比 → 序列化。"""
    body = [
        _raw("old", days_ago=10, distance=5000.0),
        _raw("new", days_ago=2, distance=3000.0),
        _raw("out", days_ago=60, distance=99999.0),
    ]
    captured: list[dict] = []
    mock = _mock_client(body, captured)
    with patch("src.huawei_health_mcp.server.create_htc_client", return_value=mock), \
         patch("src.huawei_health_mcp.server.datetime", _FixedDatetime):
        result = get_training_weekly_trend_delta(weeks=2, limit=100)

    assert result["anchor"] is not None and result["window_days"] == 7
    assert len(result["transitions"]) == 1
    t = result["transitions"][0]
    assert t["total_distance"]["baseline"] == 5000.0
    assert t["total_distance"]["current"] == 3000.0
    assert t["total_distance"]["delta"] == -2000.0
    assert t["activity_count"]["delta"] == 0  # old/new 各 1 条
    # 单次请求整个跨度 [now-14d, now)
    assert len(captured) == 1
    assert int(captured[0]["startTime"]) == _ms(NOW - timedelta(days=14))
    assert int(captured[0]["endTime"]) == _ms(NOW)


def test_tool_weeks1_returns_empty_transitions():
    mock = _mock_client([_raw("a", days_ago=2, distance=1000.0)])
    with patch("src.huawei_health_mcp.server.create_htc_client", return_value=mock):
        result = get_training_weekly_trend_delta(weeks=1)
    assert result["transitions"] == []


def test_tool_invalid_params():
    for bad_weeks in (0, -1, MAX_TREND_WEEKS + 1):
        with pytest.raises(ValueError):
            get_training_weekly_trend_delta(weeks=bad_weeks)
    for bad_limit in (0, -5, 101):
        with pytest.raises(ValueError):
            get_training_weekly_trend_delta(limit=bad_limit)


def test_tool_empty_response_legal():
    mock = _mock_client([])
    with patch("src.huawei_health_mcp.server.create_htc_client", return_value=mock):
        result = get_training_weekly_trend_delta(weeks=3)
    assert len(result["transitions"]) == 2
    for t in result["transitions"]:
        assert t["activity_count"]["baseline"] == 0
        assert t["activity_count"]["delta"] == 0  # int 0，真实零语义
        assert t["total_distance"]["delta"] is None  # 缺失语义


# ---------------- 真实 fixture（SYNTHETIC / 解析归窗级验证） ----------------

def test_real_records_parse_window_and_delta_structure():
    """真实数据：解析 → 归窗 → 环比结构（AUTOMATED VERIFIED，非真实多周趋势）。

    fixture 实际跨度 1.43 天（Phase 6.10 已报告），锚点=最晚 end+1s 时
    全部 5 条落最近窗 → 唯一相邻对 = 空周 → 5 条周：
    count delta=+5、pct=None（baseline 0 不除零）、距离 delta=None。
    """
    if not REAL_RECORDS.exists():
        pytest.skip("real fixture 不存在（data_temp 为本地隐私数据）")
    from src.huawei_health_mcp.parser import parse_activity_record

    raw = json.loads(REAL_RECORDS.read_text(encoding="utf-8"))
    records = [parse_activity_record(r) for r in raw]
    anchor = max(r.end_time for r in records) + timedelta(seconds=1)

    delta = compute_weekly_trend_delta(
        aggregate_weekly_trend(records, weeks=2, now=anchor)
    )
    assert len(delta.transitions) == 1
    t = delta.transitions[0]
    assert t.activity_count.baseline == 0
    assert t.activity_count.current == 5
    assert t.activity_count.delta == 5
    assert t.activity_count.percentage_change is None
    assert t.total_distance.delta is None  # 旧周距离缺失≠0
    assert t.current_window_end == anchor


def test_real_records_synthetic_multiweek_delta():
    """SYNTHETIC COMPARISON VALIDATION：真实记录人工拆两个周窗口做环比。

    锚点 = 第 3 早记录 start + 7d：旧窗 2 条、新窗 3 条（Phase 6.10 已
    验证的拆分），环比与手写 compare_metric 一致；不伪装成真实历史周环比。
    """
    if not REAL_RECORDS.exists():
        pytest.skip("real fixture 不存在")
    from src.huawei_health_mcp.parser import parse_activity_record

    raw = json.loads(REAL_RECORDS.read_text(encoding="utf-8"))
    records = sorted(
        (parse_activity_record(r) for r in raw), key=lambda r: r.start_time
    )
    anchor = records[2].start_time + timedelta(days=7)
    trend = aggregate_weekly_trend(records, weeks=2, now=anchor)
    delta = compute_weekly_trend_delta(trend)

    t = delta.transitions[0]
    b, c = trend.weeks[0].summary, trend.weeks[1].summary
    # 手写交叉验证：与直接对两个 summary 调 compare_metric 完全一致
    assert t.total_distance == compare_metric(b.total_distance, c.total_distance)
    assert t.total_calories == compare_metric(b.total_calories, c.total_calories)
    assert t.total_steps == compare_metric(b.total_steps, c.total_steps)
    # 计数方向：older 2 条 → newer 3 条
    assert t.activity_count.baseline == 2 and t.activity_count.current == 3
    assert t.activity_count.delta == 1
    # 环比口径与总量守恒：两窗合计精确值不变（Phase 6.13 重新核对）
    assert b.total_distance + c.total_distance == 22570.0
