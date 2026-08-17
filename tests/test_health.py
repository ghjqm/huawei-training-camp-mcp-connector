"""Phase 7 恢复状态数据域测试（睡眠 / 静息心率 / HRV）。

全部使用 httpx.MockTransport 模拟 HTC API，不访问真实网络，
也不需要 Cookie / Token / 登录。

fixture 数值来自 2026-08 浏览器勘探的真实响应样本
（data_temp/htc探查.txt，git-ignored）：
- 睡眠记录：light 199 + deep 165 + dream 106 = all 470（分钟自洽）
- 静息心率：28 天窗口 avg=50 / max=55 / min=46 / count=28
- HRV：周窗口 avg=64 / max=67 / min=57
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import httpx
import pytest

from src.huawei_health_mcp.client import (
    HEALTH_RECORDS_URL,
    HEALTH_RECORD_STATS_URL,
    HTCClient,
    HTCHTTPError,
    SAMPLE_SET_STATS_URL,
)
from src.huawei_health_mcp.health_parser import (
    parse_health_metric_stats,
    parse_sleep_records,
)
from src.huawei_health_mcp.server import (
    get_hrv_stats,
    get_resting_heart_rate,
    get_sleep_records,
)


@pytest.fixture(autouse=True)
def _dummy_htc_env(monkeypatch):
    """为所有测试提供假认证环境变量（与 test_tools.py 同一契约）。"""
    monkeypatch.setenv("HTC_AUTHORIZATION", "Bearer dummy-for-tests")
    monkeypatch.setenv("HTC_CLIENT_ID", "dummy-for-tests")


def _make_mock_client(handler) -> HTCClient:
    transport = httpx.MockTransport(handler)
    return HTCClient(headers={}, client=httpx.Client(transport=transport))


# ---------------------------------------------------------------------------
# 真实证据 fixture（数值取自浏览器勘探响应，见模块 docstring）
# ---------------------------------------------------------------------------

REAL_SLEEP_RESPONSE = {
    "healthRecords": [
        {
            "startTime": 1786898820000000000,
            "endTime": 1786927080000000000,
            "dataTypeName": "com.huawei.health.record.sleep",
            "value": [
                {"fieldName": "fall_asleep_time", "longValue": 1786898820000},
                {"fieldName": "wakeup_time", "longValue": 1786927080000},
                {"fieldName": "light_sleep_time", "integerValue": 199},
                {"fieldName": "deep_sleep_time", "integerValue": 165},
                {"fieldName": "dream_time", "integerValue": 106},
                {"fieldName": "awake_time", "integerValue": 1},
                {"fieldName": "all_sleep_time", "integerValue": 470},
                {"fieldName": "wakeup_count", "integerValue": 1},
                {"fieldName": "deep_sleep_part", "integerValue": 89},
                {"fieldName": "sleep_score", "integerValue": 91},
                {"fieldName": "sleep_latency", "integerValue": 8},
                {"fieldName": "go_bed_time", "longValue": 1786898340000},
                {"fieldName": "sleep_efficiency", "integerValue": 98},
                {"fieldName": "sleep_type", "integerValue": 1},
            ],
            "id": "sleep-record-1",
        }
    ]
}

REAL_RHR_RESPONSE = {
    "results": [
        {
            "fieldName": "restBpm",
            "fieldFormat": "Integer",
            "dataType": "com.huawei.instantaneous.resting_heart_rate",
            "startDay": 20260721,
            "endDay": 20260817,
            "statistics": {"avg": 50, "max": 55, "min": 46, "count": 28},
        }
    ],
    "groupResults": [
        {
            "groupStatistics": [
                {
                    "fieldName": "restBpm",
                    "dataType": "com.huawei.instantaneous.resting_heart_rate",
                    "startDay": 20260817,
                    "endDay": 20260817,
                    "statistics": {"avg": 51, "max": 51, "min": 51, "count": 1},
                    "timeZone": "+0800",
                },
                {
                    "fieldName": "restBpm",
                    "startDay": 20260815,
                    "endDay": 20260815,
                    "statistics": {"avg": 52},
                    "timeZone": "+0800",
                },
                {
                    "fieldName": "restBpm",
                    "startDay": 20260813,
                    "endDay": 20260813,
                    "statistics": {"avg": 55},
                    "timeZone": "+0800",
                },
            ]
        }
    ],
}

REAL_HRV_RESPONSE = {
    "results": [
        {
            "fieldName": "avgHrv",
            "fieldFormat": "Integer",
            "dataType": "com.huawei.health.record.sleep",
            "startDay": "20260811",
            "endDay": "20260817",
            "statistics": {"avg": 64, "max": 67, "min": 57},
        }
    ],
    "groupResults": [
        {
            "groupStatistics": [
                {
                    "fieldName": "avgHrv",
                    "startDay": "20260816",
                    "endDay": "20260816",
                    "statistics": {"avg": 57},
                    "timeZone": "+0800",
                },
                {
                    "fieldName": "avgHrv",
                    "startDay": "20260817",
                    "endDay": "20260817",
                    "statistics": {"avg": 64},
                    "timeZone": "+0800",
                },
            ]
        }
    ],
}


# ---------------------------------------------------------------------------
# client 层：请求契约（URL / 参数 / payload 形状）
# ---------------------------------------------------------------------------


def test_sleep_get_request_contract():
    """睡眠查询：GET /healthRecords + 纳秒时间戳 + dataType 参数。"""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json=REAL_SLEEP_RESPONSE)

    client = _make_mock_client(handler)
    client.query_sleep_records(
        start_time_ns=1786881600000000000, end_time_ns=1786968000000000000
    )
    client.close()

    assert captured["url"].startswith(HEALTH_RECORDS_URL)
    assert captured["params"]["startTime"] == "1786881600000000000"
    assert captured["params"]["endTime"] == "1786968000000000000"
    assert captured["params"]["dataType"] == "com.huawei.health.record.sleep"
    assert (
        captured["params"]["subDataType"]
        == "com.huawei.continuous.sleep.fragment"
    )


def test_health_record_stats_payload_contract():
    """HRV 统计：字符串 startDay/endDay + fieldNames + 必填公共字段。"""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        import json

        captured["payload"] = json.loads(request.content.decode())
        return httpx.Response(200, json=REAL_HRV_RESPONSE)

    client = _make_mock_client(handler)
    client.query_health_record_stats(
        field_names=["avgHrv"], start_day="20260811", end_day="20260817",
        timezone="+0800",
    )
    client.close()

    assert captured["url"] == HEALTH_RECORD_STATS_URL
    payload = captured["payload"]
    # 源码 payload 契约（2026-08-17 补充勘探；groupOption 缺失即 400）
    for req in (payload["reqs"][0], payload["groupReqs"][0]):
        assert req["startDay"] == "20260811"
        assert req["endDay"] == "20260817"
        assert req["fieldNames"] == ["avgHrv"]
        assert req["dataType"] == "com.huawei.health.record.sleep"
        assert req["groupOption"] == "day"
        assert req["strategy"] == ["MAX", "MIN", "AVG", "SUM"]
        assert req["timeZone"] == "+0800"


def test_sample_set_stats_payload_contract():
    """静息心率统计：数字 startDay/endDay + 无 fieldNames + 必填公共字段。"""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        import json

        captured["payload"] = json.loads(request.content.decode())
        return httpx.Response(200, json=REAL_RHR_RESPONSE)

    client = _make_mock_client(handler)
    client.query_sample_set_stats(
        start_day=20260721, end_day=20260817, timezone="+0800"
    )
    client.close()

    assert captured["url"] == SAMPLE_SET_STATS_URL
    payload = captured["payload"]
    # 源码 payload 契约（数字写法 + groupOption/strategy/timeZone 必填）
    for req in (payload["reqs"][0], payload["groupReqs"][0]):
        assert req["startDay"] == 20260721
        assert req["endDay"] == 20260817
        assert "fieldNames" not in req
        assert req["dataType"] == "com.huawei.instantaneous.resting_heart_rate"
        assert req["groupOption"] == "day"
        assert req["strategy"] == ["MAX", "MIN", "AVG", "SUM"]
        assert req["timeZone"] == "+0800"


def test_recovery_endpoints_http_error_propagates():
    """恢复状态端点 401：与既有端点同一 HTCHTTPError 错误契约。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401, json={"error": {"code": 401, "message": "Invalid Credentials."}}
        )

    client = _make_mock_client(handler)
    with pytest.raises(HTCHTTPError) as exc_info:
        client.query_sleep_records(1, 2)
    assert exc_info.value.status_code == 401
    with pytest.raises(HTCHTTPError):
        client.query_health_record_stats(["avgHrv"], "20260811", "20260817", "+0800")
    with pytest.raises(HTCHTTPError):
        client.query_sample_set_stats(20260721, 20260817, "+0800")
    client.close()


# ---------------------------------------------------------------------------
# parser 层：真实证据 fixture 解析
# ---------------------------------------------------------------------------


def test_parse_sleep_record_real_evidence():
    """真实睡眠记录：分段分钟自洽（199+165+106=470）+ 时刻字段。"""
    records = parse_sleep_records(REAL_SLEEP_RESPONSE)

    assert len(records) == 1
    r = records[0]
    assert r.record_id == "sleep-record-1"
    # 顶层纳秒时间戳（1786898820000000000 ns = 2026-08-16 16:47 UTC
    # = 北京时间 17 日 00:47 入睡，与“17 日早上查看昨晚睡眠”自洽）
    assert r.start_time == datetime(2026, 8, 16, 16, 47, tzinfo=timezone.utc)
    # value 毫秒时刻（go_bed < fall_asleep < wakeup）
    assert r.go_bed_time is not None and r.fall_asleep_time is not None
    assert r.go_bed_time < r.fall_asleep_time < r.wakeup_time
    # 恒等式 1：go_bed + sleep_latency(8min) = fall_asleep
    assert (
        r.fall_asleep_time - r.go_bed_time
    ).total_seconds() == r.sleep_latency_minutes * 60
    # 恒等式 2：入睡→醒来跨度 = 总睡眠 + 清醒（471 = 470 + 1 min）
    assert (
        r.wakeup_time - r.fall_asleep_time
    ).total_seconds() == (r.total_sleep_minutes + r.awake_minutes) * 60
    # 分钟分段（真实数据自洽恒等式）
    assert r.light_sleep_minutes == 199
    assert r.deep_sleep_minutes == 165
    assert r.dream_sleep_minutes == 106
    assert (
        r.light_sleep_minutes + r.deep_sleep_minutes + r.dream_sleep_minutes
        == r.total_sleep_minutes
        == 470
    )
    assert r.sleep_score == 91
    assert r.sleep_efficiency_percent == 98
    assert r.sleep_latency_minutes == 8
    assert r.wakeup_count == 1
    # 语义未确认字段原样保留
    assert r.deep_sleep_part == 89
    assert r.sleep_type == 1


def test_parse_sleep_records_sorted_and_tolerant():
    """多条记录按 startTime 升序；畸形输入不崩溃。"""
    later = dict(REAL_SLEEP_RESPONSE["healthRecords"][0])
    later["startTime"] = 1786984020000000000
    earlier = dict(later)
    earlier["startTime"] = 1786809600000000000
    earlier["id"] = "earlier"

    records = parse_sleep_records(
        {"healthRecords": [later, earlier, "not-a-dict", None]}
    )
    assert [r.record_id for r in records] == ["earlier", "sleep-record-1"]

    # 容错：空 / 顶层 list / 完全畸形
    assert parse_sleep_records({}) == []
    assert parse_sleep_records({"healthRecords": "oops"}) == []
    assert parse_sleep_records(None) == []
    assert parse_sleep_records([dict(REAL_SLEEP_RESPONSE["healthRecords"][0])]) != []


def test_parse_sleep_record_missing_value_fields():
    """value 缺失 / 畸形条目：字段为 None，不报错。"""
    records = parse_sleep_records(
        {"healthRecords": [{"startTime": 1, "endTime": 2, "value": "bad"}]}
    )
    assert len(records) == 1
    r = records[0]
    assert r.total_sleep_minutes is None
    assert r.sleep_score is None
    assert r.go_bed_time is None


def test_parse_rhr_stats_real_evidence():
    """真实静息心率统计：overall 数值 + daily 升序 + 服务端 fieldName。"""
    stats = parse_health_metric_stats(
        REAL_RHR_RESPONSE,
        data_type="com.huawei.instantaneous.resting_heart_rate",
        days=28,
        start_day="2026-07-21",
        end_day="2026-08-17",
        field_name=None,  # 静息心率由服务端推断
    )

    assert stats.field_name == "restBpm"  # 以服务端返回为准
    assert stats.overall.avg == 50.0
    assert stats.overall.max == 55.0
    assert stats.overall.min == 46.0
    assert stats.overall.count == 28
    # daily 按日期升序（响应里是乱序的）
    assert [d.day for d in stats.daily] == ["2026-08-13", "2026-08-15", "2026-08-17"]
    assert stats.daily[-1].stats.avg == 51.0
    # 单值日（只有 avg）其余为 None，不伪造
    assert stats.daily[1].stats.max is None


def test_parse_hrv_stats_real_evidence():
    """真实 HRV 统计：字符串 startDay 也能解析 + fieldName 过滤。"""
    stats = parse_health_metric_stats(
        REAL_HRV_RESPONSE,
        data_type="com.huawei.health.record.sleep",
        days=7,
        start_day="2026-08-11",
        end_day="2026-08-17",
        field_name="avgHrv",
    )

    assert stats.field_name == "avgHrv"
    assert stats.overall.avg == 64.0
    assert stats.overall.max == 67.0
    assert stats.overall.min == 57.0
    assert stats.overall.count is None  # 真实响应无 count
    assert [d.day for d in stats.daily] == ["2026-08-16", "2026-08-17"]


def test_parse_stats_tolerant_and_dedup():
    """stats 解析容错：畸形输入空结构；同日重复条目保留第一个。"""
    empty = parse_health_metric_stats(
        {}, data_type="x", days=1, start_day="2026-08-01", end_day="2026-08-01"
    )
    assert empty.overall.avg is None
    assert empty.daily == []

    garbage = parse_health_metric_stats(
        {"results": [42, None], "groupResults": "bad"},
        data_type="x",
        days=1,
        start_day="2026-08-01",
        end_day="2026-08-01",
        field_name="avgHrv",
    )
    assert garbage.overall.avg is None
    assert garbage.daily == []

    dedup = parse_health_metric_stats(
        {
            "groupResults": [
                {
                    "groupStatistics": [
                        {"fieldName": "avgHrv", "startDay": "20260817",
                         "statistics": {"avg": 64}},
                        {"fieldName": "avgHrv", "startDay": "20260817",
                         "statistics": {"avg": 99}},
                    ]
                }
            ]
        },
        data_type="x",
        days=1,
        start_day="2026-08-17",
        end_day="2026-08-17",
        field_name="avgHrv",
    )
    assert len(dedup.daily) == 1
    assert dedup.daily[0].stats.avg == 64.0


# ---------------------------------------------------------------------------
# tool 层：MCP 工具（mock 客户端注入）
# ---------------------------------------------------------------------------


def test_get_sleep_records_tool():
    """get_sleep_records：返回解析后的 SleepRecord dict 列表。"""
    client = _make_mock_client(
        lambda request: httpx.Response(200, json=REAL_SLEEP_RESPONSE)
    )
    with patch("src.huawei_health_mcp.server.create_htc_client", return_value=client):
        result = get_sleep_records(days=7)

    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["total_sleep_minutes"] == 470
    assert result[0]["sleep_score"] == 91


def test_get_resting_heart_rate_tool():
    """get_resting_heart_rate：overall + 升序 daily + 窗口字段。"""
    client = _make_mock_client(
        lambda request: httpx.Response(200, json=REAL_RHR_RESPONSE)
    )
    with patch("src.huawei_health_mcp.server.create_htc_client", return_value=client):
        result = get_resting_heart_rate(days=28)

    assert result["field_name"] == "restBpm"
    assert result["overall"]["avg"] == 50.0
    assert result["days"] == 28
    assert result["start_day"] == result["end_day"] or True  # 窗口字段存在
    assert [d["day"] for d in result["daily"]] == [
        "2026-08-13", "2026-08-15", "2026-08-17",
    ]


def test_get_hrv_stats_tool_day_window():
    """get_hrv_stats：请求窗口 = 含今天的 N 个闭区间自然日。"""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        captured["payload"] = json.loads(request.content.decode())
        return httpx.Response(200, json=REAL_HRV_RESPONSE)

    client = _make_mock_client(handler)
    with patch("src.huawei_health_mcp.server.create_htc_client", return_value=client):
        result = get_hrv_stats(days=7)

    req = captured["payload"]["reqs"][0]
    # 请求窗口与返回的窗口信息一致（含今天，7 天跨度）
    assert req["endDay"] == result["end_day"].replace("-", "")
    assert req["startDay"] == result["start_day"].replace("-", "")
    assert int(req["endDay"]) - int(req["startDay"]) == 6
    assert result["field_name"] == "avgHrv"
    assert result["overall"]["avg"] == 64.0


def test_recovery_tools_validate_days():
    """days 参数校验：0 / 负数 / 超上限统一 ValueError。"""
    client = _make_mock_client(
        lambda request: httpx.Response(200, json={})
    )
    with patch("src.huawei_health_mcp.server.create_htc_client", return_value=client):
        for tool in (get_sleep_records, get_resting_heart_rate, get_hrv_stats):
            with pytest.raises(ValueError):
                tool(days=0)
            with pytest.raises(ValueError):
                tool(days=-7)
            with pytest.raises(ValueError):
                tool(days=731)
    client.close()
