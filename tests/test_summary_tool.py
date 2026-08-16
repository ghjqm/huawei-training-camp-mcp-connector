"""get_training_summary MCP 工具测试（Phase 6.3）。

复用 test_detail_tool 的 two-phase MockTransport 模式（列表定位 + 详情查询），
不访问真实网络、不需要 Cookie。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from src.huawei_health_mcp.client import HTCClient, HTCHTTPError
from src.huawei_health_mcp.server import get_training_summary

FIXTURE = Path(__file__).parent / "fixtures" / "sample_activity_detail.json"
TARGET_ID = "synthetic-detail-0001"
RECORDS_BODY = [
    {"id": TARGET_ID, "activityType": 56,
     "startTime": 1786706473000, "endTime": 1786709186000},
]


def _make_mock_client(handler) -> HTCClient:
    transport = httpx.MockTransport(handler)
    return HTCClient(headers={}, client=httpx.Client(transport=transport))


def test_summary_full_chain():
    """全链路：列表定位 → 详情 → parse → 统计 → 序列化 dict。"""
    detail_body = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode())
        if "detailDataType" in payload:
            return httpx.Response(200, json=detail_body)
        return httpx.Response(200, json=RECORDS_BODY)

    mock_client = _make_mock_client(handler)
    with patch(
        "src.huawei_health_mcp.server.create_htc_client", return_value=mock_client
    ):
        result = get_training_summary(activity_id=TARGET_ID)

    assert isinstance(result, dict)
    assert result["activity_id"] == TARGET_ID
    assert len(result["collectors"]) == 3

    hr = result["collectors"][0]
    assert hr["sample_count"] == 2
    bpm = hr["fields"][0]
    assert bpm["field_name"] == "bpm"
    assert bpm["min_value"] == 100.0
    assert bpm["max_value"] == 110.0
    assert bpm["avg_value"] == 105.0

    assert result["collectors"][2]["sample_count"] == 0


def test_summary_error_propagates():
    """底层错误（401）向上清晰传播，不变味。"""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="unauthorized")

    mock_client = _make_mock_client(handler)
    with patch(
        "src.huawei_health_mcp.server.create_htc_client", return_value=mock_client
    ):
        with pytest.raises(HTCHTTPError) as exc_info:
            get_training_summary(activity_id=TARGET_ID)

    assert "401" in str(exc_info.value)