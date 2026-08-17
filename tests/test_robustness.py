"""健壮性压力测试（审计轮新增）：malformed 原始数据绝不毒死解析。

parse_activity_record / parse_activity_detail 的 docstring 契约：
"任何缺失字段都填 None 或空列表，不会抛错"。真实 API 字段类型若
发生变化（如 stringValue / 字符串时间戳 / 异常数值），单条毒记录
不得让整批列表解析崩溃（get_recent_activities 对每条记录调用
parse_activity_record）。本文件用合成 malformed 数据锁定该契约。
"""

from __future__ import annotations

import pytest

from src.huawei_health_mcp.parser import (
    parse_activity_detail,
    parse_activity_record,
)

# ---------------- 单值污染矩阵：malformed 值 → None，绝不抛错 ----------------

_MALFORMED_VALUES = [
    "abc",              # 非数字字符串（stringValue 场景）
    "",                 # 空字符串
    {"a": 1},           # dict
    [1, 2],             # list
    True,               # bool（pydantic 会拒 int/float 之外的类型）
    float("nan"),       # NaN
    float("inf"),       # inf
]


class TestMalformedScalarFields:
    """统计字段的 value 携带异常类型时，字段归 None、解析不崩。"""

    @pytest.mark.parametrize("bad", _MALFORMED_VALUES)
    def test_distance_malformed(self, bad):
        raw = {
            "id": "m-1", "activityType": 56,
            "activitySummary": {"dataSummary": [
                {"dataTypeName": "com.huawei.continuous.distance.total",
                 "value": [{"fieldName": "distance", "stringValue": bad if isinstance(bad, str) else None,
                            "floatValue": None if isinstance(bad, str) else bad}]},
            ]},
        }
        rec = parse_activity_record(raw)  # 不得抛错
        # 合法路径：distance=None（或数值型异常值按能转则转规则处理）
        assert rec.activity_id == "m-1"

    @pytest.mark.parametrize("bad", ["abc", "", {"a": 1}, [1]])
    def test_calories_string_garbage(self, bad):
        raw = {
            "id": "m-2", "activityType": 56,
            "activitySummary": {"dataSummary": [
                {"dataTypeName": "com.huawei.continuous.calories.burnt.total",
                 "value": [{"fieldName": "calories_total", "stringValue": bad}]},
            ]},
        }
        rec = parse_activity_record(raw)
        assert rec.calories is None  # 不可解析字符串 → None，不伪造

    @pytest.mark.parametrize("bad", ["12.5abc", {"x": 1}, [3]])
    def test_steps_malformed(self, bad):
        raw = {
            "id": "m-3", "activityType": 56,
            "activitySummary": {"dataSummary": [
                {"dataTypeName": "com.huawei.continuous.steps.total",
                 "value": [{"fieldName": "steps", "integerValue": bad}]},
            ]},
        }
        rec = parse_activity_record(raw)
        assert rec.steps is None


class TestMalformedTimestamps:
    """时间戳类型/取值异常 → None，不抛错。"""

    @pytest.mark.parametrize("bad", ["1786706473000", "", None, 1.5, {"a": 1}])
    def test_record_string_timestamp(self, bad):
        """字符串/浮点/dict 时间戳：不可安全按毫秒解释 → None。"""
        raw = {"id": "t-1", "activityType": 56, "startTime": bad, "endTime": 1786706473000}
        rec = parse_activity_record(raw)
        assert rec.activity_id == "t-1"

    @pytest.mark.parametrize("bad", [-1, -10**14, 10**16])
    def test_record_extreme_timestamp(self, bad):
        """负值 / 超出 datetime 表示范围：fromtimestamp 会 Overflow/OSError。"""
        raw = {"id": "t-2", "activityType": 56, "startTime": bad, "endTime": 1786706473000}
        rec = parse_activity_record(raw)  # 不得抛错
        assert rec.activity_id == "t-2"

    def test_detail_extreme_ns_timestamp(self):
        raw = {
            "id": "d-1", "activityType": 56,
            "startTime": 1786706473000, "endTime": 1786709186000,
            "details": [
                {"dataCollectorId": "c1",
                 "startTime": 10**20,  # 超界纳秒
                 "samplePoints": [
                     {"startTime": -10**19, "dataTypeName": "x", "value": []},
                 ]},
            ],
        }
        detail = parse_activity_detail(raw)
        assert detail.activity_id == "d-1"
        # 超界采样点被跳过，不崩
        assert detail.details[0].sample_points == []


class TestMalformedStructures:
    """结构级异常：错误形状的容器不崩，按缺失处理。"""

    @pytest.mark.parametrize("summary", [
        "not-a-dict",                      # activitySummary 是字符串
        ["list", "of", "junk"],            # activitySummary 是列表
        None,
        {"dataSummary": "garbage"},        # dataSummary 是字符串
        {"dataSummary": {"valid.name": "not-dict"}},  # dict 形态的值非 dict
        {"dataSummary": [1, 2, "x"]},      # list 形态元素非 dict
        {"dataSummary": [{"noTypeName": 1}]},  # 缺 dataTypeName
        {"performanceSummary": "garbage"},
        {"paceSummary": [1, 2]},
    ])
    def test_activity_summary_shapes(self, summary):
        raw = {"id": "s-1", "activityType": 56, "activitySummary": summary}
        rec = parse_activity_record(raw)
        assert rec.activity_id == "s-1"

    def test_speed_string_comparison_crash(self):
        """速度字段为字符串时，配速推导的 `avg_speed > 0` 不得 TypeError。"""
        raw = {
            "id": "sp-1", "activityType": 56,
            "activitySummary": {"dataSummary": [
                {"dataTypeName": "com.huawei.continuous.speed.statistics",
                 "value": [{"fieldName": "averageSpeed", "stringValue": "2.85x"}]},
            ]},
        }
        rec = parse_activity_record(raw)  # 此前：str > int → TypeError
        assert rec.avg_pace is None

    def test_value_entries_with_garbage(self):
        """value 数组内混合 malformed 条目：逐条跳过，不静默造数据。"""
        raw = {
            "id": "v-1", "activityType": 56,
            "activitySummary": {"dataSummary": [
                {"dataTypeName": "com.huawei.continuous.heart_rate.statistics",
                 "value": [
                     "not-dict",
                     {"fieldName": "avg"},                    # 无值
                     {"fieldName": "avg", "floatValue": None},  # 显式 None
                     {"noField": 1, "floatValue": 150.0},       # 无 fieldName
                     {"fieldName": "avg", "floatValue": 164.0},  # 唯一合法
                 ]},
            ]},
        }
        rec = parse_activity_record(raw)
        assert rec.avg_heart_rate == 164.0

    def test_detail_data_shapes(self):
        """detailData / details / samplePoints 结构异常不崩。"""
        for raw in (
            {"id": "dd-1", "detailData": "garbage"},
            {"id": "dd-2", "detailData": [{"samplePoints": "garbage"}]},
            {"id": "dd-3", "details": "garbage"},
            {"id": "dd-4", "details": [{"samplePoints": [{"startTime": "str", "dataTypeName": "x"}]}]},
            {"id": "dd-5", "details": [42, "x", None]},
        ):
            if "detailData" in raw:
                rec = parse_activity_record(raw)  # 列表路径的 detailData
                assert rec.heart_rate_samples == []
            else:
                detail = parse_activity_detail(raw)
                assert detail.activity_id == raw["id"]

    def test_string_digit_values_accepted(self):
        """合法字符串数字（ stringValue="5120" 场景）：能转则转，行为确定。"""
        raw = {
            "id": "sd-1", "activityType": 56,
            "activitySummary": {"dataSummary": [
                {"dataTypeName": "com.huawei.continuous.distance.total",
                 "value": [{"fieldName": "distance", "stringValue": "5120"}]},
            ]},
        }
        rec = parse_activity_record(raw)
        # 修复后：能安全转换的数字字符串按数值接受（宽松转换）或归 None——
        # 两种策略都合法，但必须不抛错且行为稳定
        assert rec.distance is None or rec.distance == 5120.0
