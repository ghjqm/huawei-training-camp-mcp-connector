"""周趋势层测试（Phase 6.10）：build_week_windows / aggregate_weekly_trend + MCP tool。

全部 synthetic 或真实 fixture 离线归窗，不联网、无真实凭证。
真实 fixture 仅做"真实数据解析 / 归窗验证"（一次列表快照不伪装成
真实历史周趋势），跨周趋势逻辑由 synthetic 测试验证。
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
from src.huawei_health_mcp.server import (
    MAX_TREND_WEEKS,
    fetch_weekly_trend,
    get_training_weekly_trend,
)
from src.huawei_health_mcp.trend_analysis import (
    aggregate_weekly_trend,
    build_week_windows,
)

REAL_RECORDS = Path(__file__).parent.parent / "data_temp" / "real_activity_records.json"

NOW = datetime(2026, 8, 16, 12, 0, 0, tzinfo=timezone.utc)


class _FixedDatetime(datetime):
    """now() 恒返回 NOW 的 datetime 替身（其余行为不变）。"""

    @classmethod
    def now(cls, tz=None):  # noqa: ARG003
        return NOW


def _at(days_ago: float) -> datetime:
    """NOW - days_ago 天。"""
    return NOW - timedelta(days=days_ago)


def _rec(
    rid: str, days_ago: float, hours: float = 1.0, activity_type: int = 56,
    distance: float | None = None, avg_hr: float | None = None,
    max_hr: float | None = None, calories: float | None = None,
    ascent: float | None = None, descent: float | None = None,
    steps: int | None = None, start_time: datetime | None = None,
) -> ActivityRecord:
    return ActivityRecord(
        activity_id=rid, activity_type=activity_type,
        start_time=start_time if start_time is not None else _at(days_ago),
        end_time=(start_time if start_time is not None else _at(days_ago)) + timedelta(hours=hours),
        distance=distance, avg_heart_rate=avg_hr, max_heart_rate=max_hr,
        calories=calories, ascent=ascent, descent=descent, steps=steps,
    )


# ---------------- 窗口构造：同一锚点 / 连续 / 不重叠 ----------------

def test_build_windows_contiguous_non_overlap():
    """4 个窗口：升序、连续（前窗 end == 后窗 start）、最旧起点 = now-28d。"""
    windows = build_week_windows(4, NOW)
    assert len(windows) == 4
    assert windows[0][0] == NOW - timedelta(days=28)
    assert windows[-1][1] == NOW  # 最 recent 窗口右端 = 锚点
    for i in range(3):
        assert windows[i][1] == windows[i + 1][0]  # 连续且不重叠


@pytest.mark.parametrize("weeks", [1, 2, 4, 13, MAX_TREND_WEEKS])
def test_build_windows_parametrated(weeks):
    """不同 weeks：数量正确，跨度 = weeks * 7 天。"""
    windows = build_week_windows(weeks, NOW)
    assert len(windows) == weeks
    assert windows[0][0] == NOW - timedelta(days=weeks * 7)


def test_build_windows_invalid_weeks():
    with pytest.raises(ValueError):
        build_week_windows(0, NOW)
    with pytest.raises(ValueError):
        build_week_windows(-3, NOW)


def test_same_anchor_used_for_all_windows():
    """所有窗口来自同一 anchor（结构断言：趋势对象记录 anchor）。"""
    trend = aggregate_weekly_trend([], weeks=4, now=NOW)
    assert trend.anchor == NOW
    assert trend.window_days == 7
    assert trend.weeks[0].window_start == NOW - timedelta(days=28)


# ---------------- 归窗语义 ----------------

def test_empty_records():
    """空数据：weeks 个空 summary（count=0，distance=None），不抛错。"""
    trend = aggregate_weekly_trend([], weeks=3, now=NOW)
    assert len(trend.weeks) == 3
    for w in trend.weeks:
        assert w.summary.activity_count == 0
        assert w.summary.total_distance is None
        assert w.summary.activity_types == []


def test_records_assigned_to_correct_weeks():
    """多周分布：各记录按自身 start_time 归属唯一窗口。"""
    recs = [
        _rec("w1", days_ago=2, distance=3000.0),    # 最新窗口
        _rec("w2", days_ago=8, distance=5000.0),    # 次新
        _rec("w3", days_ago=15, distance=4000.0),   # 次旧
        _rec("w4", days_ago=25, distance=1000.0),   # 最旧
    ]
    trend = aggregate_weekly_trend(recs, weeks=4, now=NOW)
    counts = [w.summary.activity_count for w in trend.weeks]
    assert counts == [1, 1, 1, 1]  # 升序：最旧→最新各 1 条
    assert trend.weeks[0].summary.total_distance == 1000.0
    assert trend.weeks[3].summary.total_distance == 3000.0


def test_activity_exactly_on_boundary_goes_to_newer_week():
    """恰在共享边界（now-7d）上的活动只归较新窗口。"""
    edge = NOW - timedelta(days=7)
    recs = [_rec("edge", days_ago=0, start_time=edge)]
    trend = aggregate_weekly_trend(recs, weeks=2, now=NOW)
    assert trend.weeks[0].summary.activity_count == 0  # edge >= 旧窗 end → 排除
    assert trend.weeks[1].summary.activity_count == 1  # edge >= 新窗 start → 归入


def test_out_of_order_records():
    """乱序输入：归窗结果与顺序无关。"""
    recs = [
        _rec("late", days_ago=1, distance=3000.0),
        _rec("early", days_ago=10, distance=5000.0),
        _rec("mid", days_ago=5, distance=2000.0),
    ]
    trend = aggregate_weekly_trend(recs, weeks=2, now=NOW)
    assert trend.weeks[0].summary.total_distance == 5000.0
    assert trend.weeks[1].summary.total_distance == 5000.0  # 3000+2000


def test_records_outside_span_or_missing_start_excluded():
    """跨度之外 / start_time 缺失的记录被排除，不进任何窗口。"""
    recs = [
        _rec("too-old", days_ago=40),          # 超出 4 周
        _rec("future", days_ago=-1),           # start >= now（半开区间外）
        ActivityRecord(activity_id="no-time", activity_type=56),
    ]
    trend = aggregate_weekly_trend(recs, weeks=4, now=NOW)
    assert all(w.summary.activity_count == 0 for w in trend.weeks)


def test_single_week():
    """单周窗口：全部记录落入 [now-7d, now)。"""
    recs = [
        _rec("a", days_ago=1, distance=5000.0),
        _rec("b", days_ago=6, distance=3000.0),
    ]
    trend = aggregate_weekly_trend(recs, weeks=1, now=NOW)
    assert len(trend.weeks) == 1
    assert trend.weeks[0].summary.activity_count == 2
    assert trend.weeks[0].summary.total_distance == 8000.0


# ---------------- 每周聚合字段（继承 6.7/6.8 契约） ----------------

def test_week_aggregates_all_fields():
    """distance / duration / calories / steps / ascent / descent 全聚合。"""
    recs = [
        _rec(
            "a", days_ago=1, hours=2.0, distance=5000.0,
            calories=400.0, ascent=30.0, descent=25.0, steps=6000,
        ),
        _rec(
            "b", days_ago=3, hours=1.0, distance=3000.0,
            calories=200.0, ascent=10.0, descent=8.0, steps=3000,
        ),
    ]
    w = aggregate_weekly_trend(recs, weeks=1, now=NOW).weeks[0].summary
    assert w.activity_count == 2
    assert w.total_distance == 8000.0
    assert w.total_duration_seconds == 10800.0  # 2h+1h，含间歇总历时
    assert w.total_calories == 600.0
    assert w.total_steps == 9000
    assert w.total_ascent == 40.0 and w.total_descent == 33.0
    assert w.calories_activity_count == 2
    assert w.ascent_activity_count == 2 and w.steps_activity_count == 2


def test_missing_fields_none_semantics():
    """缺失字段 None（不伪造 0），*_activity_count 标记有效数。"""
    recs = [_rec("a", days_ago=1, distance=5000.0)]  # 其余全缺
    w = aggregate_weekly_trend(recs, weeks=1, now=NOW).weeks[0].summary
    assert w.total_calories is None and w.calories_activity_count == 0
    assert w.total_ascent is None and w.total_ascent is None
    assert w.total_steps is None and w.steps_activity_count == 0
    assert w.total_distance == 5000.0 and w.distance_activity_count == 1


def test_hr_availability_preserved():
    """HR 随内嵌 summary 保留：hr_activity_count 标记有效数（设备来源契约）。"""
    recs = [
        _rec("with-hr", days_ago=1, avg_hr=150.0, max_hr=170.0),
        _rec("no-hr", days_ago=2),
    ]
    w = aggregate_weekly_trend(recs, weeks=1, now=NOW).weeks[0].summary
    assert w.hr_activity_count == 1
    assert w.average_heart_rate == 150.0
    assert w.max_heart_rate == 170.0


def test_multi_activity_type_grouping():
    """activity_types 分组随内嵌 summary 保真（int 原样，不命名）。"""
    recs = [
        _rec("a", days_ago=1, activity_type=56, distance=5000.0),
        _rec("b", days_ago=2, activity_type=90, distance=3000.0),
        _rec("c", days_ago=3, activity_type=56, distance=2000.0),
    ]
    w = aggregate_weekly_trend(recs, weeks=1, now=NOW).weeks[0].summary
    by_type = {t.activity_type: t for t in w.activity_types}
    assert by_type[56].count == 2 and by_type[56].total_distance == 7000.0
    assert by_type[90].count == 1 and by_type[90].total_distance == 3000.0


def test_input_not_mutated():
    recs = [_rec("a", days_ago=1, distance=5000.0)]
    before = [r.model_dump() for r in recs]
    aggregate_weekly_trend(recs, weeks=4, now=NOW)
    assert [r.model_dump() for r in recs] == before


# ---------------- MCP tool ----------------

def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _raw(rid: str, start: datetime, end: datetime, distance: float | None = None) -> dict:
    rec: dict[str, Any] = {
        "id": rid, "activityType": 56,
        "startTime": _ms(start), "endTime": _ms(end),
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


def test_tool_full_chain_single_request():
    """tool 全链路：单次列表请求整个跨度 → 本地归窗 → 每周聚合 → 序列化。"""
    body = [
        _raw("cur", NOW - timedelta(days=2), NOW - timedelta(days=2, hours=-1), 3000.0),
        _raw("base", NOW - timedelta(days=10), NOW - timedelta(days=10, hours=-1), 5000.0),
        _raw("out", NOW - timedelta(days=60), NOW - timedelta(days=60, hours=-1), 99999.0),
    ]
    captured: list[dict] = []
    mock = _mock_client(body, captured)
    with patch("src.huawei_health_mcp.server.create_htc_client", return_value=mock), \
         patch("src.huawei_health_mcp.server.datetime", _FixedDatetime):
        result = get_training_weekly_trend(weeks=4, limit=100)

    assert len(result["weeks"]) == 4
    assert result["window_days"] == 7
    counts = [w["summary"]["activity_count"] for w in result["weeks"]]
    assert counts == [0, 0, 1, 1]  # 升序：10 天前归 week[2]，2 天前归 week[3]
    assert result["weeks"][3]["summary"]["total_distance"] == 3000.0
    assert result["weeks"][2]["summary"]["total_distance"] == 5000.0
    # 单次请求，时间范围 = [now-28d, now]，同一锚点
    assert len(captured) == 1
    assert int(captured[0]["startTime"]) == _ms(NOW - timedelta(days=28))
    assert int(captured[0]["endTime"]) == _ms(NOW)
    assert captured[0]["limit"] == 100


def test_tool_invalid_params():
    """非法 weeks / limit 抛 ValueError，不发请求。"""
    for bad_weeks in (0, -1, MAX_TREND_WEEKS + 1, 9999):
        with pytest.raises(ValueError):
            get_training_weekly_trend(weeks=bad_weeks)
    for bad_limit in (0, -5, 101):
        with pytest.raises(ValueError):
            get_training_weekly_trend(limit=bad_limit)


def test_tool_weeks_upper_bound_legal():
    """边界 weeks=104（728 天 ≤ 730）合法。"""
    captured: list[dict] = []
    mock = _mock_client([], captured)
    with patch("src.huawei_health_mcp.server.create_htc_client", return_value=mock), \
         patch("src.huawei_health_mcp.server.datetime", _FixedDatetime):
        result = get_training_weekly_trend(weeks=MAX_TREND_WEEKS)
    assert len(result["weeks"]) == MAX_TREND_WEEKS
    assert int(captured[0]["startTime"]) == _ms(NOW - timedelta(days=728))


def test_tool_empty_response_legal():
    mock = _mock_client([])
    with patch("src.huawei_health_mcp.server.create_htc_client", return_value=mock):
        result = get_training_weekly_trend()
    assert len(result["weeks"]) == 4
    assert all(w["summary"]["activity_count"] == 0 for w in result["weeks"])
    assert all(w["summary"]["total_distance"] is None for w in result["weeks"])


def test_fetch_validates_before_request():
    captured: list[dict] = []
    mock = _mock_client([], captured)
    with pytest.raises(ValueError):
        fetch_weekly_trend(mock, weeks=0)
    with pytest.raises(ValueError):
        fetch_weekly_trend(mock, limit=101)
    assert captured == []


# ---------------- 真实 fixture：解析 / 归窗验证 ----------------

def test_real_records_parse_and_window_assignment():
    """真实数据解析 + 归窗验证（非真实历史周趋势：fixture 是一次列表快照）。

    策略：锚点 = 最晚 end_time + 1s（全部 5 条必落最近窗口），
    与 Phase 6.8 精确总量交叉验证；再显式报告 fixture 实际时间范围。
    """
    if not REAL_RECORDS.exists():
        pytest.skip("real fixture 不存在（data_temp 为本地隐私数据）")
    from src.huawei_health_mcp.parser import parse_activity_record

    raw = json.loads(REAL_RECORDS.read_text(encoding="utf-8"))
    records = [parse_activity_record(r) for r in raw]
    assert all(r.start_time is not None for r in records)

    span_days = (max(r.end_time for r in records) - min(r.start_time for r in records)).total_seconds() / 86400
    print(f"real fixture span: {span_days:.2f} days")
    anchor = max(r.end_time for r in records) + timedelta(seconds=1)

    trend = aggregate_weekly_trend(records, weeks=2, now=anchor)
    old, new = trend.weeks
    # 锚点 = 最晚 end + 1s → 全部记录落最近窗口（数据来自 7 天列表查询）
    assert old.summary.activity_count == 0
    assert new.summary.activity_count == 5
    # 与 Phase 6.8 已知精确总量交叉验证
    assert new.summary.total_distance == 20400.0
    assert new.summary.total_calories == 917.0
    assert new.summary.total_ascent == 70.6 and new.summary.total_descent == 65.7
    assert new.summary.total_steps == 26769
    by_type = {t.activity_type: t.count for t in new.summary.activity_types}
    assert by_type == {90: 4, 56: 1}


def test_real_records_split_across_weeks_synthetic():
    """跨周趋势逻辑验证（SYNTHETIC）：真实记录按时间人工拆到两个周窗口。

    锚点取中位分割：前 2 条归旧窗、后 3 条归新窗，两窗计数与距离
    之和与整体精确值一致（不伪装成真实历史周趋势）。
    """
    if not REAL_RECORDS.exists():
        pytest.skip("real fixture 不存在")
    from src.huawei_health_mcp.parser import parse_activity_record

    raw = json.loads(REAL_RECORDS.read_text(encoding="utf-8"))
    records = sorted(
        (parse_activity_record(r) for r in raw), key=lambda r: r.start_time
    )
    # 锚点：旧窗 = [r2-7d, r2)，新窗 = [r2, r2+7d)——r2 恰在边界归新窗；
    # r0/r1 均晚于 r2-7d（fixture 来自 7 天列表查询），必落旧窗
    anchor = records[2].start_time + timedelta(days=7)
    trend = aggregate_weekly_trend(records, weeks=2, now=anchor)
    old, new = trend.weeks
    assert old.summary.activity_count == 2
    assert new.summary.activity_count == 3
    # 两窗合计 = Phase 6.8 精确总量
    assert old.summary.total_distance + new.summary.total_distance == 20400.0
    assert old.summary.total_calories + new.summary.total_calories == 917.0
    assert old.summary.total_steps + new.summary.total_steps == 26769
