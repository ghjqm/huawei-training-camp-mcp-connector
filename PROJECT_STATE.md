# Project State — Huawei Training Camp MCP

> 新 Coding Agent 恢复项目的唯一入口（配合当前代码 + 当前任务）。
> 不含真实 Cookie / Token / location 数据。真实响应只存 data_temp/（git-ignored）。

## Current Phase

Phase 6.4（查询窗口可配置：三个 tool 暴露 lookback_days）— **PASS**

## Completed

- Phase 1–4：FastMCP 服务、health_check、ActivityRecord models/parser、HTCClient（httpx）、get_recent_activities tool
- Phase 5：真实 HTC API 连通性验证（环境变量凭证，HTTP 2xx）；修复响应顶层 list 假设 bug；activityType int 对齐
- Phase 6.1：真实 Activity Detail schema 勘探（details[]/samplePoints[]/value[] 结构确认）
- Phase 6.2-A/B：代码审计；Detail domain model（ActivityDetail/DetailCollector/DetailSamplePoint）+ timestamp contract + synthetic tests
- Phase 6.2-C：parse_activity_detail()（真实数据修正：detail 顶层时间戳为毫秒）；真实 JSON 离线解析成功
- Phase 6.2-D：get_activity_detail MCP tool（id 两步解析）+ Mock 全链路 + 真实数据离线端到端验证
- Git checkpoint：仓库已初始化并完成首次提交（4110281）
- Phase 6.3：analysis.py（summarize_activity_detail：确定性聚合）+ TrainingSummary/CollectorSummary/FieldStats 模型 + get_training_summary tool + 10 项测试；真实 JSON 离线统计验证成功（HR 93-188 avg 164.7 / speed 0-3.9 / altitude 1488.9-1500.5 / GCT 262-316ms）
- Phase 6.4：get_recent_activities / get_activity_detail / get_training_summary 均暴露 lookback_days（默认 30，1~730 校验 _validate_lookback_days）；fetch 层复用现有时间范围参数，HTC payload contract 未变；+5 测试，真实 JSON 离线验证（90 天窗口定位成功、payload 逐字、summary 无回归）

## Code Status

```
src/huawei_health_mcp/
├── __init__.py   包标识 0.1.0
├── client.py     HTCClient：query_activity_records / query_activity_detail，只返回原始 JSON
├── models.py     ActivityRecord / SamplePoint / ActivityDetail / DetailCollector / DetailSamplePoint / TrainingSummary 族
├── parser.py     parse_activity_record（ms 契约）/ parse_activity_detail（顶层 ms + collector/sample ns）
├── analysis.py   summarize_activity_detail：ActivityDetail → TrainingSummary（确定性统计，无 AI）
└── server.py     FastMCP：health_check / get_recent_activities / get_activity_detail / get_training_summary
scripts/test_real_htc.py   人工真实请求脚本（list / detail 两模式，凭证只从环境变量）
tests/   8 个测试文件 + 2 个 synthetic fixtures（无真实用户数据）
```

## Tests

`python -m pytest`（项目根，.venv）→ **66 passed**（parser 5 + client 8 + tools 7 + detail models 10 + detail parser 17 + detail tool 9 + analysis 8 + summary tool 2）

## Architecture Decisions

- 认证只经环境变量（HTC_AUTHORIZATION / HTC_CLIENT_ID 必填，HTC_VERSION / HTC_COOKIE 可选），不登录/不刷新/不绕过
- 分层：client=HTTP 取数 / parser=原始 JSON→model / models=标准模型 / server=MCP 接线；分析逻辑将放 analysis.py（不进 server）
- Detail 采样用通用 field-value（values: dict[str, float|int]），不做 dataType 专属模型
- 容错风格：缺失即 None/空列表；malformed value 条目跳过；绝不静默造数据
- detail 顶层 name/desc/deviceInfo/appInfo/activitySummary 等暂不进 model（无消费方）

## Critical Contracts

### 时间戳契约（真实 JSON + 跨接口交叉验证，禁止"看起来统一"就改单位）

- ActivityRecord 记录级 startTime/endTime = **milliseconds**（_ms_to_datetime）
- ActivityDetail 顶层 startTime/endTime = **milliseconds**（6.2-C 修正：与同活动列表记录毫秒值一致）
- ActivityDetail.details[].startTime/endTime = **nanoseconds**（_ns_to_datetime）
- ActivityDetail.samplePoints[].startTime/endTime = **nanoseconds**（_ns_to_datetime）

### Detail value 契约

- floatValue → float；integerValue → int（类型保真）
- malformed value entry（缺 fieldName / 缺值 / 双值 / 不可转换）→ skip，不静默造数据

### Activity Detail lookup 契约

HTC API 不支持 activity_id → detail 直查。现实现为两步：

```
activity_id → 列表(30天,limit=50)定位 → 逐字取 startTime/endTime/activityType
           → query_activity_detail(...) → parse_activity_detail(...)
```

除非有真实 API 证据证明存在更直接 endpoint，不得删除此设计。

### Client 契约

- query_activity_records() / query_activity_detail() 响应顶层 = **list**（真实 API 确认）
- 真实数据只存 data_temp/（git-ignored）；测试只用 synthetic fixture

### lookback_days 契约（Phase 6.4）

- 三个查询 tool 均有 lookback_days: int = 30，范围 1~730（MAX_LOOKBACK_DAYS），非法值 ValueError
- fetch 层复用既有 startTime/endTime 毫秒参数，HTC payload contract 不变

## Known Issues

1. ActivityDetail.activeTime 单位存疑：真实值呈毫秒量级（2464000≈41min），模型注释写"秒"；parser 原样保留不转换，待证据充分再改
2. DETAIL_LOOKUP_LIMIT=50：单次列表定位最多 50 条；查询窗口已可由 lookback_days 扩到 730 天（Phase 6.4），但单窗口内活动数超 50 时仍可能漏定位
3. 真实 schema 基于有限真实响应（1 活动 / 6 collectors）验证，未验证字段语义不算确定事实
4. samplePoint endTime 丢弃（真实数据 startTime==endTime，暂无损失）
5. 真实数据观察（非 bug）：location 采样内嵌 altitude 字段恒 0，真实海拔以 altitude collector 为准；posture 部分字段（swing_angle/hang_time 等）在真实样本中恒 0

## Risks

- 凭证只在用户终端会话有效；Agent 无法真实联调（scripts/test_real_htc.py 由用户执行）
- 不同运动类型/设备的 detail schema 可能与本仓验证样本有差异

## Next Action

候选（Manager 决定）：1) 用户持凭证在真实 MCP 客户端验收四个 tool（需人工）；2) HR zones / 配速区间业务语义层——需 Manager 先定区间标准（五区/三区/%HRmax/%HRR/厂商标准），Agent 不得自行选择；3) get_recent_activities 分页支持（offset/cursor，需先确认 HTC API 是否支持，暂无真实证据）。

## Last Updated

2026-08-16（Phase 6.4 完成时）
