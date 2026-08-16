"""parser 的最小测试：只依赖本地 fixture，不连网、不读真实华为接口。"""

from pathlib import Path

from src.huawei_health_mcp.parser import parse_activity_record, parse_activity_record_file

FIXTURE = Path(__file__).parent / "fixtures" / "sample_activity_record.json"


def _load():
    return parse_activity_record_file(FIXTURE)


def test_parse_activity_record():
    """可以解析一次完整的 ActivityRecord，并取到摘要字段。"""
    rec = _load()
    assert rec.activity_id == "2338979373-1786706479000"
    assert rec.start_time is not None
    assert rec.end_time is not None
    assert rec.distance == 5120.0
    assert rec.avg_heart_rate == 142.0
    assert rec.max_heart_rate == 168.0
    assert rec.min_heart_rate == 98.0
    assert rec.vo2_max == 47.3
    assert rec.recovery_time == 36
    assert rec.training_load == 84.5
    # 配速由速度推导：1000 / 2.85 ≈ 350.9 秒/公里
    assert rec.avg_pace is not None and 350.0 < rec.avg_pace < 352.0


def test_parse_heart_rate_samples():
    """可以解析心率（exercise_heart_rate）samplePoints。"""
    rec = _load()
    assert len(rec.heart_rate_samples) == 3
    # 已按时间排序，第一个应为 93 bpm
    assert rec.heart_rate_samples[0].value == 93.0
    assert rec.heart_rate_samples[1].value == 121.0
    assert rec.heart_rate_samples[2].value == 133.0


def test_parse_cadence_samples():
    """可以解析步频（steps.rate）samplePoints，字段名 step_rate。"""
    rec = _load()
    assert len(rec.cadence_samples) == 3
    assert rec.cadence_samples[0].value == 156.0
    assert rec.cadence_samples[1].value == 168.0
    assert rec.cadence_samples[2].value == 172.0


def test_record_millisecond_timestamp_conversion():
    """时间契约：ActivityRecord 记录级 startTime/endTime 是 13 位毫秒。"""
    rec = _load()
    # 1786706479000 ms == 1786706479 s
    assert int(rec.start_time.timestamp()) == 1786706479
    assert int(rec.end_time.timestamp()) == 1786707100
    assert rec.start_time.tzinfo is not None


def test_sample_point_nanosecond_timestamp_conversion():
    """时间契约：高频采样时间戳是 19 位纳秒，且按时间排序。"""
    rec = _load()
    ts = [int(s.time.timestamp()) for s in rec.heart_rate_samples]
    assert ts == sorted(ts)
    assert ts[0] == 1786706479
    assert ts[1] == 1786706484
    assert ts[2] == 1786706490

# ---------------- activitySummary 嵌套结构（Phase 6.6 真实契约） ----------------

def test_record_with_nested_activity_summary():
    """真实结构：统计嵌套在 activitySummary 下（顶层数据从未存在过）。"""
    raw = {
        "id": "nest-1", "activityType": 56,
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
            "performanceSummary": {"vo2Max": 49, "recoveryTime": 1380,
                                    "aerobicTrainingStress": 3.0},
            "paceSummary": {"avgPace": 351.5, "bestPace": 309.0},
        },
    }
    rec = parse_activity_record(raw)
    assert rec.avg_heart_rate == 164.0
    assert rec.max_heart_rate == 188.0
    assert rec.min_heart_rate == 93.0
    assert rec.distance == 7010.0
    assert rec.vo2_max == 49.0
    assert rec.recovery_time == 1380
    assert rec.training_load == 3.0
    # paceSummary 权威值优先于速度推导
    assert rec.avg_pace == 351.5 and rec.best_pace == 309.0


def test_top_level_summary_still_supported():
    """兼容路径：顶层 dataSummary 写法（历史 fixture 结构）仍可解析。"""
    raw = {
        "activityId": "top-1",
        "dataSummary": [
            {"dataTypeName": "com.huawei.continuous.heart_rate.statistics",
             "value": [{"fieldName": "avg", "floatValue": 150.0}]},
        ],
        "performanceSummary": {"vo2Max": 45},
    }
    rec = parse_activity_record(raw)
    assert rec.avg_heart_rate == 150.0 and rec.vo2_max == 45.0
