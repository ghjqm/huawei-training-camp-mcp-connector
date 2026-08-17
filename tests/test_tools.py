"""get_recent_activities 工具的单元测试。

全部使用 httpx.MockTransport 模拟 HTC API，不访问真实网络，
也不需要 Cookie / Token / 登录。
"""

from __future__ import annotations

import json
from unittest.mock import patch

import httpx
import pytest

from src.huawei_health_mcp.client import HTCClient, HTCClientError
from src.huawei_health_mcp.server import (
    get_recent_activities,
    health_check,
)


@pytest.fixture(autouse=True)
def _dummy_htc_env(monkeypatch):
    """为所有测试提供假认证环境变量（请求全部走 Mock，绝不联网）。

    Phase 6.14 起 create_htc_client 对缺失的必填凭证 fail-fast，
    参数校验类测试需要环境变量存在才能走到被测逻辑；
    同时保证套件在有真实凭证的宿主终端里行为一致。
    """
    monkeypatch.setenv("HTC_AUTHORIZATION", "Bearer dummy-for-tests")
    monkeypatch.setenv("HTC_CLIENT_ID", "dummy-for-tests")


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


# ---------------- 认证头构造（Phase 6.14 修复回归） ----------------

_ALL_ENV_VARS = (
    "HTC_AUTHORIZATION",
    "HTC_CLIENT_ID",
    "HTC_VERSION",
    "HTC_COOKIE",
)


def test_build_htc_headers_maps_all_four_vars(monkeypatch):
    """四个环境变量齐全时，全部映射为对应 HTTP 请求头。"""
    from src.huawei_health_mcp.server import build_htc_headers

    for name in _ALL_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HTC_AUTHORIZATION", "Bearer test-auth")
    monkeypatch.setenv("HTC_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("HTC_VERSION", "HealthKitRunningGroupPortal_6.26.4.200")
    monkeypatch.setenv("HTC_COOKIE", "test-cookie")

    headers = build_htc_headers()

    assert headers == {
        "Authorization": "Bearer test-auth",
        "x-client-id": "test-client-id",
        "x-version": "HealthKitRunningGroupPortal_6.26.4.200",
        "Cookie": "test-cookie",
    }


def test_build_htc_headers_required_missing_raises(monkeypatch):
    """必填变量缺失时清晰报错（指明变量名），而不是发出注定 401 的请求。"""
    import pytest

    from src.huawei_health_mcp.server import build_htc_headers

    for name in _ALL_ENV_VARS:
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ValueError) as exc_info:
        build_htc_headers()
    message = str(exc_info.value)
    assert "HTC_AUTHORIZATION" in message
    assert "HTC_CLIENT_ID" in message


def test_build_htc_headers_optional_omitted(monkeypatch):
    """可选变量（HTC_VERSION / HTC_COOKIE）缺失时对应 Header 省略。"""
    from src.huawei_health_mcp.server import build_htc_headers

    for name in _ALL_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HTC_AUTHORIZATION", "Bearer test-auth")
    monkeypatch.setenv("HTC_CLIENT_ID", "test-client-id")

    headers = build_htc_headers()

    assert headers == {
        "Authorization": "Bearer test-auth",
        "x-client-id": "test-client-id",
    }
