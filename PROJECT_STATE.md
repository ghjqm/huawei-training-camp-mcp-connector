# Project State — Huawei Training Camp MCP

> 新 Coding Agent 恢复项目的唯一入口（配合当前代码 + 当前任务）。
> 不含真实 Cookie / Token / location 数据。真实响应只存 data_temp/（git-ignored）。

## Current Phase

Phase 6.11（周趋势环比 Week-over-Week Delta + 第 9 个 tool）— **PASS**

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
- Phase 6.5：get_recent_activities 的 limit 边界校验（1~MAX_LIST_LIMIT=100，MCP 入口层；内部 DETAIL_LOOKUP_LIMIT 路径不受影响）；README 全面重写（4 tools 说明 / 环境变量凭证表 / MCP 客户端配置示例 / 安装与测试指引——供用户真实客户端验收）；+2 测试
- Phase 6.6：**修复重大契约缺口**——真实记录统计字段嵌套在 activitySummary.{dataSummary,performanceSummary,paceSummary} 下，此前 parser 只读顶层导致真实列表的 HR/距离/配速/VO2max 全部丢失为 None（Phase 5"轻量摘要"结论有误，数据一直存在）；parser 改为嵌套优先+顶层兼容双路径；新增 SessionMetrics 模型 + compute_session_metrics（双源：device_summary 权威优先/samples 聚合回退，source 字段标记来源）+ get_session_metrics tool + fetch_session_data（一次定位同时返回 record+detail，fetch_activity_detail 变薄包装签名不变）；+10 测试；真实数据全链路验证（HR 164/188/93、distance 7010m、pace 351.5/309 s/km、duration 2713s；双源交叉：设备配速 vs 采样换算差 7 s/km，采样含静止段）
- Phase 6.7：period_analysis.py（aggregate_activity_period 纯函数：count/距离/历时/HR 聚合 + activityType 原样分组）+ TrainingPeriodSummary/ActivityTypeSummary 模型 + get_training_period_summary(lookback_days=7, limit=100) tool（复用 fetch_recent_activities，一次列表请求即可聚合）；真实 fixture（5 条：4×type90+1×type56，总距 20400m）离线聚合 + 手写逐条交叉验证通过；+16 测试
- Phase 6.8：ActivityRecord + calories/ascent/descent/steps 字段（parser 从嵌套 dataSummary 映射：calories.burnt.total/altitude.statistics/steps.total）；TrainingPeriodSummary + 对应 total 字段与 *_activity_count；真实数据 5/5 稳定提供（总卡 917.0 / 爬升 70.6 / 下降 65.7 / 步数 26769 精确断言）；未聚合：stride.avg（单位未确认）、resting_calories（跨活动语义不明）、steps.total.duration（单位未知）→ 记录待决；+5 测试
- Phase 6.9：period_comparison.py（compare_metric / compare_training_periods 纯函数）+ MetricComparison / TrainingPeriodComparison 模型 + fetch_period_comparison（连续双窗口 [now-2N,now-N)/[now-N,now)，单 now 锚点边界精确连续，返回记录按自身 start_time 半开区间过滤保证窗口语义）+ get_training_period_comparison(window_days=7, limit=100) tool（第 7 个）；test_real_htc.py 新增 period / comparison 模式；真实 fixture 按时间拆 2/3 合成周期交叉验证（SYNTHETIC COMPARISON VALIDATION）；+30 测试（含参数化）
- Phase 6.11：compute_weekly_trend_delta（消费 TrainingTrendSummary → TrainingTrendDelta，100% 复用 Phase 6.9 compare_metric 零数学复制；相邻周 older→newer，weeks=N 最多 N-1 对，weeks=1 → transitions=[]）+ WeekDeltaTransition（内嵌 7 项必选指标 + HR avg/max 的 MetricComparison，窗口边界直接取自 trend.weeks 不重新定义）+ fetch_weekly_trend_delta（单次请求 → 一次聚合 → 环比，无重复请求/聚合）+ get_training_weekly_trend_delta(weeks=4, limit=100) tool（第 9 个）；真实 fixture 验证解析/归窗/环比结构（1.43 天跨度，空周→5条周：delta=+5、pct=None）+ synthetic 拆窗环比与 compare_metric 交叉验证；+20 测试
- Phase 6.10：trend_analysis.py（build_week_windows / aggregate_weekly_trend 纯函数，每窗口复用 aggregate_activity_period 零复制）+ TrainingWeekSummary（内嵌完整 TrainingPeriodSummary）/ TrainingTrendSummary 模型 + fetch_weekly_trend（单次列表请求整个跨度 [now-weeks*7d, now)，同一 now 用于请求与切分 → 零边界漂移）+ get_training_weekly_trend(weeks=4, limit=100) tool（第 8 个，weeks 上限 104=730//7）；真实 fixture 实际跨度仅 1.43 天 → 只做解析/归窗验证 + Phase 6.8 精确总量交叉验证（20400/917/70.6/65.7/26769），跨周逻辑 synthetic 覆盖；+26 测试

## Code Status

```
src/huawei_health_mcp/
├── __init__.py   包标识 0.1.0
├── client.py     HTCClient：query_activity_records / query_activity_detail，只返回原始 JSON
├── models.py     ActivityRecord / SamplePoint / ActivityDetail / DetailCollector / DetailSamplePoint / TrainingSummary 族
├── parser.py     parse_activity_record（ms 契约）/ parse_activity_detail（顶层 ms + collector/sample ns）
├── analysis.py   summarize_activity_detail + compute_session_metrics（确定性聚合，双源，无 AI）
├── period_analysis.py   aggregate_activity_period：多 ActivityRecord → TrainingPeriodSummary
├── period_comparison.py   compare_training_periods：两个 TrainingPeriodSummary → TrainingPeriodComparison
├── trend_analysis.py   build_week_windows + aggregate_weekly_trend + compute_weekly_trend_delta：周趋势与环比（复用聚合层/比较层）
└── server.py     FastMCP：health_check / get_recent_activities / get_activity_detail / get_training_summary / get_session_metrics / get_training_period_summary / get_training_period_comparison / get_training_weekly_trend / get_training_weekly_trend_delta
scripts/test_real_htc.py   人工真实请求脚本（list / detail / period / comparison 四模式，凭证只从环境变量）
tests/   12 个测试文件 + 2 个 synthetic fixtures（无真实用户数据；真实 fixture 聚合/对比/趋势测试在无 data_temp 时自动 skip）
```

## Tests

`python -m pytest`（项目根，.venv）→ **175 passed**（parser 8 + client 8 + tools 9 + detail models 10 + detail parser 17 + detail tool 9 + analysis 8 + summary tool 2 + metrics 8 + period 20 + comparison 30 + trend 26 + trend delta 20）

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

### limit 契约（Phase 6.5）

- get_recent_activities 的 limit 范围 1~100（MAX_LIST_LIMIT），MCP 入口校验；内部定位调用（limit=50）不受影响

### activitySummary 嵌套契约（Phase 6.6，真实数据确认）

- 真实记录/detail 的统计字段嵌套在 activitySummary 下：dataSummary / performanceSummary / paceSummary；顶层写法仅为历史 fixture 兼容路径
- paceSummary.avgPace/bestPace 单位 = 秒/公里（交叉验证：351.5 ≈ 1000/2.848 avgSpeed）；paceMap/partTimeMap 键=公里（字符串），值=秒，partTimeMap 为累计时间
- posture statistics 中 -1 为无效标记（如 avg_swing_angle=-1），不是真实生理数据
- resting_heart_rate.statistics 未在真实样本中出现（列表+detail 均无）

### 周环比契约（Phase 6.11）

- 只比较相邻两个 7 天滚动窗口：older（baseline）→ newer（current）；weeks=N 最多 N-1 对；weeks=1 → transitions=[]（空，最小合理结果）
- 数学 100% 复用 compare_metric（Phase 6.9）：delta=current-baseline；任一侧 None→delta=None（None≠0）；baseline None/==0→pct=None；count 类保持 int
- 窗口边界/顺序直接取自 trend.weeks（继承 6.10 同一锚点半开区间），本层不重新定义；transitions 时间升序（最旧相邻对在前）
- 每对覆盖：activity_count / total_distance / total_duration_seconds / total_calories / total_steps / total_ascent / total_descent + HR avg/max（设备 summary 来源契约；hr_activity_count 由 get_training_weekly_trend 的 weeks[].summary 保真，不在 transition 重复）
- fetch 单次列表请求 → aggregate_weekly_trend（一次聚合）→ compute_weekly_trend_delta，无重复请求/聚合
- 不含周期配速；不解释 activityType 语义

### 周趋势契约（Phase 6.10）

- 周窗口 = 滚动 7 天连续半开区间 [now-k*7d, now-(k-1)*7d)，非自然周（不引入时区/周起始日语义，避免擅自决定业务标准）
- 所有窗口由同一 now 锚点切分（fetch 层同一 now 同时用于请求时间范围与窗口切分，零边界漂移）；相邻窗口共享边界值但不重叠，恰在边界的活动归较新窗口
- 单次列表请求整个跨度 [now-weeks*7d, now)（limit 1~100 作用于全跨度），本地按记录自身 start_time 归窗；start_time 缺失或跨度之外的记录排除
- 每周输出 = 完整 TrainingPeriodSummary（Phase 6.7/6.8 全字段契约继承，含 HR 设备来源 + hr_activity_count + activity_types 分组）；不复制聚合逻辑
- weeks 范围 1~104（MAX_TREND_WEEKS，weeks*7 ≤ 728 ≤ 730 上限）；trend.weeks 按时间升序（weeks[0] 最旧，weeks[-1]=[now-7d, now)）
- 不含周期配速；不解释 activityType 语义；无分页假设（沿用现有 list 契约）

### 周期对比契约（Phase 6.9）

- delta = current - baseline；任一侧为 None → delta=None（None=无可靠数据，不得当 0；无法证明基线是 0 时不出数字）
- percentage_change = (current-baseline)/baseline*100；baseline 为 None 或 ==0 → None（不除零，不伪造基线）
- count 类指标（int，0=真实零）delta 恒可计算、保持 int；percentage_change 仍受 baseline==0 约束
- 两个完整 TrainingPeriodSummary 原样内嵌（各 *_activity_count / activity_types 保真，不重复展开）；对比层只覆盖总量与 HR（设备 summary 来源，继承 6.7 契约）；不含周期配速对比
- 窗口语义：current=[now-N, now)、baseline=[now-2N, now-N)，半开区间相邻不重叠；两次请求共用同一 now 锚点（边界毫秒值相等）；返回记录按自身 start_time 过滤归窗（防服务端对历史区间行为宽松），start_time 缺失的记录两窗都排除
- window_days 范围 1~365（MAX_COMPARISON_WINDOW_DAYS，两窗总跨度不超 730 上限）；limit 每窗 1~100
- HTC list API 的 startTime/endTime 毫秒参数契约自 Phase 5 真实验证，但“endTime 在过去”的历史窗口真实行为未验证 → 防御性过滤 + Known Issue 跟踪

### 周期聚合扩展契约（Phase 6.8）

- ActivityRecord 新增 calories（kcal，设备口径原值）/ ascent / descent（米）/ steps（int 计数），全部来自嵌套 dataSummary，缺失即 None
- 周期聚合为设备原值求和（不做单位换算）；全缺→None 不伪造 0；各自带 *_activity_count；ascent_activity_count 同时覆盖 descent（同源 altitude.statistics）
- 平均值语义不变：average_x 一律为有效活动算术平均（无加权），加权平均若未来需要另建字段
- 明确不聚合：stride.avg（单位未确认）、resting_calories（跨活动语义不明）、steps.total 内嵌 duration（单位未知）

### 周期聚合契约（Phase 6.7）

- duration = 每条记录 end-start 总历时（含间歇），非纯运动时间（activeTime 单位未决）；单位秒
- 距离只聚合设备权威 summary（不积分采样）；缺失活动不计入，distance_activity_count 标记有效数；全缺→None（不伪造 0）
- HR 只聚合设备 summary per-activity avg/max；hr_activity_count 标记有效数
- activityType 原样 int 分组（56/90），不命名运动语义（映射待 Manager 决策）
- 不提供周期平均配速：纯运动总时长缺失，elapsed-based 与设备 avgPace 语义不一致（待 Manager 决策）
- 聚合纯函数不修改输入；空列表/单活动/乱序输入均稳定

### SessionMetrics 双源契约（Phase 6.6）

- HR/配速/距离：record 设备权威统计优先（device_summary），缺失回退采样聚合（samples）；速度：仅采样聚合；距离不积分（采样间隔不均匀）
- 每个指标带 source 字段；缺失即 None；单位：speed=m/s、pace=s/km、distance=m、duration=s
- active_time_raw 原样保留（单位疑问未裁决）

## Known Issues

1. ActivityDetail.activeTime 单位存疑：真实值呈毫秒量级（2464000≈41min），模型注释写"秒"；parser 原样保留不转换，待证据充分再改
2. DETAIL_LOOKUP_LIMIT=50：单次列表定位最多 50 条；查询窗口已可由 lookback_days 扩到 730 天（Phase 6.4），但单窗口内活动数超 50 时仍可能漏定位
3. 真实 schema 基于有限真实响应（1 活动 / 6 collectors）验证，未验证字段语义不算确定事实
4. samplePoint endTime 丢弃（真实数据 startTime==endTime，暂无损失）
5. 真实数据观察（非 bug）：location 采样内嵌 altitude 字段恒 0，真实海拔以 altitude collector 为准；posture 高频采样部分字段恒 0、statistics 部分 -1 为无效标记
6. pagination：list 响应顶层为纯数组（无 page/hasNext/total 等包裹字段——已勘探证据）；limit>5 的服务端行为未验证（需真实凭证）；未实现分页（无真实证据不伪造）；另：历史窗口（endTime<now）的 list 真实行为同样未验证——comparison tool 已用窗口过滤防御，真实行为用 scripts/test_real_htc.py comparison 模式一次验证即可
7. HR Zone / 配速区间标准未定（Manager 决策）；SessionMetrics 已提供双源基础数据，zone 计算的标准确定后可在 analysis.py 增量实现
8. stride.statistics(avg) 单位未确认（cm? m?）——数据存在但不聚合；resting_calories.statistics 跨活动语义不明——不聚合；steps.total 内嵌 duration 单位未知——不解析

## Risks

- 凭证只在用户终端会话有效；Agent 无法真实联调（scripts/test_real_htc.py 由用户执行）
- 不同运动类型/设备的 detail schema 可能与本仓验证样本有差异

## Next Action

1) 【需 Manager 决策】activityType→运动名映射；HR/配速 Zone 标准；周期配速语义（依赖 activeTime 裁决）；stride 单位确认 2) 【需用户/真实凭证】真实 MCP 客户端验收九个 tool（README 配置示例已就绪）；分页行为验证（limit>5）；历史窗口 list 行为验证（scripts/test_real_htc.py comparison 7 一次即可，输出已含 0 条歧义提示）；真实跨周趋势数据需积累 >7 天训练后验收 3) 【可自主】test_real_htc.py 增加 trend / trend-delta 模式；多趋势指标叠加（如月度滚动窗口）若 Manager 需要再扩展；comparison 支持任意错位窗口（如 vs 上上月）若 Manager 需要再扩展。

## Last Updated

2026-08-16（Phase 6.11 完成时）
