"""健壮性压力测试（审计轮新增）：malformed 原始数据绝不毒死解析。

parse_activity_record / parse_activity_detail 的 docstring 契约：
"任何缺失字段都填 None 或空列表，不会抛错"。真实 API 字段类型若
发生变化（如 stringValue / 字符串时间戳 / 异常数值），单条毒记录
不得让整批列表解析崩溃（get_recent_activities 对每条记录调用
parse_activity_record）。本文件用合成 malformed 数据锁定该契约。

Phase 8 后全程审计扩展：同一矩阵覆盖 health_parser（睡眠 / 统计）
与 performance_parser（运动能力 / PB），并做批量毒数据压力验证。
"""

from __future__ import annotations

import pytest

from src.huawei_health_mcp.health_parser import (
    parse_health_metric_stats,
    parse_sleep_records,
)
from src.huawei_health_mcp.parser import (
    parse_activity_detail,
    parse_activity_record,
)
from src.huawei_health_mcp.performance_parser import (
    parse_athletic_performance,
    parse_personal_bests,
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


# ---------------------------------------------------------------------------
# Phase 8 后全程审计扩展：health_parser / performance_parser 同一容错契约
# ---------------------------------------------------------------------------

# 数值污染矩阵（含审计修复针对的三类毒值：NaN/inf、超大 int、bool）
_POISON_NUMBERS = [
    "abc",
    "",
    {"a": 1},
    [1],
    True,
    float("nan"),
    float("inf"),
    float("-inf"),
    10**400,  # 超 float 范围的 int：float() 会 OverflowError
]


def _stats_kwargs():
    return dict(
        data_type="x", days=1, start_day="2026-08-17", end_day="2026-08-17"
    )


class TestHealthStatsMalformed:
    """periodStatistics 解析：统计值 / fieldName / startDay 异常不崩。"""

    @pytest.mark.parametrize("bad", _POISON_NUMBERS)
    def test_statistics_values_poisoned(self, bad):
        """avg/max/min/count 携带毒值：None 化（NaN/inf/超大 int/bool），不抛错。"""
        stats = parse_health_metric_stats(
            {
                "results": [
                    {
                        "fieldName": "restBpm",
                        "statistics": {"avg": bad, "max": bad, "min": bad},
                    }
                ]
            },
            field_name=None,
            **_stats_kwargs(),
        )
        assert stats.overall.avg is None
        assert stats.overall.max is None
        assert stats.overall.min is None
        assert stats.field_name == "restBpm"

    @pytest.mark.parametrize("bad_fn", [123, ["a"], {"x": 1}, None, True])
    def test_field_name_non_string(self, bad_fn):
        """服务端 fieldName 非 str：保持 None，不抛 ValidationError。

        审计修复验证：此前 `results[0].get("fieldName") or field_name` 会把
        非法类型直接塞进 Optional[str] 模型字段炸掉整次解析（真实崩溃
        路径是静息心率——该 tool 以 field_name=None 调用解析器）。
        """
        stats = parse_health_metric_stats(
            {"results": [{"fieldName": bad_fn, "statistics": {"avg": 1.0}}]},
            field_name=None,
            **_stats_kwargs(),
        )
        assert stats.field_name is None  # 非法服务端值不进模型
        assert stats.overall.avg == 1.0  # 统计仍正常解析

    @pytest.mark.parametrize("bad_day", ["٢٠٢٦٠٨١٧", "2026081", "202608177",
                                         "2026-08-1", 2026081.5, None, True])
    def test_group_start_day_poisoned(self, bad_day):
        """startDay 非 8 位 ASCII 数字（含 unicode 数字）：该天条目跳过。"""
        stats = parse_health_metric_stats(
            {
                "groupResults": [
                    {
                        "groupStatistics": [
                            {"fieldName": "restBpm", "startDay": bad_day,
                             "statistics": {"avg": 1.0}},
                        ]
                    }
                ]
            },
            field_name=None,
            **_stats_kwargs(),
        )
        assert stats.daily == []

    def test_top_level_shapes(self):
        """顶层结构异常：全空结构，不崩。"""
        for garbage in (None, [], "x", 42, {"results": "no"},
                        {"results": [None, "x", 42]},
                        {"results": [{"fieldName": "a", "statistics": "bad"}]},
                        {"groupResults": "no"}, {"groupResults": [None, 42]}):
            stats = parse_health_metric_stats(
                garbage, field_name="avgHrv", **_stats_kwargs()
            )
            assert stats.overall.avg is None
            assert stats.daily == []


class TestSleepMalformed:
    """睡眠解析：value 字段毒值 / 结构异常不崩，合法字段保留。"""

    @pytest.mark.parametrize("bad", _POISON_NUMBERS)
    def test_sleep_value_poisoned(self, bad):
        """睡眠分段字段毒值 → None；毒时间戳 → None；不抛错。

        10**400（合法 int）在分钟字段保真透传，时间戳字段超 datetime
        范围归 None。
        """
        records = parse_sleep_records(
            [
                {
                    "id": "s-1",
                    "startTime": 1777824000000000000,
                    "endTime": 1777852800000000000,
                    "value": [
                        {"fieldName": "light_sleep_time", "longValue": bad},
                        {"fieldName": "all_sleep_time", "longValue": bad},
                        {"fieldName": "go_bed_time", "longValue": bad},
                        {"fieldName": "sleep_score", "integerValue": bad},
                    ],
                }
            ]
        )
        assert len(records) == 1
        r = records[0]
        assert r.go_bed_time is None  # 时间戳毒值/超界一律 None
        if isinstance(bad, int) and not isinstance(bad, bool):
            assert r.light_sleep_minutes == bad  # int 保真（契约：合法整数透传）
            assert r.total_sleep_minutes == bad
            assert r.sleep_score == bad
        else:
            assert r.light_sleep_minutes is None
            assert r.total_sleep_minutes is None
            assert r.sleep_score is None

    def test_sleep_structures(self):
        for garbage in (None, [], "x", 42, {"healthRecords": "no"},
                        {"healthRecords": [None, 42, "x"]}):
            assert parse_sleep_records(garbage) == []


class TestPerformanceMalformed:
    """运动能力 / PB 解析：毒值矩阵 + 结构异常不崩。"""

    @pytest.mark.parametrize("bad", _POISON_NUMBERS)
    def test_athletic_performance_poisoned(self, bad):
        """五项指数毒值 → None；预测成绩毒值跳过该键；不抛错。

        审计修复验证：此前 NaN 直接透传（非法 JSON 输出风险）、
        10**400 触发 OverflowError 崩溃。
        """
        perf = parse_athletic_performance(
            {
                "runningAbility": bad,
                "condition": bad,
                "fitness": bad,
                "fatigue": bad,
                "ranking": bad,
                "predictedTimes": {"km1": bad, "km3": 875},
            }
        )
        assert perf.running_ability is None
        assert perf.condition is None
        assert perf.fitness is None
        assert perf.fatigue is None
        assert perf.ranking is None
        assert perf.predicted_times == {"km3": 875.0}

    @pytest.mark.parametrize("bad_ts", [True, False, "1786706473000",
                                        1.5, {"a": 1}, [1]])
    def test_pb_bool_timestamp_not_fabricated(self, bad_ts):
        """PB 时间戳毒值 → None：bool（int 子类）不得伪造 1970 年时间。"""
        bests = parse_personal_bests(
            {
                "sportReports": [
                    {
                        "activityType": "running",
                        "personalBest": [
                            {"name": "ok", "value": 1.0,
                             "startTime": bad_ts, "endTime": bad_ts},
                        ],
                    }
                ]
            },
            activity_type="running",
        )
        assert len(bests.personal_bests) == 1
        assert bests.personal_bests[0].start_time is None
        assert bests.personal_bests[0].end_time is None

    @pytest.mark.parametrize("bad", _POISON_NUMBERS)
    def test_pb_value_poisoned(self, bad):
        bests = parse_personal_bests(
            {
                "sportReports": [
                    {
                        "activityType": "running",
                        "personalBest": [{"name": "ok", "value": bad}],
                    }
                ]
            },
            activity_type="running",
        )
        assert bests.personal_bests[0].value is None

    def test_pb_structures(self):
        for garbage in (None, [], "x", 42, {"sportReports": "no"},
                        {"sportReports": [None, 42, "x"]},
                        {"sportReports": [{"activityType": "running",
                                           "personalBest": "no"}]},
                        {"sportReports": [{"activityType": 123,
                                           "personalBest": [{"name": "x"}]}]}):
            assert parse_personal_bests(garbage, activity_type="running").personal_bests == []


class TestBatchPoisonStress:
    """批量毒数据压力：5% 污染率下大批量解析不崩、不丢合法记录。"""

    def test_5000_sleep_records_5pct_poison(self):
        items = []
        for i in range(5000):
            poisoned = i % 20 == 0
            items.append(
                {
                    "id": f"s-{i}",
                    "startTime": 1777824000000000000 + i * 86_400_000_000_000,
                    "endTime": 1777852800000000000 + i * 86_400_000_000_000,
                    "value": (
                        [{"fieldName": "all_sleep_time", "longValue": float("nan")}]
                        if poisoned
                        else [{"fieldName": "all_sleep_time", "longValue": 470}]
                    ),
                }
            )
        records = parse_sleep_records(items)
        assert len(records) == 5000  # 毒记录保留（字段 None），不丢不崩
        valid = [r for r in records if r.total_sleep_minutes == 470]
        assert len(valid) == 4750

    def test_365_day_stats_with_poison_groups(self):
        groups = []
        for i in range(365):
            stats_entry = (
                {"avg": float("inf")} if i % 50 == 0 else {"avg": float(50 + i % 10)}
            )
            groups.append(
                {
                    "groupStatistics": [
                        {"fieldName": "restBpm",
                         "startDay": 20260101 + i if i < 300 else f"20{26:02d}{i:04d}",
                         "statistics": stats_entry}
                    ]
                }
            )
        # 部分组直接给垃圾结构
        groups.append({"groupStatistics": "no"})
        groups.append(42)
        stats = parse_health_metric_stats(
            {"groupResults": groups}, field_name=None, **_stats_kwargs()
        )
        assert len(stats.daily) > 0  # 合法天保留
        days = [d.day for d in stats.daily]
        assert days == sorted(days)  # 升序契约不破坏

    def test_10000_pb_entries_5pct_poison(self):
        entries = []
        for i in range(10000):
            if i % 20 == 0:
                entries.append({"name": None, "value": float("nan")})  # 无名毒条目
            elif i % 20 == 1:
                entries.append({"name": f"pb-{i}", "value": 10**400})  # 超范围 int
            elif i % 20 == 2:
                entries.append({"name": f"pb-{i}", "value": 1.0, "startTime": True})
            else:
                entries.append({"name": f"pb-{i}", "value": float(i)})
        bests = parse_personal_bests(
            {"sportReports": [{"activityType": "running", "personalBest": entries}]},
            activity_type="running",
        )
        assert len(bests.personal_bests) == 10000 - 500  # 无名条目被跳过
        assert all(b.start_time is None for b in bests.personal_bests)  # True 不伪造时间

    def test_10000_key_predicted_times(self):
        predicted = {}
        for i in range(10000):
            if i % 3 == 0:
                predicted[f"key{i}"] = float("nan")
            elif i % 3 == 1:
                predicted[f"key{i}"] = 10**400
            else:
                predicted[f"key{i}"] = i + 0.5
        perf = parse_athletic_performance({"predictedTimes": predicted})
        assert len(perf.predicted_times) == 3333  # 仅合法数值保留（i%3==2 共 3333 键）
