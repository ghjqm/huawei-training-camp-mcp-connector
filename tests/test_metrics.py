"""compute_session_metrics + get_session_metrics 测试（Phase 6.6）。

全部 synthetic，不联网、不含真实用户数据。
双源策略断言：device_summary 权威优先 / samples 聚合回退 / 缺失即 None。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from src.huawei_health_mcp.analysis import compute_session_metrics
from src.huawei_health_mcp.client import HTCClient
from src.huawei_health_mcp.models import ActivityDetail, ActivityRecord, DetailCollector, DetailSamplePoint
from src.huawei_health_mcp.server import get_session_metrics

FIXTURE = Path(__file__).parent / "fixtures" / "sample_activity_detail.json"
T0 = datetime(2026, 8, 14, 11, 21, 13, tzinfo=timezone.utc)
T1 = datetime(2026, 8, 14, 12, 6, 26, tzinfo=timezone.utc)
HR_TYPE = "com.huawei.instantaneous.exercise_heart_rate"
SPEED_TYPE = "com.huawei.instantaneous.speed"


def _detail_with(hr: list[float] | None = None, speed: list[float] | None = None) -> ActivityDetail:
    """构造带 HR / speed 采样的 synthetic ActivityDetail。"""
    collectors = []
    if hr is not None:
        collectors.append(DetailCollector(sample_points=[
            DetailSamplePoint(
                timestamp=datetime(2026, 8, 14, 11, 21 + i, tzinfo=timezone.utc),
                data_type_name=HR_TYPE, values={"bpm": v},
            ) for i, v in enumerate(hr)
        ]))
    if speed is not None:
        collectors.append(DetailCollector(sample_points=[
            DetailSamplePoint(
                timestamp=datetime(2026, 8, 14, 11, 21 + i, tzinfo=timezone.utc),
                data_type_name=SPEED_TYPE, values={"speed": v},
            ) for i, v in enumerate(speed)
        ]))
    return ActivityDetail(
        activity_id="syn-metrics-1", activity_type=56,
        start_time=T0, end_time=T1, active_time=2464000,
        details=collectors,
    )


def _record_with(**kw) -> ActivityRecord:
    base = dict(activity_id="syn-metrics-1", activity_type=56)
    base.update(kw)
    return ActivityRecord(**base)


# ---------------- 双源优先级 ----------------

def test_hr_prefers_device_summary():
    """record 有权威 HR 时直接采用，不读采样。"""
    d = _detail_with(hr=[100.0, 200.0])  # 采样值故意不同，证明未被使用
    r = _record_with(avg_heart_rate=164.0, max_heart_rate=188.0, min_heart_rate=93.0)
    m = compute_session_metrics(d, r)
    assert (m.avg_heart_rate, m.max_heart_rate, m.min_heart_rate) == (164.0, 188.0, 93.0)
    assert m.heart_rate_source == "device_summary"


def test_hr_falls_back_to_samples():
    """record 无 HR 时用采样聚合（avg/max/min）。"""
    d = _detail_with(hr=[100.0, 140.0, 180.0])
    m = compute_session_metrics(d, _record_with())
    assert m.avg_heart_rate == pytest.approx(140.0)
    assert m.max_heart_rate == 180.0 and m.min_heart_rate == 100.0
    assert m.heart_rate_source == "samples"


def test_pace_prefers_device_summary():
    """record.avg_pace（paceSummary 权威）优先于采样换算。"""
    d = _detail_with(speed=[3.0])
    r = _record_with(avg_pace=351.5, best_pace=309.0)
    m = compute_session_metrics(d, r)
    assert (m.avg_pace, m.best_pace) == (351.5, 309.0)
    assert m.pace_source == "device_summary"


def test_pace_derived_from_speed_samples():
    """无权威配速时由速度采样换算：1000/avg(speed) 秒每公里。"""
    d = _detail_with(speed=[2.5, 3.5])  # avg=3.0 → pace=333.33
    m = compute_session_metrics(d, _record_with())
    assert m.avg_pace == pytest.approx(1000.0 / 3.0)
    assert m.best_pace == pytest.approx(1000.0 / 3.5)
    assert m.pace_source == "samples"


# ---------------- 纯数学指标 ----------------

def test_duration_seconds():
    """duration = detail 顶层 end-start（毫秒契约的时间差）。"""
    m = compute_session_metrics(_detail_with())
    assert m.duration_seconds == pytest.approx((T1 - T0).total_seconds())
    assert m.active_time_raw == 2464000  # 原样保留，不转换


def test_distance_from_record_only():
    """距离只取 record 权威值；无 record 时 None（不积分采样）。"""
    m1 = compute_session_metrics(_detail_with(speed=[3.0]), _record_with(distance=7010.0))
    assert m1.distance == 7010.0 and m1.distance_source == "device_summary"
    m2 = compute_session_metrics(_detail_with(speed=[3.0]))
    assert m2.distance is None and m2.distance_source is None


def test_empty_detail_all_none():
    """无采样、无 record：指标全 None，source 全 None，不抛错。"""
    m = compute_session_metrics(ActivityDetail(activity_id="x"))
    assert m.avg_heart_rate is None and m.avg_speed is None
    assert m.avg_pace is None and m.distance is None
    assert m.heart_rate_source is None and m.pace_source is None


# ---------------- MCP tool 全链路（MockTransport） ----------------

def test_get_session_metrics_tool_full_chain():
    """列表定位 + 详情 + record 解析 + 双源指标全链路（synthetic 嵌套结构）。"""
    detail_body = json.loads(FIXTURE.read_text(encoding="utf-8"))
    # 真实嵌套结构：权威统计在 activitySummary 下（Phase 6.6 契约）
    records_body = [{
        "id": "synthetic-detail-0001", "activityType": 56,
        "startTime": 1786706473000, "endTime": 1786709186000,
        "activitySummary": {
            "dataSummary": [
                {"dataTypeName": "com.huawei.continuous.heart_rate.statistics",
                 "value": [{"fieldName": "avg", "floatValue": 164.0},
                           {"fieldName": "max", "floatValue": 188.0},
                           {"fieldName": "min", "floatValue": 93.0}]},
                {"dataTypeName": "com.huawei.continuous.distance.total",
                 "value": [{"fieldName": "distance", "floatValue": 7010.0}]},
            ],
            "paceSummary": {"avgPace": 351.5, "bestPace": 309.0},
        },
    }]

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode())
        if "detailDataType" in payload:
            return httpx.Response(200, json=detail_body)
        return httpx.Response(200, json=records_body)

    mock_client = HTCClient(
        headers={}, client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    with patch(
        "src.huawei_health_mcp.server.create_htc_client", return_value=mock_client
    ):
        result = get_session_metrics(activity_id="synthetic-detail-0001")

    assert result["activity_id"] == "synthetic-detail-0001"
    assert result["avg_heart_rate"] == 164.0
    assert result["heart_rate_source"] == "device_summary"
    assert result["distance"] == 7010.0
    assert result["avg_pace"] == 351.5
    assert result["pace_source"] == "device_summary"
    assert result["duration_seconds"] == pytest.approx(2713.0)