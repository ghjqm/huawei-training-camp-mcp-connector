"""summarize_activity_detail() 纯分析层测试（Phase 6.3）。

全部 synthetic：直接构造 ActivityDetail 或解析 synthetic fixture，
不访问网络、不含真实用户数据。统计均为确定性聚合，断言精确值。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from src.huawei_health_mcp.analysis import summarize_activity_detail
from src.huawei_health_mcp.models import ActivityDetail, DetailCollector, DetailSamplePoint
from src.huawei_health_mcp.parser import parse_activity_detail

FIXTURE = Path(__file__).parent / "fixtures" / "sample_activity_detail.json"

T0 = datetime(2026, 8, 14, 11, 21, 19, tzinfo=timezone.utc)
HR_TYPE = "com.huawei.instantaneous.exercise_heart_rate"
LOC_TYPE = "com.huawei.instantaneous.location.sample"


def _sp(minute: int, data_type: str, values: dict) -> DetailSamplePoint:
    return DetailSamplePoint(
        timestamp=datetime(2026, 8, 14, 11, 21 + minute, tzinfo=timezone.utc),
        data_type_name=data_type,
        values=values,
    )


# ---------------- 单字段统计 ----------------

def test_single_field_stats():
    """bpm 序列的 count/min/max/avg/first/last 全部正确。"""
    detail = ActivityDetail(
        activity_id="syn-1",
        details=[
            DetailCollector(
                data_collector_id="raw:hr:1",
                sample_points=[
                    _sp(0, HR_TYPE, {"bpm": 100.0}),
                    _sp(1, HR_TYPE, {"bpm": 140.0}),
                    _sp(2, HR_TYPE, {"bpm": 120.0}),
                ],
            )
        ],
    )
    s = summarize_activity_detail(detail)
    assert s.activity_id == "syn-1"
    assert len(s.collectors) == 1
    c = s.collectors[0]
    assert c.sample_count == 3
    assert c.data_type_name == HR_TYPE  # 从首个 sample 推断
    bpm = c.fields[0]
    assert (bpm.field_name, bpm.count) == ("bpm", 3)
    assert bpm.min_value == 100.0 and bpm.max_value == 140.0
    assert bpm.avg_value == 120.0
    assert bpm.first_value == 100.0 and bpm.last_value == 120.0


# ---------------- 多字段独立统计 ----------------

def test_multi_field_independent_stats():
    """location 多字段各自独立统计，互不混合。"""
    detail = ActivityDetail(
        details=[
            DetailCollector(
                data_collector_id="raw:loc:1",
                sample_points=[
                    _sp(0, LOC_TYPE, {"latitude": 35.0, "longitude": 139.0}),
                    _sp(1, LOC_TYPE, {"latitude": 35.2, "longitude": 139.1}),
                ],
            )
        ],
    )
    fields = {f.field_name: f for f in summarize_activity_detail(detail).collectors[0].fields}
    assert set(fields) == {"latitude", "longitude"}
    assert fields["latitude"].max_value == 35.2
    assert fields["longitude"].min_value == 139.0
    assert fields["latitude"].count == 2


# ---------------- int 字段类型保真 ----------------

def test_int_field_type_preserved():
    """int 字段 min/max/first/last 保持 int，avg 为 float。"""
    detail = ActivityDetail(
        details=[
            DetailCollector(
                sample_points=[
                    _sp(0, LOC_TYPE, {"coordinate": 1}),
                    _sp(1, LOC_TYPE, {"coordinate": 3}),
                ],
            )
        ],
    )
    f = summarize_activity_detail(detail).collectors[0].fields[0]
    assert f.min_value == 1 and isinstance(f.min_value, int)
    assert f.max_value == 3 and isinstance(f.max_value, int)
    assert f.avg_value == 2.0 and isinstance(f.avg_value, float)


# ---------------- 空数据 ----------------

def test_empty_collector_legal():
    """空 collector：sample_count=0、fields=[]、data_type_name=None。"""
    detail = ActivityDetail(
        details=[DetailCollector(data_collector_id="raw:power:1", sample_points=[])]
    )
    c = summarize_activity_detail(detail).collectors[0]
    assert c.sample_count == 0
    assert c.fields == []
    assert c.data_type_name is None


def test_empty_details_legal():
    """details=[]：collectors=[]，顶层元信息保留。"""
    detail = ActivityDetail(activity_id="syn-2", activity_type=56)
    s = summarize_activity_detail(detail)
    assert s.collectors == []
    assert s.activity_id == "syn-2" and s.activity_type == 56


# ---------------- 时间范围 fallback ----------------

def test_time_range_fallback_to_samples():
    """collector 自带时间缺失时，用首末 sample timestamp。"""
    detail = ActivityDetail(
        details=[
            DetailCollector(
                sample_points=[
                    _sp(5, HR_TYPE, {"bpm": 1.0}),
                    _sp(10, HR_TYPE, {"bpm": 2.0}),
                ],
            )
        ],
    )
    c = summarize_activity_detail(detail).collectors[0]
    assert c.start_time == datetime(2026, 8, 14, 11, 26, tzinfo=timezone.utc)
    assert c.end_time == datetime(2026, 8, 14, 11, 31, tzinfo=timezone.utc)


def test_collector_time_preferred_when_present():
    """collector 自带时间优先于 sample 推断。"""
    detail = ActivityDetail(
        details=[
            DetailCollector(
                start_time=T0,
                end_time=T0,
                sample_points=[_sp(0, HR_TYPE, {"bpm": 1.0})],
            )
        ],
    )
    c = summarize_activity_detail(detail).collectors[0]
    assert c.start_time == T0 and c.end_time == T0


# ---------------- synthetic fixture 全链路 ----------------

def test_fixture_full_chain_parse_then_summarize():
    """fixture（真实 schema 结构）→ parse → summarize 全链路。"""
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    s = summarize_activity_detail(parse_activity_detail(raw))

    assert s.activity_id == "synthetic-detail-0001"
    assert s.activity_type == 56
    assert len(s.collectors) == 3

    hr = s.collectors[0]
    assert hr.sample_count == 2
    bpm = hr.fields[0]
    assert bpm.min_value == 100.0 and bpm.max_value == 110.0
    assert bpm.avg_value == 105.0

    loc = s.collectors[1]
    loc_fields = {f.field_name: f for f in loc.fields}
    assert set(loc_fields) == {"latitude", "longitude", "precision", "altitude", "coordinate"}
    assert loc_fields["coordinate"].min_value == 1  # int 保真

    assert s.collectors[2].sample_count == 0  # 空 power collector