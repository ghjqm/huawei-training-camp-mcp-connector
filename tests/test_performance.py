"""Phase 8 训练能力域测试（运动能力评估 / PB 个人纪录）。

全部使用 httpx.MockTransport 模拟 HTC API，不访问真实网络，
也不需要 Cookie / Token / 登录。

fixture 数值来自 2026-08 浏览器勘探的真实响应样本
（data_temp/htc探查.txt，git-ignored）：
- athleticPerformance：runningAbility 41.9 / condition -2.0833 /
  predictedTimes 六距离（km1 456 .. marathon 14028 秒）
- sportReports：bestRunPartTimeHM 7303.189s / 10KM 2746s /
  bestRunDistance 26220m
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import httpx
import pytest

from src.huawei_health_mcp.client import (
    ATHLETIC_PERFORMANCE_URL,
    HTCClient,
    HTCHTTPError,
    SPORT_REPORTS_URL,
)
from src.huawei_health_mcp.performance_parser import (
    parse_athletic_performance,
    parse_personal_bests,
)
from src.huawei_health_mcp.server import (
    get_athletic_performance,
    get_personal_bests,
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

REAL_PERFORMANCE_RESPONSE = {
    "runningAbility": 41.9,
    "condition": -2.0833,
    "fitness": 10.8454,
    "fatigue": 12.9287,
    "ranking": 71.9753,
    "predictedTimes": {
        "km1": 456,
        "km3": 875,
        "km5": 1499,
        "km10": 3074,
        "halfMarathon": 6701,
        "marathon": 14028,
    },
}

REAL_PB_RESPONSE = {
    "resultCode": 0,
    "sportReports": [
        {
            "activityType": "running",
            "personalBest": [
                {
                    "name": "bestRunPartTimeHM",
                    "value": 7303.189,
                    "startTime": 1777860415125,
                    "endTime": 1777868840249,
                },
                {
                    "name": "bestRunPartTime10KM",
                    "value": 2746.0,
                    "startTime": 1785024087000,
                    "endTime": 1785026863000,
                },
                {
                    "name": "bestRunDistance",
                    "value": 26220.0,
                    "startTime": 1743149407000,
                },
            ],
        }
    ],
}


# ---------------------------------------------------------------------------
# client 层：请求契约
# ---------------------------------------------------------------------------


def test_athletic_performance_request_contract():
    """运动能力：GET athleticPerformance/latest + timeZone 参数。"""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json=REAL_PERFORMANCE_RESPONSE)

    client = _make_mock_client(handler)
    client.query_athletic_performance(timezone="+0800")
    client.close()

    assert captured["url"].startswith(ATHLETIC_PERFORMANCE_URL)
    assert captured["params"] == {"timeZone": "+0800"}


def test_sport_reports_request_contract():
    """PB：GET sportReports + activityType 参数。"""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json=REAL_PB_RESPONSE)

    client = _make_mock_client(handler)
    client.query_sport_reports(activity_type="running")
    client.close()

    assert captured["url"].startswith(SPORT_REPORTS_URL)
    assert captured["params"] == {"activityType": "running"}


def test_performance_endpoints_http_error_propagates():
    """训练能力端点 401：与既有端点同一 HTCHTTPError 错误契约。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401, json={"error": {"code": 401, "message": "Invalid Credentials."}}
        )

    client = _make_mock_client(handler)
    with pytest.raises(HTCHTTPError) as exc_info:
        client.query_athletic_performance(timezone="+0800")
    assert exc_info.value.status_code == 401
    with pytest.raises(HTCHTTPError):
        client.query_sport_reports(activity_type="running")
    client.close()


# ---------------------------------------------------------------------------
# parser 层：真实证据 fixture 解析
# ---------------------------------------------------------------------------


def test_parse_athletic_performance_real_evidence():
    """真实运动能力：五项指数 + 六距离预测成绩（秒）。"""
    perf = parse_athletic_performance(REAL_PERFORMANCE_RESPONSE)

    assert perf.running_ability == 41.9
    assert perf.condition == -2.0833  # 有正负（疲劳累积为负）
    assert perf.fitness == 10.8454
    assert perf.fatigue == 12.9287
    assert perf.ranking == 71.9753
    assert perf.predicted_times == {
        "km1": 456.0,
        "km3": 875.0,
        "km5": 1499.0,
        "km10": 3074.0,
        "halfMarathon": 6701.0,
        "marathon": 14028.0,
    }
    # 量级自洽：半马 6701s≈1:51:41、全马 14028s≈3:53:48（业余跑者）
    assert 6000 < perf.predicted_times["halfMarathon"] < 7500
    assert perf.predicted_times["marathon"] < 2.1 * perf.predicted_times["halfMarathon"]


def test_parse_athletic_performance_tolerant():
    """运动能力解析容错：畸形输入全空模型，不崩溃。"""
    empty = parse_athletic_performance({})
    assert empty.running_ability is None
    assert empty.predicted_times == {}

    for garbage in (None, [], "x", 42):
        result = parse_athletic_performance(garbage)
        assert result.running_ability is None
        assert result.predicted_times == {}

    # predictedTimes 值为垃圾时跳过该键，不整体失败
    dirty = parse_athletic_performance(
        {"runningAbility": "bad", "predictedTimes": {"km1": 456, "km3": "oops",
                                                     "km5": True}}
    )
    assert dirty.running_ability is None
    assert dirty.predicted_times == {"km1": 456.0}


def test_parse_personal_bests_real_evidence():
    """真实 PB：三条纪录 + 名称原样 + 达成时段毫秒时刻。"""
    bests = parse_personal_bests(REAL_PB_RESPONSE, activity_type="running")

    assert bests.activity_type == "running"
    assert len(bests.personal_bests) == 3
    by_name = {b.name: b for b in bests.personal_bests}

    hm = by_name["bestRunPartTimeHM"]
    assert hm.value == 7303.189  # 秒（≈2:01:43）
    # 1777860415125 ms = 2026-05-04 02:06:55.125 UTC
    assert hm.start_time == datetime(2026, 5, 4, 2, 6, 55, 125000, tzinfo=timezone.utc)
    assert hm.end_time is not None and hm.end_time > hm.start_time

    tenk = by_name["bestRunPartTime10KM"]
    assert tenk.value == 2746.0  # 秒（45:46）

    dist = by_name["bestRunDistance"]
    assert dist.value == 26220.0  # 米
    assert dist.start_time is not None
    assert dist.end_time is None  # 真实样本该条无 endTime → None


def test_parse_personal_bests_tolerant():
    """PB 解析容错：结构异常空列表；name 非 str 跳过该条。"""
    empty = parse_personal_bests({}, activity_type="running")
    assert empty.personal_bests == []

    for garbage in (None, [], "x", {"sportReports": "bad"}):
        result = parse_personal_bests(garbage, activity_type="running")
        assert result.personal_bests == []

    # activityType 不匹配 → 空（服务端返回其他类型的报表）
    mismatch = parse_personal_bests(REAL_PB_RESPONSE, activity_type="cycling")
    assert mismatch.personal_bests == []

    # 畸形条目跳过，合法条目保留
    dirty = parse_personal_bests(
        {
            "sportReports": [
                {
                    "activityType": "running",
                    "personalBest": [
                        "not-a-dict",
                        {"value": 1.0},               # 无 name → 跳过
                        {"name": "", "value": 2.0},   # 空 name → 跳过
                        {"name": "ok", "value": "x", "startTime": "bad"},
                    ],
                }
            ]
        },
        activity_type="running",
    )
    assert len(dirty.personal_bests) == 1
    assert dirty.personal_bests[0].name == "ok"
    assert dirty.personal_bests[0].value is None
    assert dirty.personal_bests[0].start_time is None


# ---------------------------------------------------------------------------
# tool 层：MCP 工具（mock 客户端注入）
# ---------------------------------------------------------------------------


def test_get_athletic_performance_tool():
    """get_athletic_performance：五项指数 + 预测成绩字典。"""
    client = _make_mock_client(
        lambda request: httpx.Response(200, json=REAL_PERFORMANCE_RESPONSE)
    )
    with patch("src.huawei_health_mcp.server.create_htc_client", return_value=client):
        result = get_athletic_performance()

    assert result["running_ability"] == 41.9
    assert result["condition"] == -2.0833
    assert result["predicted_times"]["marathon"] == 14028.0


def test_get_personal_bests_tool():
    """get_personal_bests：activity_type 透传 + 纪录列表。"""
    client = _make_mock_client(
        lambda request: httpx.Response(200, json=REAL_PB_RESPONSE)
    )
    with patch("src.huawei_health_mcp.server.create_htc_client", return_value=client):
        result = get_personal_bests(activity_type="running")

    assert result["activity_type"] == "running"
    names = [b["name"] for b in result["personal_bests"]]
    assert "bestRunDistance" in names
    assert result["personal_bests"][0]["value"] == 7303.189


def test_get_personal_bests_validates_activity_type():
    """activity_type 空 / 非字符串：统一 ValueError。"""
    client = _make_mock_client(
        lambda request: httpx.Response(200, json={})
    )
    with patch("src.huawei_health_mcp.server.create_htc_client", return_value=client):
        with pytest.raises(ValueError):
            get_personal_bests(activity_type="")
        with pytest.raises(ValueError):
            get_personal_bests(activity_type="   ")
    client.close()
