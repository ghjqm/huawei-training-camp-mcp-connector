"""get_recent_activities 工具的单元测试。

全部使用 httpx.MockTransport 模拟 HTC API，不访问真实网络，
也不需要 Cookie / Token / 登录。
"""

from __future__ import annotations

import json
from unittest.mock import patch

import httpx

from src.huawei_health_mcp.client import HTCClient, HTCClientError
from src.huawei_health_mcp.server import (
    get_recent_activities,
    health_check,
)


def _make_mock_client(handler) -> HTCClient:
    """用 MockTransport 构造一个不联网的 HTCClient。"""
    transport = httpx.MockTransport(handler)
    return HTCClient(headers={}, client=httpx.Client(transport=transport))


def test_get_recent_activities_returns_parsed_records():
    """Test1：工具能返回解析后的 ActivityRecord 数据。"""
    body = {
        "activityRecords": [
            {"activityId": "act-1", "activityType": "56"},
            {"activityId": "act-2", "activityType": "56"},
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    mock_client = _make_mock_client(handler)
    with patch(
        "src.huawei_health_mcp.server.create_htc_client", return_value=mock_client
    ):
        result = get_recent_activities(limit=5)

    assert isinstance(result, list)
    assert len(result) == 2
    assert result[0]["activity_id"] == "act-1"
    assert result[1]["activity_id"] == "act-2"
    # 缺失字段被 parser 填成 None / 空列表，而不是报错
    assert result[0]["avg_heart_rate"] is None


def test_limit_passed_to_client():
    """Test2：limit 参数能够正确传递到 HTCClient。"""
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content.decode())
        return httpx.Response(200, json={"activityRecords": []})

    mock_client = _make_mock_client(handler)
    with patch(
        "src.huawei_health_mcp.server.create_htc_client", return_value=mock_client
    ):
        get_recent_activities(limit=3)

    assert captured["payload"]["limit"] == 3
    # 时间范围也应被传入（毫秒级字符串）
    assert "startTime" in captured["payload"]
    assert "endTime" in captured["payload"]


def test_client_http_error_propagates():
    """Test3：HTCClient 的 HTTP 错误能清晰向上层传播。"""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="forbidden")

    mock_client = _make_mock_client(handler)
    with patch(
        "src.huawei_health_mcp.server.create_htc_client", return_value=mock_client
    ):
        try:
            get_recent_activities(limit=5)
            raised = None
        except HTCClientError as exc:
            raised = exc

    assert raised is not None
    assert "403" in str(raised)


def test_health_check_still_works():
    """Test4：已有的 health_check 工具仍然正常。"""
    result = health_check()
    assert result["status"] == "ok"
    assert result["service"] == "huawei-training-camp-mcp"
    assert result["version"] == "0.1.0"

# ---------------- lookback_days（Phase 6.4） ----------------

def _capture_list_payload(tool_call) -> dict:
    """跑一次工具调用并返回捕获到的列表请求 payload。"""
    captured: dict = {}
    body = {"activityRecords": [{"activityId": "act-1", "activityType": "56"}]}

    def handler(request):
        captured["payload"] = json.loads(request.content.decode())
        return httpx.Response(200, json=body)

    mock_client = HTCClient(
        headers={}, client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    with patch(
        "src.huawei_health_mcp.server.create_htc_client", return_value=mock_client
    ):
        tool_call()
    return captured["payload"]


def test_default_lookback_30_days():
    """不传 lookback_days 时保持旧默认：startTime ≈ 30 天前。"""
    import time

    payload = _capture_list_payload(lambda: get_recent_activities())
    start_ms = int(payload["startTime"])
    expected = (time.time() - 30 * 86400) * 1000
    # 允许 60 秒时钟漂移
    assert abs(start_ms - expected) < 60_000


def test_custom_lookback_days_passed_to_client():
    """lookback_days=7 正确生效：startTime ≈ 7 天前。"""
    import time

    payload = _capture_list_payload(lambda: get_recent_activities(lookback_days=7))
    start_ms = int(payload["startTime"])
    expected = (time.time() - 7 * 86400) * 1000
    assert abs(start_ms - expected) < 60_000
    # limit 行为不受影响
    assert payload["limit"] == 5


def test_lookback_days_validation():
    """非法窗口（0 / 负数 / 超过 730）抛 ValueError，不发请求。"""
    import pytest

    for bad in (0, -5, 731, 100000):
        with pytest.raises(ValueError) as exc_info:
            get_recent_activities(lookback_days=bad)
        assert "lookback_days" in str(exc_info.value)

# ---------------- limit 边界校验（Phase 6.5） ----------------

def test_limit_validation():
    """非法 limit（0 / 负数 / 超过 100）抛 ValueError，不发请求。"""
    import pytest

    for bad in (0, -1, 101, 100000):
        with pytest.raises(ValueError) as exc_info:
            get_recent_activities(limit=bad)
        assert "limit" in str(exc_info.value)


def test_limit_upper_bound_accepted():
    """合法边界值 limit=100 正常执行（不发请求错误）。"""
    payload = _capture_list_payload(lambda: get_recent_activities(limit=100))
    assert payload["limit"] == 100
