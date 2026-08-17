"""周期对比层测试（Phase 6.9）：compare_training_periods + MCP tool。

全部 synthetic 或真实 fixture 离线对比，不联网、无真实凭证。
真实 fixture 的对比是 SYNTHETIC COMPARISON VALIDATION：从真实记录
按时间拆成两个合成周期，只验证“真实数据→聚合→对比”链路，
不代表真实历史周期对比。
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
from src.huawei_health_mcp.period_analysis import aggregate_activity_period
from src.huawei_health_mcp.period_comparison import (
    compare_metric,
    compare_training_periods,
)
from src.huawei_health_mcp.server import (
    get_training_period_comparison,
    fetch_period_comparison,
)

REAL_RECORDS = Path(__file__).parent.parent / "data_temp" / "real_activity_records.json"

# 固定“现在”锚点：让窗口边界测试完全确定（通过 monkeypatch server.datetime）
_FIXED_NOW = datetime(2026, 8, 16, 12, 0, 0, tzinfo=timezone.utc)


class _FixedDatetime(datetime):
    """now() 恒返回 _FIXED_NOW 的 datetime 替身（其余行为不变）。"""

    @classmethod
    def now(cls, tz=None):  # noqa: ARG003 - 测试替身签名需与 datetime.now 一致
        return _FIXED_NOW


def _t(h: int, m: int = 0) -> datetime:
    return datetime(2026, 8, 10, h, m, tzinfo=timezone.utc)


def _rec(
    rid: str, start: datetime, end: datetime, activity_type: int = 56,
    distance: float | None = None,
    avg_hr: float | None = None, max_hr: float | None = None,
    calories: float | None = None, ascent: float | None = None,
    descent: float | None = None, steps: int | None = None,
) -> ActivityRecord:
    return ActivityRecord(
        activity_id=rid, activity_type=activity_type,
        start_time=start, end_time=end, distance=distance,
        avg_heart_rate=avg_hr, max_heart_rate=max_hr,
        calories=calories, ascent=ascent, descent=descent, steps=steps,
    )


# ---------------- compare_metric：数学契约 ----------------

@pytest.mark.parametrize(
    "baseline,current,delta,pct",
    [
        (None, None, None, None),          # 双缺：全部 None
        (None, 5.0, None, None),           # 基线缺失：不得把 None 当 0
        (5.0, None, None, None),           # 当前缺失
        (0.0, 5.0, 5.0, None),             # 基线真实 0：delta 可算，不除零
        (5.0, 0.0, -5.0, -100.0),          # 当前真实 0
        (100.0, 150.0, 50.0, 50.0),        # 常规增长
        (100, 150, 50, 50.0),              # int 输入 delta 保持 int
    ],
)
def test_compare_metric_contract(baseline, current, delta, pct):
    m = compare_metric(baseline, current)
    assert m.baseline == baseline
    assert m.current == current
    assert m.delta == delta
    assert m.percentage_change == pct


# ---------------- compare_training_periods ----------------

def test_identical_periods_zero_delta():
    """两个完全相同的周期：所有 delta=0、pct=0.0。"""
    recs = [_rec("a", _t(6), _t(7), 56, 5000.0, 150.0, 170.0, 300.0, 10.0, 8.0, 6000)]
    c = compare_training_periods(
        aggregate_activity_period(recs), aggregate_activity_period(recs)
    )
    for name in (
        "activity_count", "total_distance", "total_calories", "total_ascent",
        "total_descent", "total_steps", "total_duration_seconds",
        "average_heart_rate", "max_heart_rate",
    ):
        m = getattr(c, name)
        assert m.delta == 0, name
        assert m.percentage_change == 0.0, name


def test_distance_increase():
    b = aggregate_activity_period([_rec("a", _t(6), _t(7), 56, 5000.0)])
    cur = aggregate_activity_period([_rec("b", _t(6), _t(7), 56, 6000.0)])
    m = compare_training_periods(b, cur).total_distance
    assert m.baseline == 5000.0 and m.current == 6000.0
    assert m.delta == 1000.0 and m.percentage_change == 20.0


def test_distance_decrease():
    b = aggregate_activity_period([_rec("a", _t(6), _t(7), 56, 5000.0)])
    cur = aggregate_activity_period([_rec("b", _t(6), _t(7), 56, 4000.0)])
    m = compare_training_periods(b, cur).total_distance
    assert m.delta == -1000.0 and m.percentage_change == -20.0


def test_duration_growth():
    b = aggregate_activity_period([_rec("a", _t(6), _t(7))])       # 3600s
    cur = aggregate_activity_period([_rec("b", _t(6), _t(8))])     # 7200s
    m = compare_training_periods(b, cur).total_duration_seconds
    assert m.delta == 3600.0 and m.percentage_change == 100.0


def test_calories_growth():
    b = aggregate_activity_period([_rec("a", _t(6), _t(7), calories=300.0)])
    cur = aggregate_activity_period([_rec("b", _t(6), _t(7), calories=400.0)])
    m = compare_training_periods(b, cur).total_calories
    assert m.delta == 100.0
    assert m.percentage_change == pytest.approx(100.0 / 3)


def test_steps_growth_int_preserved():
    b = aggregate_activity_period([_rec("a", _t(6), _t(7), steps=6000)])
    cur = aggregate_activity_period([_rec("b", _t(6), _t(7), steps=8000)])
    m = compare_training_periods(b, cur).total_steps
    assert m.delta == 2000
    assert isinstance(m.delta, int)  # count 类 delta 类型保真
    assert m.percentage_change == pytest.approx(100.0 / 3)


def test_ascent_descent_change():
    b = aggregate_activity_period([_rec("a", _t(6), _t(7), ascent=30.0, descent=25.0)])
    cur = aggregate_activity_period([_rec("b", _t(6), _t(7), ascent=40.0, descent=20.0)])
    c = compare_training_periods(b, cur)
    assert c.total_ascent.delta == 10.0
    assert c.total_descent.delta == -5.0


def test_multi_field_simultaneous():
    b = aggregate_activity_period([
        _rec("a", _t(6), _t(7), 56, 5000.0, 150.0, 170.0, 300.0, steps=6000),
    ])
    cur = aggregate_activity_period([
        _rec("b", _t(6), _t(7), 56, 8000.0, 155.0, 175.0, 500.0, steps=9000),
        _rec("c", _t(9), _t(10), 56, 2000.0),
    ])
    c = compare_training_periods(b, cur)
    assert c.activity_count.delta == 1
    assert c.total_distance.delta == 5000.0
    assert c.total_calories.delta == 200.0
    assert c.total_steps.delta == 3000
    assert c.average_heart_rate.delta == pytest.approx(5.0)
    assert c.max_heart_rate.delta == 5.0


def test_baseline_empty_period():
    """基线为空周期：count 是真实 0 → delta 可算、pct=None；
    距离是 None（无数据）→ delta=None。两种语义并存且区分。"""
    b = aggregate_activity_period([])
    cur = aggregate_activity_period([
        _rec("a", _t(6), _t(7), 56, 3000.0),
        _rec("c", _t(9), _t(10), 56, 2000.0),
    ])
    c = compare_training_periods(b, cur)
    assert c.activity_count.baseline == 0 and c.activity_count.delta == 2
    assert c.activity_count.percentage_change is None  # 不除零
    assert c.total_distance.baseline is None
    assert c.total_distance.delta is None  # 无法证明基线距离是 0
    # duration 聚合契约是 0.0（非 None）：真实零语义，delta 可算
    assert c.total_duration_seconds.baseline == 0.0
    assert c.total_duration_seconds.delta == 7200.0
    assert c.total_duration_seconds.percentage_change is None


def test_baseline_metric_none():
    """基线某指标缺失、当前存在：delta=None（不把 None 当 0）。"""
    b = aggregate_activity_period([_rec("a", _t(6), _t(7))])            # 无距离
    cur = aggregate_activity_period([_rec("b", _t(6), _t(7), 56, 5000.0)])
    m = compare_training_periods(b, cur).total_distance
    assert m.baseline is None and m.current == 5000.0
    assert m.delta is None and m.percentage_change is None


def test_current_metric_none():
    b = aggregate_activity_period([_rec("a", _t(6), _t(7), 56, 5000.0)])
    cur = aggregate_activity_period([_rec("b", _t(6), _t(7))])
    m = compare_training_periods(b, cur).total_distance
    assert m.baseline == 5000.0 and m.current is None
    assert m.delta is None and m.percentage_change is None


def test_hr_both_present():
    b = aggregate_activity_period([_rec("a", _t(6), _t(7), avg_hr=150.0, max_hr=170.0)])
    cur = aggregate_activity_period([_rec("b", _t(6), _t(7), avg_hr=160.0, max_hr=180.0)])
    c = compare_training_periods(b, cur)
    assert c.average_heart_rate.delta == 10.0
    assert c.average_heart_rate.percentage_change == pytest.approx(10.0 / 150.0 * 100)
    assert c.max_heart_rate.delta == 10.0


def test_hr_baseline_missing():
    """基线周期 HR 全缺：HR 对比为 None；当前期 HR 不被跨来源补全。"""
    b = aggregate_activity_period([_rec("a", _t(6), _t(7), 56, 5000.0)])
    cur = aggregate_activity_period([_rec("b", _t(6), _t(7), avg_hr=160.0, max_hr=180.0)])
    c = compare_training_periods(b, cur)
    for m in (c.average_heart_rate, c.max_heart_rate):
        assert m.baseline is None
        assert m.delta is None and m.percentage_change is None
    assert m.current is not None  # 当前值原样保留
    # 有效样本数随内嵌 summary 保真
    assert c.baseline_summary.hr_activity_count == 0
    assert c.current_summary.hr_activity_count == 1


def test_hr_current_missing():
    b = aggregate_activity_period([_rec("a", _t(6), _t(7), avg_hr=150.0, max_hr=170.0)])
    cur = aggregate_activity_period([_rec("b", _t(6), _t(7), 56, 5000.0)])
    c = compare_training_periods(b, cur)
    assert c.average_heart_rate.delta is None
    assert c.current_summary.hr_activity_count == 0
    assert c.baseline_summary.hr_activity_count == 1


def test_activity_counts_preserved_in_summary():
    """各 *_activity_count 不做对比展开，随内嵌 summary 原样保真。"""
    b = aggregate_activity_period([
        _rec("a", _t(6), _t(7), 56, 5000.0, avg_hr=150.0, calories=300.0),
    ])
    cur = aggregate_activity_period([
        _rec("b", _t(6), _t(7), 56, 3000.0),
        _rec("c", _t(9), _t(10), 56, calories=200.0, avg_hr=140.0),
    ])
    c = compare_training_periods(b, cur)
    assert c.baseline_summary.distance_activity_count == 1
    assert c.current_summary.distance_activity_count == 1
    assert c.current_summary.calories_activity_count == 1
    assert c.current_summary.hr_activity_count == 1
    # activity_types 分组同样保真
    assert len(c.current_summary.activity_types) == 1


def test_input_not_mutated():
    recs_b = [_rec("a", _t(6), _t(7), 56, 5000.0)]
    recs_c = [_rec("b", _t(6), _t(7), 56, 6000.0)]
    b, cur = aggregate_activity_period(recs_b), aggregate_activity_period(recs_c)
    dump_b, dump_c = b.model_dump(), cur.model_dump()
    compare_training_periods(b, cur)
    assert b.model_dump() == dump_b
    assert cur.model_dump() == dump_c


# ---------------- MCP tool 全链路 ----------------

def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _raw(
    rid: str, start: datetime, end: datetime, activity_type: int = 56,
    distance: float | None = None, avg_hr: float | None = None,
    max_hr: float | None = None,
) -> dict:
    """构造真实嵌套结构的原始列表记录（毫秒时间戳 + activitySummary）。"""
    data = []
    if distance is not None:
        data.append({
            "dataTypeName": "com.huawei.continuous.distance.total",
            "value": [{"fieldName": "distance", "floatValue": distance}],
        })
    if avg_hr is not None or max_hr is not None:
        values = []
        if avg_hr is not None:
            values.append({"fieldName": "avg", "floatValue": avg_hr})
        if max_hr is not None:
            values.append({"fieldName": "max", "floatValue": max_hr})
        data.append({
            "dataTypeName": "com.huawei.continuous.heart_rate.statistics",
            "value": values,
        })
    rec: dict[str, Any] = {
        "id": rid, "activityType": activity_type,
        "startTime": _ms(start), "endTime": _ms(end),
    }
    if data:
        rec["activitySummary"] = {"dataSummary": data}
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
    """tool 全链路：两个窗口请求 → parse → 窗口过滤 → 聚合 → 对比。

    mock 对两个窗口返回同一份 body（模拟服务端对历史区间行为宽松）：
    2 条落在当前窗、1 条落在基线窗、1 条在两窗之外——窗口过滤必须
    只按记录自身 start_time 归类/排除。
    """
    now = datetime.now(timezone.utc)
    body = [
        _raw("cur-1", now - timedelta(days=1), now - timedelta(days=1, hours=-1), 56, 3000.0, 150.0, 170.0),
        _raw("cur-2", now - timedelta(days=3), now - timedelta(days=3, hours=-1), 90, 2000.0),
        _raw("base-1", now - timedelta(days=8), now - timedelta(days=8, hours=-1), 56, 5000.0),
        _raw("out-1", now - timedelta(days=30), now - timedelta(days=30, hours=-1), 56, 99999.0),
    ]
    captured: list[dict] = []
    mock = _mock_client(body, captured)
    with patch("src.huawei_health_mcp.server.create_htc_client", return_value=mock):
        result = get_training_period_comparison(window_days=7, limit=100)

    # 窗口归类：当前 2 条 / 基线 1 条 / 窗外 1 条被排除
    assert result["activity_count"]["baseline"] == 1
    assert result["activity_count"]["current"] == 2
    assert result["activity_count"]["delta"] == 1
    assert result["total_distance"]["baseline"] == 5000.0
    assert result["total_distance"]["current"] == 5000.0
    assert result["total_distance"]["delta"] == 0
    assert result["average_heart_rate"]["current"] == 150.0
    # 两次请求、两窗连续不重叠（同一 now 锚点 → 边界毫秒值相等）
    assert len(captured) == 2
    b_start, b_end = int(captured[0]["startTime"]), int(captured[0]["endTime"])
    c_start, c_end = int(captured[1]["startTime"]), int(captured[1]["endTime"])
    assert b_start < b_end <= c_start < c_end
    assert b_end <= c_start
    # 展示窗口字段存在
    assert result["window_days"] == 7
    assert "baseline_window" in result and "current_window" in result


def test_tool_boundary_half_open():
    """半开区间 [start, end)：恰落在共享边界的记录只归当前窗。"""
    body = [
        # 正好在当前窗起点（= 基线窗终点）
        _raw("edge", _FIXED_NOW - timedelta(days=7), _FIXED_NOW - timedelta(days=7, hours=-1)),
    ]
    mock = _mock_client(body)
    with patch("src.huawei_health_mcp.server.create_htc_client", return_value=mock),          patch("src.huawei_health_mcp.server.datetime", _FixedDatetime):
        result = get_training_period_comparison(window_days=7)

    assert result["activity_count"]["baseline"] == 0  # edge >= 基线窗 end → 排除
    assert result["activity_count"]["current"] == 1   # edge >= 当前窗 start → 归入


def test_tool_payload_windows_exact():
    """固定锚点下，两次请求的时间范围精确为连续半开窗口。"""
    captured: list[dict] = []
    mock = _mock_client([], captured)
    with patch("src.huawei_health_mcp.server.create_htc_client", return_value=mock),          patch("src.huawei_health_mcp.server.datetime", _FixedDatetime):
        get_training_period_comparison(window_days=7)

    assert len(captured) == 2
    b, c = captured
    assert int(b["startTime"]) == _ms(_FIXED_NOW - timedelta(days=14))
    assert int(b["endTime"]) == _ms(_FIXED_NOW - timedelta(days=7))
    assert int(c["startTime"]) == _ms(_FIXED_NOW - timedelta(days=7))
    assert int(c["endTime"]) == _ms(_FIXED_NOW)
    assert b["limit"] == 100 and c["limit"] == 100


def test_tool_invalid_params():
    """非法 window_days / limit 抛 ValueError，不发任何请求。"""
    for bad_days in (0, -1, 366, 9999):
        with pytest.raises(ValueError):
            get_training_period_comparison(window_days=bad_days)
    for bad_limit in (0, -5, 101):
        with pytest.raises(ValueError):
            get_training_period_comparison(limit=bad_limit)


def test_tool_empty_response_legal():
    """两个窗口都为空：合法，返回空 summary 的对比，不 traceback。"""
    mock = _mock_client([])
    with patch("src.huawei_health_mcp.server.create_htc_client", return_value=mock):
        result = get_training_period_comparison()
    assert result["activity_count"]["baseline"] == 0
    assert result["activity_count"]["current"] == 0
    assert result["activity_count"]["delta"] == 0
    assert result["activity_count"]["percentage_change"] is None
    assert result["total_distance"]["delta"] is None
    assert result["total_duration_seconds"]["delta"] == 0


def test_fetch_validates_before_request():
    """fetch 层先校验参数：非法值抛 ValueError 且不发请求。"""
    captured: list[dict] = []
    mock = _mock_client([], captured)
    with pytest.raises(ValueError):
        fetch_period_comparison(mock, window_days=400)
    with pytest.raises(ValueError):
        fetch_period_comparison(mock, limit=0)
    assert captured == []


# ---------------- 真实 fixture 合成对比（SYNTHETIC COMPARISON VALIDATION） ----------------

def test_real_records_synthetic_period_comparison():
    """真实记录按时间拆成基线/当前两个合成周期，与手写计算交叉验证。

    注意：这不是真实历史周期对比（fixture 只是一次列表快照）。
    动态对拍：拆分计数、总量合计、分组合计全部从当前 fixture 手写推导，
    快照刷新不漂移。
    """
    if not REAL_RECORDS.exists():
        pytest.skip("real fixture 不存在（data_temp 为本地隐私数据）")
    from collections import Counter

    from src.huawei_health_mcp.parser import parse_activity_record

    raw = json.loads(REAL_RECORDS.read_text(encoding="utf-8"))
    records = sorted(
        (parse_activity_record(r) for r in raw), key=lambda r: r.start_time
    )
    if len(records) < 3:
        pytest.skip(f"real fixture 记录数 {len(records)} < 3，无法做 2/N 拆分")
    baseline_recs, current_recs = records[:2], records[2:]
    b = aggregate_activity_period(baseline_recs)
    cur = aggregate_activity_period(current_recs)
    c = compare_training_periods(b, cur)

    # 手写交叉验证（距离只聚合有效值）
    b_dist = sum(r.distance for r in baseline_recs if r.distance is not None)
    c_dist = sum(r.distance for r in current_recs if r.distance is not None)
    assert c.total_distance.baseline == pytest.approx(b_dist)
    assert c.total_distance.current == pytest.approx(c_dist)
    assert c.total_distance.delta == pytest.approx(c_dist - b_dist)
    if b_dist:
        assert c.total_distance.percentage_change == pytest.approx(
            (c_dist - b_dist) / b_dist * 100
        )

    # 计数与单位不变量：合计 = 手写全量求和（None 语义保真）
    assert c.activity_count.baseline == len(baseline_recs)
    assert c.activity_count.current == len(current_recs)
    assert c.activity_count.delta == len(current_recs) - len(baseline_recs)

    def _total(recs, attr):
        vals = [getattr(r, attr) for r in recs if getattr(r, attr) is not None]
        return sum(vals) if vals else None

    all_cal = _total(records, "calories")
    if all_cal is not None:
        assert b.total_calories + cur.total_calories == pytest.approx(all_cal)
        assert c.total_calories.delta == pytest.approx(
            cur.total_calories - b.total_calories
        )
    all_steps = _total(records, "steps")
    if all_steps is not None:
        assert b.total_steps + cur.total_steps == all_steps
    # activity_types 分组随内嵌 summary 保真：两周期分组合计 == 手写 Counter
    merged = Counter()
    for t in b.activity_types:
        merged[t.activity_type] += t.count
    for t in cur.activity_types:
        merged[t.activity_type] += t.count
    assert merged == Counter(r.activity_type for r in records)
