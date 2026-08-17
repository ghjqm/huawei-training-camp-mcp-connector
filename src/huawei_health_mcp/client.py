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
_BASE_URL = "https://hihealthbase-drcn.things.dbankcloud.cn/healthrunninggroup/v1"

ACTIVITY_RECORD_QUERY_URL = f"{_BASE_URL}/activityRecord:query"

# Phase 7 恢复状态数据域端点（2026-08 浏览器勘探证据确认，
# 请求/响应样本见 data_temp/htc探查.txt，git-ignored）
HEALTH_RECORDS_URL = f"{_BASE_URL}/healthRecords"
HEALTH_RECORD_STATS_URL = f"{_BASE_URL}/healthRecords/periodStatistics:calculate"
SAMPLE_SET_STATS_URL = f"{_BASE_URL}/sampleSet/periodStatistics:calculate"

# Phase 8 训练能力域端点（同批勘探证据确认，纯 GET）
ATHLETIC_PERFORMANCE_URL = f"{_BASE_URL}/athleticPerformance/latest"
SPORT_REPORTS_URL = f"{_BASE_URL}/sportReports"

# 数据类型常量（与浏览器成功请求逐字一致）
SLEEP_RECORD_DATA_TYPE = "com.huawei.health.record.sleep"
SLEEP_FRAGMENT_SUB_DATA_TYPE = "com.huawei.continuous.sleep.fragment"
RESTING_HR_DATA_TYPE = "com.huawei.instantaneous.resting_heart_rate"

# 统计请求公共字段（2026-08-17 源码模式 payload 证据确认；
# groupOption 缺失时服务端 400 "[groupReqs[0].groupOption]must not be null"）
STATS_STRATEGY = ["MAX", "MIN", "AVG", "SUM"]
GROUP_OPTION_DAY = "day"


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

    def _post(
        self,
        payload: dict[str, Any],
        url: Optional[str] = None,
    ) -> dict[str, Any] | list[Any]:
        """发送 POST，检查 HTTP 状态，解析 JSON，返回 dict。

        url 缺省用 self._url（activityRecord:query）；Phase 7 起多个
        端点共用一个客户端实例，调用方可显式传入端点 URL。
        """
        request_url = url or self._url
        try:
            response = self._client.post(request_url, json=payload)
        except httpx.HTTPError as exc:  # 网络层错误（超时、DNS、连接失败等）
            raise HTCClientError(f"HTC 请求发生网络错误：{exc}") from exc

        # 非 2xx：明确抛异常，绝不静默返回错误 JSON
        if response.status_code < 200 or response.status_code >= 400:
            raise HTCHTTPError(
                status_code=response.status_code,
                url=request_url,
                message=(response.text or "")[:200],
            )

        # 2xx 但响应体不是合法 JSON
        try:
            return response.json()
        except ValueError as exc:
            raise HTCResponseError(url=request_url, message=str(exc)) from exc

    def _get(
        self,
        url: str,
        params: dict[str, Any],
    ) -> dict[str, Any] | list[Any]:
        """发送 GET（带 query 参数），检查 HTTP 状态，解析 JSON。

        错误契约与 _post 完全一致（Phase 7 新增，供 /healthRecords
        这类 GET 端点使用）。
        """
        try:
            response = self._client.get(url, params=params)
        except httpx.HTTPError as exc:
            raise HTCClientError(f"HTC 请求发生网络错误：{exc}") from exc

        if response.status_code < 200 or response.status_code >= 400:
            raise HTCHTTPError(
                status_code=response.status_code,
                url=str(response.request.url),
                message=(response.text or "")[:200],
            )

        try:
            return response.json()
        except ValueError as exc:
            raise HTCResponseError(url=url, message=str(exc)) from exc

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

    def query_sleep_records(
        self,
        start_time_ns: int,
        end_time_ns: int,
        data_type: str = SLEEP_RECORD_DATA_TYPE,
        sub_data_type: str = SLEEP_FRAGMENT_SUB_DATA_TYPE,
    ) -> dict[str, Any] | list[Any]:
        """查询睡眠健康记录（Phase 7，GET /healthRecords）。

        浏览器勘探证据契约：
        - startTime / endTime 为纳秒时间戳（1e18 量级，与活动列表的
          毫秒字符串不同）
        - dataType=com.huawei.health.record.sleep
        - subDataType=com.huawei.continuous.sleep.fragment（浏览器
          成功请求原样携带；fragment 子结构本层不解析）
        返回原始 JSON dict，不解析为 SleepRecord。
        """
        params = {
            "startTime": start_time_ns,
            "endTime": end_time_ns,
            "dataType": data_type,
            "subDataType": sub_data_type,
        }
        return self._get(HEALTH_RECORDS_URL, params)

    @staticmethod
    def _stats_req(
        data_type: str,
        start_day: Any,
        end_day: Any,
        timezone: str,
        field_names: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """构造 periodStatistics 请求条目（源码 payload 契约）。

        字段顺序与浏览器一致：dataType, [fieldNames], startDay, endDay,
        strategy, groupOption, timeZone。
        - groupOption="day"：必填（缺失即 400）
        - strategy 四则统计（MAX/MIN/AVG/SUM）
        - fieldNames 仅 healthRecords 族携带；sampleSet 静息心率
          请求证据中无此键（fieldName 由服务端推断）
        """
        req: dict[str, Any] = {"dataType": data_type}
        if field_names is not None:
            req["fieldNames"] = list(field_names)
        req.update(
            {
                "startDay": start_day,
                "endDay": end_day,
                "strategy": list(STATS_STRATEGY),
                "groupOption": GROUP_OPTION_DAY,
                "timeZone": timezone,
            }
        )
        return req

    def query_health_record_stats(
        self,
        field_names: list[str],
        start_day: str,
        end_day: str,
        timezone: str,
        data_type: str = SLEEP_RECORD_DATA_TYPE,
    ) -> dict[str, Any] | list[Any]:
        """按天统计健康记录字段（Phase 7，POST healthRecords stats）。

        浏览器源码 payload 契约（avgHrv 请求确认）：
        - startDay / endDay 为 "YYYYMMDD" 字符串（与 sampleSet 端点
          的数字写法不同，两者不混用）
        - strategy / groupOption / timeZone 必填（groupOption="day"）
        - reqs 汇总统计 + groupReqs 按天分组，两者同构
        返回原始 JSON dict（results + groupResults）。
        """
        req = self._stats_req(
            data_type, start_day, end_day, timezone, field_names=field_names
        )
        return self._post(
            {"reqs": [req], "groupReqs": [dict(req)]},
            url=HEALTH_RECORD_STATS_URL,
        )

    def query_sample_set_stats(
        self,
        start_day: int,
        end_day: int,
        timezone: str,
        data_type: str = RESTING_HR_DATA_TYPE,
    ) -> dict[str, Any] | list[Any]:
        """按天统计瞬时采样数据（Phase 7，POST sampleSet stats）。

        浏览器源码 payload 契约（同端点 trainingLoad 请求 +
        静息心率折叠预览双证据确认）：
        - startDay / endDay 为数字（如 20260721，与 healthRecords
          端点的字符串写法不同，两者不混用）
        - 不携带 fieldNames（静息心率请求中 dataType 后直接
          startDay；fieldName 由服务端推断，真实响应为 restBpm）
        - strategy / groupOption / timeZone 必填（groupOption="day"）
        返回原始 JSON dict（results + groupResults）。
        """
        req = self._stats_req(data_type, start_day, end_day, timezone)
        return self._post(
            {"reqs": [req], "groupReqs": [dict(req)]},
            url=SAMPLE_SET_STATS_URL,
        )

    def query_athletic_performance(
        self,
        timezone: str,
    ) -> dict[str, Any] | list[Any]:
        """查询最新运动能力评估（Phase 8，GET athleticPerformance/latest）。

        浏览器勘探证据契约：
        - query 参数仅 timeZone（如 "+0800"）
        - 响应含 runningAbility / condition / fitness / fatigue /
          ranking / predictedTimes（km1..marathon，秒）
        返回原始 JSON dict，不解析。
        """
        return self._get(
            ATHLETIC_PERFORMANCE_URL, params={"timeZone": timezone}
        )

    def query_sport_reports(
        self,
        activity_type: str = "running",
    ) -> dict[str, Any] | list[Any]:
        """查询运动报表（PB 个人纪录）（Phase 8，GET sportReports）。

        浏览器勘探证据契约：
        - query 参数仅 activityType（证据值 "running"；其他取值
          未经验证）
        - 响应含 sportReports[].personalBest[]，每条
          {name, value, startTime(ms), endTime(ms)}
        返回原始 JSON dict，不解析。
        """
        return self._get(
            SPORT_REPORTS_URL, params={"activityType": activity_type}
        )

    def close(self) -> None:
        """关闭底层 httpx 客户端。"""
        self._client.close()

    def __enter__(self) -> "HTCClient":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()
