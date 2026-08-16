"""HTC（华为训练营 / Huawei Health）数据获取层。

职责：用 httpx 向 HTC API 发送请求，返回原始 JSON dict。
这一层只负责"取数据"，不解析成 ActivityRecord（那是 parser 的事），
也不做任何登录、Cookie 自动获取/刷新、Token 处理或绕过访问控制。

【合规要求】
- 本文件不写入任何真实 Cookie / Token / Authorization。
- headers 必须由调用方通过构造函数传入；未来可从环境变量或配置文件传入。
- 不猜测华为的认证机制，不实现登录流程，不绕过任何权限或访问控制。
"""

from __future__ import annotations

from typing import Any, Optional

import httpx

# API 地址集中管理，避免在方法里到处硬编码 URL
ACTIVITY_RECORD_QUERY_URL = (
    "https://hihealthbase-drcn.things.dbankcloud.cn/healthrunninggroup/v1/activityRecord:query"
)


class HTCClientError(Exception):
    """HTC 客户端所有异常的基类（网络层错误也归到这里）。"""


class HTCHTTPError(HTCClientError):
    """HTTP 状态码非 2xx 时抛出。"""

    def __init__(self, status_code: int, url: str, message: str = ""):
        self.status_code = status_code
        self.url = url
        super().__init__(
            f"HTC HTTP 请求失败：status={status_code}, url={url}"
            + (f", 详情={message}" if message else "")
        )


class HTCResponseError(HTCClientError):
    """HTTP 成功（2xx）但响应体不是合法 JSON 时抛出。"""

    def __init__(self, url: str, message: str = ""):
        self.url = url
        super().__init__(
            f"HTC 响应不是合法 JSON：url={url}"
            + (f", 详情={message}" if message else "")
        )


class HTCClient:
    """最小 HTC 数据获取客户端。

    用法：
        client = HTCClient(headers=my_headers, timeout=10.0)
        records = client.query_activity_records(start_time="...", end_time="...")

    说明：
    - headers 必须由调用方提供，本类不从任何地方自动读取认证信息。
    - 允许注入一个 httpx.Client（测试时用 MockTransport），默认新建一个。
    - 所有方法只返回原始 JSON dict，不解析为 ActivityRecord。
    """

    def __init__(
        self,
        headers: Optional[dict[str, str]] = None,
        timeout: float = 30.0,
        url: str = ACTIVITY_RECORD_QUERY_URL,
        client: Optional[httpx.Client] = None,
    ):
        self._headers = dict(headers or {})
        self._timeout = timeout
        self._url = url
        # 允许注入 httpx.Client（测试用 MockTransport）；否则用传入的 headers/timeout 新建
        self._client = client or httpx.Client(
            headers=self._headers, timeout=self._timeout
        )

    def _post(self, payload: dict[str, Any]) -> dict[str, Any] | list[Any]:
        """发送 POST，检查 HTTP 状态，解析 JSON，返回 dict。"""
        try:
            response = self._client.post(self._url, json=payload)
        except httpx.HTTPError as exc:  # 网络层错误（超时、DNS、连接失败等）
            raise HTCClientError(f"HTC 请求发生网络错误：{exc}") from exc

        # 非 2xx：明确抛异常，绝不静默返回错误 JSON
        if response.status_code < 200 or response.status_code >= 400:
            raise HTCHTTPError(
                status_code=response.status_code,
                url=self._url,
                message=(response.text or "")[:200],
            )

        # 2xx 但响应体不是合法 JSON
        try:
            return response.json()
        except ValueError as exc:
            raise HTCResponseError(url=self._url, message=str(exc)) from exc

    def query_activity_records(
        self,
        start_time: str,
        end_time: str,
        limit: int = 5,
        order: str = "endTimeDesc",
    ) -> dict[str, Any] | list[Any]:
        """查询活动列表（最近训练）。

        start_time / end_time 使用 HTC 约定的毫秒级字符串时间戳。
        返回原始 JSON dict，不解析为 ActivityRecord。
        """
        payload = {
            "startTime": start_time,
            "endTime": end_time,
            "limit": limit,
            "order": order,
        }
        return self._post(payload)

    def query_activity_detail(
        self,
        start_time: str,
        end_time: str,
        activity_type: str = "56",
        detail_data_types: Optional[list[str]] = None,
        high_freq_details_preferred: bool = True,
    ) -> dict[str, Any] | list[Any]:
        """查询单次活动详情（高频采样数据）。

        6.1 真实观测：响应顶层为 list（契约见返回 annotation）。

        返回原始 JSON dict，不解析为 ActivityRecord。
        """
        payload = {
            "startTime": start_time,
            "endTime": end_time,
            "activityType": activity_type,
            "detailDataType": list(detail_data_types or []),
            "highFreqDetailsPreferred": high_freq_details_preferred,
        }
        return self._post(payload)

    def close(self) -> None:
        """关闭底层 httpx 客户端。"""
        self._client.close()

    def __enter__(self) -> "HTCClient":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()
