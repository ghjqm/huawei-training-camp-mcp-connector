"""parse_activity_detail() 合成数据单元测试（Phase 6.2-C）。

全部数据为 synthetic，不含任何真实用户数据（尤其 location 坐标——
真实坐标只存在于 git-ignored 的 data_temp/，绝不进入 tests/）。
真实 schema 依据 Phase 6.1 勘探结果。

时间契约（6.2-C 真实数据修正）：detail 顶层毫秒；collector / sample 纳秒。
"""

from __future__ import annotations

import json
from pathlib import Path

from src.huawei_health_mcp.models import ActivityDetail, DetailCollector
from src.huawei_health_mcp.parser import parse_activity_detail

FIXTURE = Path(__file__).parent / "fixtures" / "sample_activity_detail.json"

# 合成时间戳：1786706479 s == 2026-08-14T11:21:19Z（真实活动同一时刻）
NS = 1_786_706_479_000_000_000  # 19 位纳秒（collector / sample 契约）
MS = 1_786_706_479_000          # 13 位毫秒（detail 顶层契约，6.2-C 真实数据修正）
HR_TYPE = "com.huawei.instantaneous.exercise_heart_rate"
SPEED_TYPE = "com.huawei.instantaneous.speed"
LOC_TYPE = "com.huawei.instantaneous.location.sample"
HR_COLLECTOR_ID = f"raw:{HR_TYPE}:com.huawei.health:HUAWEI:SYNTHETIC WATCH:1"


def _sample(data_type: str, start: int, values: list[dict]) -> dict:
    """构造一个 synthetic samplePoint。"""
    return {
        "startTime": start,
        "endTime": start,
        "dataTypeName": data_type,
        "value": values,
    }


def _detail(**overrides) -> dict:
    """最小 synthetic detail 对象（1 个心率 collector，1 个采样点）。"""
    base = {
        "id": "synthetic-detail-0001",
        "activityType": 56,
        "startTime": MS,
        "endTime": MS + 2_713_000,
        "activeTime": 2713,
        "timeZone": "Asia/Shanghai",
        "details": [
            {
                "startTime": NS,
                "endTime": NS + 2_700_000_000_000,
                "dataCollectorId": HR_COLLECTOR_ID,
                "samplePoints": [
                    _sample(HR_TYPE, NS, [{"fieldName": "bpm", "floatValue": 100.0}])
                ],
            }
        ],
    }
    base.update(overrides)
    return base


# ---------------- Test 1：最小结构 ----------------

def test_minimal_detail_with_one_collector():
    d = parse_activity_detail(_detail())
    assert isinstance(d, ActivityDetail)
    assert d.activity_id == "synthetic-detail-0001"
    assert d.activity_type == 56
    assert d.time_zone == "Asia/Shanghai"
    assert d.active_time == 2713
    assert len(d.details) == 1
    assert len(d.details[0].sample_points) == 1
    assert d.details[0].sample_points[0].values == {"bpm": 100.0}


# ---------------- Test 2：dataCollectorId 保留 ----------------

def test_data_collector_id_preserved():
    d = parse_activity_detail(_detail())
    assert d.details[0].data_collector_id == HR_COLLECTOR_ID
    assert HR_TYPE in d.details[0].data_collector_id


# ---------------- Test 3：dataTypeName 保留 ----------------

def test_data_type_name_preserved():
    d = parse_activity_detail(_detail())
    assert d.details[0].sample_points[0].data_type_name == HR_TYPE


# ---------------- Test 4：三级 ns 时间戳 ----------------

def test_ns_timestamps_at_all_levels():
    d = parse_activity_detail(_detail())
    # detail 顶层毫秒契约：精确到秒
    assert int(d.start_time.timestamp()) == 1786706479
    assert int(d.end_time.timestamp()) == 1786706479 + 2713
    assert int(d.details[0].start_time.timestamp()) == 1786706479
    assert int(d.details[0].end_time.timestamp()) == 1786706479 + 2700
    assert int(d.details[0].sample_points[0].timestamp.timestamp()) == 1786706479
    # 亚秒纳秒落到 microsecond（纳秒以下截断）
    raw = _sample(HR_TYPE, NS + 123_456_789, [{"fieldName": "bpm", "floatValue": 1.0}])
    detail = _details_with_samples([raw])
    assert detail.sample_points[0].timestamp.microsecond == 123456


def test_mixed_precision_contract_cross_validated():
    """真实数据修正的回归测试：同一时刻，顶层是毫秒、collector/sample 是纳秒。

    真实证据（data_temp 离线验证，2026-08-14 活动）：顶层 1786706473000(ms)
    与 collector/sample 的 1786706473000000000(ns) 指向同一时刻，且与列表
    接口同一活动的毫秒 startTime 完全一致。'
    """
    detail = _detail()
    detail["startTime"] = 1_786_706_473_000           # ms
    detail["endTime"] = 1_786_709_186_000            # ms
    detail["details"] = [{
        "startTime": 1_786_706_473_000_000_000,     # ns（同一时刻）
        "endTime": 1_786_709_186_000_000_000,
        "dataCollectorId": HR_COLLECTOR_ID,
        "samplePoints": [
            _sample(HR_TYPE, 1_786_706_473_000_000_000,
                   [{"fieldName": "bpm", "floatValue": 1.0}])
        ],
    }]
    d = parse_activity_detail(detail)
    t_top = int(d.start_time.timestamp())
    assert t_top == int(d.details[0].start_time.timestamp())
    assert t_top == int(d.details[0].sample_points[0].timestamp.timestamp())
    assert t_top == 1786706473  # 2026-08-14T11:21:13Z，与真实活动一致


def _details_with_samples(samples: list[dict]) -> DetailCollector:
    d = parse_activity_detail(
        _detail(details=[{"dataCollectorId": HR_COLLECTOR_ID, "samplePoints": samples}])
    )
    return d.details[0]


# ---------------- Test 5：floatValue ----------------

def test_float_value_conversion():
    samples = [
        _sample(HR_TYPE, NS, [{"fieldName": "bpm", "floatValue": 142.0}]),
        _sample(SPEED_TYPE, NS, [{"fieldName": "speed", "floatValue": 2.91}]),
    ]
    collector = _details_with_samples(samples)
    assert collector.sample_points[0].values["bpm"] == 142.0
    assert isinstance(collector.sample_points[0].values["bpm"], float)
    assert collector.sample_points[1].values["speed"] == 2.91
    assert isinstance(collector.sample_points[1].values["speed"], float)


# ---------------- Test 6：integerValue ----------------

def test_integer_value_conversion():
    samples = [
        _sample(LOC_TYPE, NS, [{"fieldName": "coordinate", "integerValue": 1}])
    ]
    collector = _details_with_samples(samples)
    v = collector.sample_points[0].values["coordinate"]
    assert v == 1
    assert isinstance(v, int)


# ---------------- Test 7：单字段 sample ----------------

def test_single_field_sample():
    collector = _details_with_samples(
        [_sample(HR_TYPE, NS, [{"fieldName": "bpm", "floatValue": 100.0}])]
    )
    assert collector.sample_points[0].values == {"bpm": 100.0}


# ---------------- Test 8：多字段 sample（synthetic location） ----------------

def test_multi_field_sample_location_synthetic():
    samples = [
        _sample(
            LOC_TYPE,
            NS,
            [
                {"fieldName": "latitude", "floatValue": 35.0},      # synthetic
                {"fieldName": "longitude", "floatValue": 139.0},   # synthetic
                {"fieldName": "precision", "floatValue": 5.0},
                {"fieldName": "altitude", "floatValue": 40.0},
                {"fieldName": "coordinate", "integerValue": 1},
            ],
        )
    ]
    collector = _details_with_samples(samples)
    vals = collector.sample_points[0].values
    assert set(vals) == {"latitude", "longitude", "precision", "altitude", "coordinate"}
    assert isinstance(vals["latitude"], float)
    assert isinstance(vals["coordinate"], int)


# ---------------- Test 9：空 details ----------------

def test_empty_details_legal():
    d = parse_activity_detail(_detail(details=[]))
    assert isinstance(d, ActivityDetail)
    assert d.details == []
    assert d.activity_id == "synthetic-detail-0001"  # 其余字段仍正常解析


# ---------------- Test 10：空 samplePoints ----------------

def test_empty_sample_points_legal():
    d = parse_activity_detail(
        _detail(details=[{"dataCollectorId": HR_COLLECTOR_ID, "samplePoints": []}])
    )
    assert len(d.details) == 1
    assert d.details[0].sample_points == []
    assert d.details[0].data_collector_id == HR_COLLECTOR_ID


# ---------------- Test 11：多 collector ----------------

def test_multiple_collectors():
    details = [
        {
            "dataCollectorId": HR_COLLECTOR_ID,
            "samplePoints": [_sample(HR_TYPE, NS, [{"fieldName": "bpm", "floatValue": 100.0}])],
        },
        {
            "dataCollectorId": f"raw:{SPEED_TYPE}:com.huawei.health:HUAWEI:SYNTHETIC WATCH:2",
            "samplePoints": [_sample(SPEED_TYPE, NS, [{"fieldName": "speed", "floatValue": 3.0}])],
        },
        {
            "dataCollectorId": f"raw:{LOC_TYPE}:com.huawei.health:HUAWEI:SYNTHETIC WATCH:3",
            "samplePoints": [],
        },
    ]
    d = parse_activity_detail(_detail(details=details))
    assert len(d.details) == 3
    assert d.details[0].data_collector_id == HR_COLLECTOR_ID
    assert d.details[1].sample_points[0].values == {"speed": 3.0}
    assert d.details[2].sample_points == []


# ---------------- Client 顶层 list 契约 ----------------

def test_list_input_uses_first_element():
    d = parse_activity_detail([_detail()])
    assert isinstance(d, ActivityDetail)
    assert d.activity_id == "synthetic-detail-0001"
    assert len(d.details) == 1


def test_empty_list_returns_empty_detail():
    d = parse_activity_detail([])
    assert isinstance(d, ActivityDetail)
    assert d.details == []
    assert d.activity_id is None


# ---------------- Malformed value 边界处理 ----------------

def test_malformed_values_skipped():
    samples = [
        _sample(
            HR_TYPE,
            NS,
            [
                {"fieldName": "bpm", "floatValue": 100.0},        # 正常
                {"floatValue": 1.0},                              # 缺 fieldName
                {"fieldName": "x"},                               # 无任何值
                {"fieldName": "y", "floatValue": 1.0, "integerValue": 2},  # 双值
                {"fieldName": "z", "floatValue": None},           # None 无法转 float
                "not-a-dict",                                     # 非 dict 条目
            ],
        )
    ]
    collector = _details_with_samples(samples)
    # 只保留正常条目，malformed 全部跳过、不静默制造错误数据
    assert collector.sample_points[0].values == {"bpm": 100.0}


def test_sample_missing_required_fields_skipped():
    samples = [
        {"dataTypeName": HR_TYPE, "value": []},                  # 缺 startTime
        {"startTime": NS, "value": []},                          # 缺 dataTypeName
        _sample(HR_TYPE, NS, [{"fieldName": "bpm", "floatValue": 1.0}]),  # 正常
    ]
    collector = _details_with_samples(samples)
    assert len(collector.sample_points) == 1
    assert collector.sample_points[0].values == {"bpm": 1.0}


# ---------------- 合成 fixture 完整解析 ----------------

def test_synthetic_fixture_full_parse():
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    d = parse_activity_detail(raw)  # fixture 顶层是 list（真实契约）
    assert d.activity_id == "synthetic-detail-0001"
    assert d.activity_type == 56
    assert int(d.start_time.timestamp()) == 1786706473
    assert len(d.details) == 3  # 心率 / location / 空 power collector
    hr = d.details[0]
    assert len(hr.sample_points) == 2
    ts = [s.timestamp for s in hr.sample_points]
    assert ts == sorted(ts)
    loc = d.details[1]
    assert set(loc.sample_points[0].values) == {
        "latitude", "longitude", "precision", "altitude", "coordinate"
    }
    assert d.details[2].sample_points == []