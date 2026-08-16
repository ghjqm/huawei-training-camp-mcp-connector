"""HTCClient 的最小测试：用 httpx.MockTransport 模拟 HTTP，绝不访问真实网络。

运行方式（项目根目录）：
    pytest
完全断网情况下也应全部通过。
"""

import json

import httpx
import pytest

from src.huawei_health_mcp.client import (
    ACTIVITY_RECORD_QUERY_URL,
    HTCClient,
    HTCHTTPError,
    HTCResponseError,
)


def _make_client(handler, headers=None):
    """用 MockTransport 构建一个 HTCClient，不连网。"""
    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(transport=transport, headers=headers or {})
    return HTCClient(headers=headers, client=http_client)


# ---------------- Test 1：正常 JSON 响应 ----------------

def test_query_activity_records_returns_dict():
    def handler(request):
        return httpx.Response(200, json={"records": [{"id": "1"}]})

    client = _make_client(handler)
    result = client.query_activity_records(start_time="1", end_time="2")
    assert isinstance(result, dict)
    assert result["records"][0]["id"] == "1"


def test_query_activity_detail_returns_dict():
    def handler(request):
        return httpx.Response(200, json={"detail": {"ok": True}})

    client = _make_client(handler)
    result = client.query_activity_detail(start_time="1", end_time="2")
    assert isinstance(result, dict)
    assert result["detail"]["ok"] is True


# ---------------- Test 2：HTTP 非 2xx ----------------

def test_http_401_raises():
    def handler(request):
        return httpx.Response(401, text="unauthorized")

    client = _make_client(handler)
    with pytest.raises(HTCHTTPError) as exc:
        client.query_activity_records(start_time="1", end_time="2")
    assert exc.value.status_code == 401


def test_http_500_raises():
    def handler(request):
        return httpx.Response(500, text="server error")

    client = _make_client(handler)
    with pytest.raises(HTCHTTPError) as exc:
        client.query_activity_detail(start_time="1", end_time="2")
    assert exc.value.status_code == 500


# ---------------- Test 3：无效 JSON ----------------

def test_invalid_json_raises():
    def handler(request):
        return httpx.Response(200, text="{not valid json")

    client = _make_client(handler)
    with pytest.raises(HTCResponseError):
        client.query_activity_records(start_time="1", end_time="2")


# ---------------- Test 4：payload 正确传递 ----------------

def test_records_payload_and_headers():
    captured: dict = {}

    def handler(request):
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["json"] = request.content.decode()
        return httpx.Response(200, json={"ok": True})

    headers = {"X-Demo": "demo-value"}
    client = _make_client(handler, headers=headers)
    client.query_activity_records(
        start_time="1786706473000",
        end_time="1786709186000",
        limit=5,
        order="endTimeDesc",
    )

    # URL 正确
    assert captured["url"] == ACTIVITY_RECORD_QUERY_URL
    # headers 被正确发送（httpx 头名大小写不敏感）
    assert captured["headers"].get("x-demo") == "demo-value"
    # payload 没有被偷偷修改
    body = json.loads(captured["json"])
    assert body["startTime"] == "1786706473000"
    assert body["endTime"] == "1786709186000"
    assert body["limit"] == 5
    assert body["order"] == "endTimeDesc"


def test_detail_payload_and_headers():
    captured: dict = {}

    def handler(request):
        captured["json"] = request.content.decode()
        return httpx.Response(200, json={"ok": True})

    detail_types = ["com.huawei.instantaneous.exercise_heart_rate"]
    client = _make_client(handler)
    client.query_activity_detail(
        start_time="1786706473000",
        end_time="1786709186000",
        activity_type="56",
        detail_data_types=detail_types,
        high_freq_details_preferred=True,
    )

    body = json.loads(captured["json"])
    assert body["activityType"] == "56"
    assert body["detailDataType"] == detail_types
    assert body["highFreqDetailsPreferred"] is True


# ---------------- Test 8：真实契约：detail 响应顶层是 list ----------------

def test_detail_response_top_level_list():
    """6.1 真实观测：detail 响应顶层为 list，client 应原样返回。

    对应 query_activity_detail 的返回 annotation：dict | list。
    """
    body = [{"id": "a1", "details": []}]

    def handler(request):
        return httpx.Response(200, json=body)

    client = _make_client(handler)
    result = client.query_activity_detail(start_time="1", end_time="2")
    assert isinstance(result, list)
    assert result == body