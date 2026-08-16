"""周期聚合层测试（Phase 6.7）：aggregate_activity_period + MCP tool。

全部 synthetic 或真实 fixture 离线聚合，不联网、无真实凭证。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from src.huawei_health_mcp.client import HTCClient
from src.huawei_health_mcp.models import ActivityRecord
from src.huawei_health_mcp.parser import parse_activity_record
from src.huawei_health_mcp.period_analysis import aggregate_activity_period
from src.huawei_health_mcp.server import get_training_period_summary

REAL_RECORDS = Path(__file__).parent.parent / "data_temp" / "real_activity_records.json"


def _rec(
    rid: str, start: datetime, end: datetime, activity_type: int = 56,
    distance: float | None = None,
    avg_hr: float | None = None, max_hr: float | None = None,
) -> ActivityRecord:
    return ActivityRecord(
        activity_id=rid, activity_type=activity_type,
        start_time=start, end_time=end, distance=distance,
        avg_heart_rate=avg_hr, max_heart_rate=max_hr,
    )


def _t(h: int, m: int = 0) -> datetime:
    return datetime(2026, 8, 10, h, m, tzinfo=timezone.utc)


# ---------------- 基础聚合 ----------------

def test_empty_records_legal():
    """空列表合法：全零/全 None，不抛错。"""
    s = aggregate_activity_period([])
    assert s.activity_count == 0
    assert s.total_distance is None and s.total_duration_seconds == 0.0
    assert s.period_start is None and s.period_end is None
    assert s.activity_types == []


def test_single_activity():
    """单活动：count=1，各指标等于该活动自身值。"""
    s = aggregate_activity_period([_rec("a", _t(6), _t(7), 56, 5000.0, 160.0, 180.0)])
    assert s.activity_count == 1
    assert s.total_distance == 5000.0
    assert s.total_duration_seconds == 3600.0
    assert s.average_distance == 5000.0
    assert s.max_distance == 5000.0
    assert s.average_duration_seconds == 3600.0
    assert s.max_duration_seconds == 3600.0
    assert s.period_start == _t(6) and s.period_end == _t(7)


def test_multiple_activities_totals():
    """多活动：total / average / max 距离与时长全部正确。"""
    recs = [
        _rec("a", _t(6), _t(7), 56, 5000.0),        # 3600s, 5000m
        _rec("b", _t(9), _t(9, 30), 56, 3000.0),    # 1800s, 3000m
        _rec("c", _t(12), _t(14), 90, 8000.0),      # 7200s, 8000m
    ]
    s = aggregate_activity_period(recs)
    assert s.activity_count == 3
    assert s.total_distance == 16000.0
    assert s.total_duration_seconds == 12600.0
    assert s.average_distance == pytest.approx(16000.0 / 3)
    assert s.max_distance == 8000.0
    assert s.average_duration_seconds == 4200.0
    assert s.max_duration_seconds == 7200.0


def test_distance_missing_excluded_from_aggregation():
    """距离缺失的活动不计入距离聚合，标记有效样本数。"""
    recs = [
        _rec("a", _t(6), _t(7), 56, 5000.0),
        _rec("b", _t(9), _t(10), 56),  # 无距离
    ]
    s = aggregate_activity_period(recs)
    assert s.activity_count == 2
    assert s.distance_activity_count == 1
    assert s.total_distance == 5000.0
    assert s.average_distance == 5000.0  # 只对有效样本平均
    assert s.duration_activity_count == 2


def test_period_time_range():
    """period_start=最早 start，period_end=最晚 end（含乱序输入）。"""
    recs = [
        _rec("late", _t(12), _t(13)),
        _rec("early", _t(5), _t(6)),
    ]
    s = aggregate_activity_period(recs)
    assert s.period_start == _t(5)
    assert s.period_end == _t(13)


# ---------------- HR 聚合 ----------------

def test_hr_aggregated_with_availability():
    """HR：avg 取各活动 avg 的平均，max 取各活动 max 的最大值。"""
    recs = [
        _rec("a", _t(6), _t(7), 56, avg_hr=150.0, max_hr=170.0),
        _rec("b", _t(9), _t(10), 56, avg_hr=170.0, max_hr=190.0),
    ]
    s = aggregate_activity_period(recs)
    assert s.average_heart_rate == pytest.approx(160.0)
    assert s.max_heart_rate == 190.0
    assert s.hr_activity_count == 2


def test_hr_missing_returns_none():
    """HR 全缺失：None + hr_activity_count=0，不伪造。"""
    s = aggregate_activity_period([_rec("a", _t(6), _t(7), 56, 100.0)])
    assert s.average_heart_rate is None
    assert s.max_heart_rate is None
    assert s.hr_activity_count == 0


def test_hr_partial_availability():
    """部分活动有 HR：只聚合有效活动，数量标记真实。"""
    recs = [
        _rec("a", _t(6), _t(7), 56, avg_hr=140.0, max_hr=160.0),
        _rec("b", _t(9), _t(10), 56),
    ]
    s = aggregate_activity_period(recs)
    assert s.average_heart_rate == 140.0
    assert s.hr_activity_count == 1


# ---------------- activityType 分组 ----------------

def test_activity_type_breakdown():
    """按原始 activityType 分组：count/距离/时长正确，类型值原样保留。"""
    recs = [
        _rec("a", _t(6), _t(7), 56, 5000.0),      # run-like: 3600s
        _rec("b", _t(9), _t(10), 56, 3000.0),     # 3600s
        _rec("c", _t(12), _t(13), 90, 8000.0),    # other: 3600s
    ]
    s = aggregate_activity_period(recs)
    assert len(s.activity_types) == 2
    by_type = {t.activity_type: t for t in s.activity_types}
    assert set(by_type) == {56, 90}
    assert by_type[56].count == 2
    assert by_type[56].total_distance == 8000.0
    assert by_type[56].total_duration_seconds == 7200.0
    assert by_type[90].count == 1
    assert by_type[90].total_distance == 8000.0


def test_type_breakdown_distance_missing_is_none():
    """某类型全部无距离时 total_distance=None（不是 0.0 伪造）。"""
    s = aggregate_activity_period([_rec("a", _t(6), _t(7), 90)])
    assert s.activity_types[0].total_distance is None


# ---------------- 输入不可变 ----------------

def test_input_not_mutated():
    """聚合不修改输入对象。"""
    recs = [_rec("a", _t(6), _t(7), 56, 100.0)]
    before = recs[0].model_dump()
    aggregate_activity_period(recs)
    assert recs[0].model_dump() == before


# ---------------- MCP tool ----------------

def _mock_period_client(body: Any) -> HTCClient:
    return HTCClient(
        headers={},
        client=httpx.Client(
            transport=httpx.MockTransport(lambda req: httpx.Response(200, json=body))
        ),
    )


def test_tool_full_chain():
    """tool 全链路：列表 → parse → 聚合 → 序列化。"""
    body = [
        {"id": "a", "activityType": 56,
         "startTime": 1786706473000, "endTime": 1786710073000,  # 3600s
         "activitySummary": {"dataSummary": [
             {"dataTypeName": "com.huawei.continuous.distance.total",
              "value": [{"fieldName": "distance", "floatValue": 5000.0}]},
             {"dataTypeName": "com.huawei.continuous.heart_rate.statistics",
              "value": [{"fieldName": "avg", "floatValue": 160.0},
                        {"fieldName": "max", "floatValue": 180.0}]},
         ]}},
    ]
    mock = _mock_period_client(body)
    with patch(
        "src.huawei_health_mcp.server.create_htc_client", return_value=mock
    ):
        result = get_training_period_summary(lookback_days=7, limit=100)

    assert result["activity_count"] == 1
    assert result["total_distance"] == 5000.0
    assert result["total_duration_seconds"] == 3600.0
    assert result["average_heart_rate"] == 160.0
    assert result["activity_types"][0]["activity_type"] == 56


def test_tool_invalid_params():
    """非法 lookback_days / limit 抛 ValueError，不发请求。"""
    with pytest.raises(ValueError):
        get_training_period_summary(lookback_days=0)
    with pytest.raises(ValueError):
        get_training_period_summary(lookback_days=9999)
    with pytest.raises(ValueError):
        get_training_period_summary(limit=0)
    with pytest.raises(ValueError):
        get_training_period_summary(limit=101)


def test_tool_empty_list_legal():
    """空响应（[]）合法：返回空 summary，不 traceback。"""
    mock = _mock_period_client([])
    with patch(
        "src.huawei_health_mcp.server.create_htc_client", return_value=mock
    ):
        result = get_training_period_summary()
    assert result["activity_count"] == 0
    assert result["total_distance"] is None


# ---------------- 真实 fixture 离线聚合 ----------------

def test_real_records_aggregation():
    """真实 records（data_temp，5 条）离线聚合：只断言数据可证的事实。"""
    if not REAL_RECORDS.exists():
        pytest.skip("real fixture 不存在（data_temp 为本地隐私数据）")
    raw = json.loads(REAL_RECORDS.read_text(encoding="utf-8"))
    records = [parse_activity_record(r) for r in raw]
    s = aggregate_activity_period(records)

    assert s.activity_count == 5
    # 距离：1800+4350+1400+7010+5840（勘探确认全部有 distance）
    assert s.total_distance == 20400.0
    assert s.distance_activity_count == 5
    # 分组：4×type90 + 1×type56（勘探确认）
    by_type = {t.activity_type: t for t in s.activity_types}
    assert by_type[90].count == 4
    assert by_type[56].count == 1
    assert by_type[56].total_distance == 7010.0
    # HR：至少 type56 有（avg 164.0 / max 188.0，Phase 6.6 已确认）
    assert s.hr_activity_count >= 1
    assert s.max_heart_rate >= 188.0


def test_real_records_aggregation_matches_manual():
    """聚合结果与测试内手写逐条聚合一致（交叉验证，不依赖猜测）。"""
    if not REAL_RECORDS.exists():
        pytest.skip("real fixture 不存在")
    raw = json.loads(REAL_RECORDS.read_text(encoding="utf-8"))
    records = [parse_activity_record(r) for r in raw]
    s = aggregate_activity_period(records)

    durations = [
        (r.end_time - r.start_time).total_seconds()
        for r in records if r.start_time and r.end_time
    ]
    assert s.total_duration_seconds == pytest.approx(sum(durations))
    assert s.duration_activity_count == len(durations)
    avg_hrs = [r.avg_heart_rate for r in records if r.avg_heart_rate is not None]
    if avg_hrs:
        assert s.average_heart_rate == pytest.approx(sum(avg_hrs) / len(avg_hrs))
        assert s.hr_activity_count == len(avg_hrs)