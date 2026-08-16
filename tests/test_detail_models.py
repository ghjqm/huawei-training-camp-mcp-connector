"""ActivityDetail domain model + timestamp contract 的合成数据测试。

数据全部为 synthetic（tests/fixtures/sample_activity_detail.json 与本文件内
构造的值），不含任何真实用户数据（尤其 location）。

时间契约（由真实 HTC 响应确认，禁止按位数猜单位）：
- ActivityRecord 记录级时间：13 位毫秒（_ms_to_datetime）
- ActivityDetail（含 collector / samplePoints）：19 位纳秒（_ns_to_datetime）

注：Activity Detail 的生产 parser 属于后续阶段；本文件中的 fixture 映射
只是临时验证「真实 schema 结构能被 domain model 完整表达」。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from src.huawei_health_mcp.models import (
    ActivityDetail,
    DetailCollector,
    DetailSamplePoint,
)
from src.huawei_health_mcp.parser import _ms_to_datetime, _ns_to_datetime

FIXTURE = Path(__file__).parent / "fixtures" / "sample_activity_detail.json"


# ---------------- Timestamp contract ----------------

def test_ms_to_datetime_13_digit():
    """ActivityRecord 契约：13 位毫秒正确转换。"""
    dt = _ms_to_datetime(1786706479000)
    assert dt is not None
    assert int(dt.timestamp()) == 1786706479
    assert dt.tzinfo is not None


def test_ms_to_datetime_preserves_milliseconds():
    """毫秒部分不被丢弃。"""
    dt = _ms_to_datetime(1786706479123)
    assert dt.microsecond == 123000


def test_ns_to_datetime_19_digit():
    """ActivityDetail 契约：19 位纳秒正确转换。"""
    dt = _ns_to_datetime(1786706479000000000)
    assert dt is not None
    assert int(dt.timestamp()) == 1786706479
    assert dt.tzinfo is not None


def test_ns_to_datetime_preserves_subsecond():
    """纳秒的亚秒部分正确落到 microsecond（纳秒以下截断）。"""
    dt = _ns_to_datetime(1786706479123456789)
    assert dt.microsecond == 123456


def test_none_timestamp_passthrough():
    assert _ms_to_datetime(None) is None
    assert _ns_to_datetime(None) is None


# ---------------- Sample value 表达能力 ----------------

def test_single_field_sample():
    """单字段 sample：values 只有一个 fieldName。"""
    sp = DetailSamplePoint(
        timestamp=datetime(2026, 8, 1, tzinfo=timezone.utc),
        data_type_name="com.huawei.instantaneous.exercise_heart_rate",
        values={"bpm": 100.0},
    )
    assert sp.values == {"bpm": 100.0}
    assert isinstance(sp.values["bpm"], float)


def test_multi_field_sample_with_integer_value():
    """多字段 sample：location 式多字段 + integerValue 保真为 int。"""
    sp = DetailSamplePoint(
        timestamp=datetime(2026, 8, 1, tzinfo=timezone.utc),
        data_type_name="com.huawei.instantaneous.location.sample",
        values={"latitude": 30.0, "longitude": 120.0, "coordinate": 1},
    )
    assert len(sp.values) == 3
    assert isinstance(sp.values["latitude"], float)
    assert isinstance(sp.values["coordinate"], int)


# ---------------- 缺失数据表达 ----------------

def test_empty_details_allowed():
    """details=[] 是合法状态（请求的 dataType 不保证返回）。"""
    d = ActivityDetail(activity_id="synthetic-1")
    assert d.details == []


def test_empty_collector_allowed():
    """空 collector（sample_points=[]）可表达。"""
    c = DetailCollector(data_collector_id="raw:com.huawei.analog_power:x:1")
    assert c.sample_points == []
    assert c.data_collector_id.startswith("raw:")


# ---------------- Synthetic fixture 完整映射 ----------------

def test_synthetic_fixture_expressible_by_models():
    """synthetic fixture（真实 schema 结构）能被 domain model 完整表达。

    覆盖：ns 时间戳 / floatValue / integerValue / 单字段 / 多字段 /
    dataTypeName / dataCollectorId / details[] / samplePoints[] / 空 collector。
    """
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert isinstance(raw, list)  # 真实 detail 响应顶层是 list
    top = raw[0]

    detail = ActivityDetail(
        activity_id=top["id"],
        activity_type=top["activityType"],
        start_time=_ms_to_datetime(top["startTime"]),  # 契约：detail 顶层毫秒
        end_time=_ms_to_datetime(top["endTime"]),      # 契约：detail 顶层毫秒
        active_time=top["activeTime"],
        time_zone=top["timeZone"],
        details=[
            DetailCollector(
                data_collector_id=c["dataCollectorId"],
                start_time=_ns_to_datetime(c["startTime"]),
                end_time=_ns_to_datetime(c["endTime"]),
                sample_points=[
                    DetailSamplePoint(
                        timestamp=_ns_to_datetime(sp["startTime"]),
                        data_type_name=sp["dataTypeName"],
                        values={
                            v["fieldName"]: v.get("floatValue", v.get("integerValue"))
                            for v in sp["value"]
                        },
                    )
                    for sp in c["samplePoints"]
                ],
            )
            for c in top["details"]
        ],
    )

    assert detail.activity_id == "synthetic-detail-0001"
    assert detail.activity_type == 56
    assert int(detail.start_time.timestamp()) == 1786706473

    # collector 1：心率（单字段 + floatValue + ns 排序）
    hr = detail.details[0]
    assert "exercise_heart_rate" in hr.data_collector_id  # dataCollectorId 保留
    assert len(hr.sample_points) == 2
    assert hr.sample_points[0].values == {"bpm": 100.0}
    assert hr.sample_points[0].data_type_name == (
        "com.huawei.instantaneous.exercise_heart_rate"
    )  # dataTypeName 保留
    ts = [s.timestamp for s in hr.sample_points]
    assert ts == sorted(ts)

    # collector 2：location（多字段 + integerValue）
    loc = detail.details[1]
    assert len(loc.sample_points) == 1
    vals = loc.sample_points[0].values
    assert set(vals) == {"latitude", "longitude", "precision", "altitude", "coordinate"}
    assert isinstance(vals["coordinate"], int)

    # collector 3：空序列（缺失数据是合法状态）
    empty = detail.details[2]
    assert empty.sample_points == []