# Project State — Huawei Training Camp MCP

> 新 Coding Agent 恢复项目的唯一入口（配合当前代码 + 当前任务）。
> 不含真实 Cookie / Token / location 数据。真实响应只存 data_temp/（git-ignored）。

## Current Phase

**v1.1 已发布（2026-08-17，git tag v1.1）**——14 个 MCP tool：训练域 9（列表/详情/摘要/指标/周期聚合/对比/周趋势/环比/健康检查）+ 恢复状态域 3（睡眠/静息心率/HRV）+ 训练能力域 2（运动能力评估/PB 个人纪录）；315 测试全绿；全程代码审计 + 压力测试轮完成（4 处 P1 容错缺口修复，性能基准毫秒级）；README 补 401 认证过期排障指引。

（Phase 7 已于 2026-08-17 完成真实验收：test_mcp_client 12/12 passed；resting-hr 28 天 / hrv 28 天真实数据返回正常；Phase 8 亦于同日完成真实数据验收：performance 五指数+6 预测成绩 / pb 6 条纪录全部返回；**随后全程代码审计 + 压力测试轮完成**：修复 Phase 7/8 parser 四处容错缺口（NaN/inf 透传、超大 int OverflowError、非 str fieldName ValidationError、bool 时间戳伪造 1970）+ unicode 日过滤，test_robustness 扩展至 97 项（+61），315 passed；性能基准全绿：5000 条活动解析 52.6ms / 104 周趋势 53.2ms / 5000 晚睡眠 38.3ms / 730 天统计 2.7ms / 万级 PB 与预测成绩 ~20ms）

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
- Phase 6.12：工程闭环——scripts/test_real_htc.py 新增 trend [weeks] / trend-delta [weeks] 两模式（100% 复用 fetch_weekly_trend / fetch_weekly_trend_delta，脚本零窗口切分/聚合/环比逻辑重实现；输出仅安全摘要：周窗口边界（本地时间）/每周聚合（复用 _print_period_summary）/相邻周 9 项指标 baseline→current (delta, pct|N/A)；空周明确打印；weeks=1 空 transitions 说明；weeks 非整数→报错退出 1）+ tests/test_real_htc_script.py 轻量纯函数测试（参数解析/本地时间格式化，无网络，+4）；README 漂移修复（开发结构图补 period_analysis / period_comparison / trend_analysis 三模块；安装第 3 步更新为六模式示例）；无凭证运行打印缺失环境变量退出 1（已验证）；现有四模式行为未变
- Phase 6.13：activeTime 毫秒契约落地 + 周期活跃时间聚合与周期配速——证据 A/B（5 条真实记录：activeTime(ms)/跨度比 0.625~0.999 全 ∈(0,1]；distance÷(activeTime_ms/1000) 与设备 paceSummary.avgPace 5/5 精确恒等，如 8010m/2859s=356.93 vs 356.92883）确认 activeTime=毫秒、设备配速口径=activeTime/distance；ActivityRecord +active_time_ms（记录级顶层 activeTime，int 毫秒原样保留，与既有 active_time 同源同值）；TrainingPeriodSummary / ActivityTypeSummary 对称 +total_active_time_seconds（Σms/1000，全仓唯一单位换算点）+ active_time_activity_count + average_pace_seconds_per_km（ΣactiveTime_s/Σdistance_km，双值活动子集）+ pace_activity_count，全缺→None 不伪造 0；models.py 三处单位注释修正（active_time / active_time_raw / TrainingSummary.active_time）；trend / comparison 层零改动（内嵌 summary 自动继承），compare_metric 与 transition 指标清单不动（对比是否加配速留待 Manager）；test_real_htc.py：trend-delta 的 delta 浮点噪声修复（_fmt_delta：float 两位小数带符号、int 原样、None→N/A）+ _print_period_summary 增打 total_active_time / average_pace（None→N/A）；README 三表格行补新字段说明；真实 fixture 在 6.12 真实验收后刷新（5 条：2×type56+3×type90，总距 22570m），6 处精确断言随新 fixture 重新核对（22570/1154.0/70.5/70.7/25712）；+12 测试（parser 3 + 周期聚合 6 + 证据 B 恒等式永久回归 1（容差 0.01 s/km，fixture 缺失 skip）+ 脚本格式化 2）
- 审计加固轮（2026-08-17）：全代码审阅（client/parser/models/analysis/period/trend/comparison/server）+ 合成 malformed 数据压力测试（tests/test_robustness.py，36 项）暴露 21 处真实 crash——全部违反 parse_activity_record/parse_activity_detail 的“不抛错”docstring 契约（单条毒记录可炸掉整批 get_recent_activities 解析）。修复全部集中在 parser.py 容错边界：(1) _ms_to_datetime/_ns_to_datetime 类型守卫+超界捕获（字符串/float/dict 时间戳、负值/超 datetime 范围 → None）；(2) _as_float/_as_int 容错转换（垃圾字符串/dict/list → None；NaN/inf 为 JSON 非法值归 None；数字字符串宽松接受）；(3) performanceSummary 非 dict 形状守卫（字符串时 .get() AttributeError——审阅时未预见、压力测试暴露）；(4) speed 统计字段先 _as_float 再比较（此前字符串 avgSpeed 使 `> 0` 直接 TypeError）；(5) _iter_sample_points 采样值经 _as_float（垃圾 stringValue 跳过）；(6) 配速 0/负值显式视为无效标记回退速度推导（与 posture -1 标记同风格，行为等价原 or 写法但显式化）。性能压力：5000 条记录（5% 毒数据）解析 0.08s / 聚合 0.02s / 104 周归窗+环比 0.04s / 周期对比 0.01s；解析幂等。观察项不修（无 crash 风险、真实数据从未出现、属数据语义非契约）：_duration_of 负时长（end<start）无防护、activityType=0 falsy fallback、单条 distance≤0 参与配速分子分母（总体已有 total>0 防护）；+36 测试
- Phase 6.14：真实 MCP 客户端验收发现并修复 P0 认证 bug——scripts/test_mcp_client.py（脚本化 MCP 客户端：mcp 库 stdio 拉起服务 → initialize → list_tools → 逐个调用 9 tool，走完整 JSON-RPC 出站链路；EXPECTED_TOOLS 名单核对；get_recent_activities 结果提取真实 activity_id 供 detail 族；输出仅安全摘要）首次真实调用即暴露：server.py build_htc_headers 此前只映射 HTC_COOKIE，从不读取 HTC_AUTHORIZATION/HTC_CLIENT_ID/HTC_VERSION → MCP 服务发出的请求从不带 Authorization，真实调用恒 401 "Invalid Credentials"（此前所有真实验证走 test_real_htc.py 自建 headers，完全掩盖该缺口；src/ 全仓 grep 证实）。修复：_ENV_TO_HEADER 四变量完整映射（与 test_real_htc.py 同一契约），必填缺失时 ValueError 指明变量名（清晰本地报错优于发出注定 401 的请求误导排查）；验收脚本同步修复 StdioServerParameters env=dict(os.environ)（MCP SDK 不传 env 时子进程只得到安全默认环境，HTC_* 不继承——此前与认证 bug 叠加无法区分）；tests/test_tools.py +autouse fixture _dummy_htc_env（假凭证环境，Mock 不联网，套件在有无真实凭证的终端行为一致）+3 认证头回归测试（四变量全映射/必填缺失报错含变量名/可选缺省省略）；无凭证本地验证：MCP 链路传出清晰 "Missing required environment variable(s)" 错误（替代误导性 401）；全量 194 passed；**用户真凭证复验 9/9 passed（2026-08-17）**——9 个 tool 全部经真实 MCP 客户端出站链路（JSON-RPC → 服务 → HTTPS → HTC）真实调用成功，detail 族使用真实 activity_id
- 发布就绪评审轮（2026-08-17）：Final Release Readiness Review——9 tool 实现/README/真实验收/认证安全（对用户曾粘贴的 token 特征全仓扫描零匹配）/.gitignore/parser 容错/State 一致性全项核对；修复 2 项 P2（requirements.txt 显式声明 mcp>=1.29.0；State 6.9 契约段过时表述与 KI-6 矛盾）；判定 RELEASE / v1.0-ready（无 P0/P1）；230 passed
- Phase 7：恢复状态数据域（睡眠 / 静息心率 / HRV）——用户浏览器勘探（data_temp/htc探查.txt，请求+响应双证据）确认三条新链路后实施：① GET /healthRecords（纳秒时间戳 + dataType=sleep + subDataType=sleep.fragment；value 数组 fieldName→longValue/integerValue 结构）→ get_sleep_records(days=14)（SleepRecord：上床/入睡/醒来时刻（ms）、light/deep/dream/awake/total 分钟、score/efficiency/latency/wakeup_count；deep_sleep_part/sleep_type 语义未公开原样保留；两条自洽恒等式已验证并固化为回归：分段和=总睡眠 199+165+106=470、入睡→醒来跨度=总睡眠+清醒 471=470+1、go_bed+latency=fall_asleep 8min）；② POST sampleSet/periodStatistics:calculate（startDay/endDay 数字写法、无 fieldNames、fieldName 服务端推断 restBpm）→ get_resting_heart_rate(days=28)（HealthMetricStats：overall avg/max/min/count + daily 升序去重）；③ POST healthRecords/periodStatistics:calculate（startDay/endDay 字符串写法、fieldNames=["avgHrv"]、dataType=sleep）→ get_hrv_stats(days=28)（avgHrv 原样返回，华为未声明单位/算法口径不换算）。实现：client.py +_get（GET 错误契约与 _post 一致）+_post 可选 url 参数 + query_sleep_records/query_health_record_stats/query_sample_set_stats（端点 URL 与数据类型常量集中管理；两 stats 端点 startDay 类型差异在 client 层隔离）；health_parser.py（复用 parser._ms_to_datetime/_ns_to_datetime 加固助手；容错哲学一致：缺失→None 畸形不崩）；models.py +SleepRecord/StatBlock/DailyStat/HealthMetricStats；server.py +HRV_FIELD_NAME/_local_midnight/_inclusive_day_window（含今天两端闭区间，证据：28 天窗口 count=28）/fetch_sleep_records（尾随窗口 [now-N天, now] 纳秒，证据：浏览器 1 天窗口=[capture-24h, capture] 不与任何时区自然日对齐，尾随窗口对自然日语义同样稳健）/fetch_resting_heart_rate/fetch_hrv_stats + 3 tool（days 1~730 复用 _validate_lookback_days）；test_real_htc.py +sleep/resting-hr/hrv 三模式（100% 复用 fetch 层；sleep 模式内置三条自洽恒等式运行时检查，MISMATCH 即契约漂移信号）；test_mcp_client.py EXPECTED_TOOLS 9→12（+恢复状态族默认参数调用）；README（工具表 12 行/结构图 health_parser/九模式/契约摘要 +恢复状态端点）；+14 测试（tests/test_health.py：client 请求契约 4——sleep GET 参数逐字/healthRecords stats 字符串日+fieldNames/sampleSet stats 数字日无 fieldNames/401 传播；parser 真实证据 5——睡眠记录全字段+三条恒等式/排序容错/缺失容错/RHR overall+daily 升序单值日不伪造/HRV 字符串日解析+同日去重+畸形容错；tool 4——三工具结构+窗口字段+days 校验参数化）；244 passed；hotfix（2026-08-17 用户真实运行暴露）：_run_stats_mode 漏传 headers → resting-hr/hrv 模式 NameError（sleep 不受影响）——离线冒烟盲区：无凭证/非法 days 提前退出未覆盖 client 构造路径；修复后新增 dummy 凭证 401 冒烟（三模式干净 FAILED exit=1，顺带确认恢复状态端点 401 契约与活动端点一致）；**用户真实验收（2026-08-17）：12 tool 中 10 个 PASS——sleep 真实通过（GET /healthRecords 链路+解析+恒等式全验证），9 个训练域 tool 维持通过；resting-hr/hrv 两 stats 端点被服务端 400 拒绝：[groupReqs[0].groupOption] must not be null——groupOption 为必填字段但勘探 payload 被 Chrome devtools 省略号折叠吞掉（证据文件无此字段），不可猜测，待用户补一次完整 payload（Payload 面板 view source 模式）后精确实现**；**修复（同日，用户源码模式补探后）**：三份完整 payload 到手（activityRecords / sampleSet trainingLoad / healthRecords avgHrv），确认 stats 请求必填公共字段 strategy=["MAX","MIN","AVG","SUM"] / groupOption="day" / timeZone="+HHMM"（reqs 与 groupReqs 均需）；client._stats_req 统一构造（字段顺序与浏览器一致；fieldNames 仅 healthRecords 族携带——静息心率请求 dataType 后直接 startDay 的折叠证据确认无此键）；server._local_tz_offset() 从系统推导时区（非硬编码 +0800）传入两个 stats fetch；契约测试同步断言新必填字段；244 passed；dummy 凭证冒烟两模式干净 401（认证先于 payload 校验，真实 400 是否消除待用户复验）；**最终真实验收（2026-08-17）**：test_mcp_client 12/12 passed + resting-hr 28 / hrv 28 真实数据正常（窗口 [2026-07-21..2026-08-17]，RHR avg=50.0 max=55.0 min=46.0、HRV avg=71.0 max=88.0 min=57.0，各 28 天明细）——Phase 7 关闭
- Phase 8：训练能力域（用户勘探证据 data_temp/htc探查.txt）——① GET /healthrunninggroup/v1/athleticPerformance/latest?timeZone=%2B0800 → get_athletic_performance()（AthleticPerformance：runningAbility 41.9 / condition -2.0833 / fitness / fatigue / ranking + predictedTimes{km1,km3,km5,km10,halfMarathon,marathon} 秒；全为服务端口径本地不换算；timeZone 由 server._local_tz_offset() 系统推导）；② GET /healthrunninggroup/v1/sportReports?activityType=running → get_personal_bests(activity_type='running')（SportPersonalBests/PersonalBestEntry：name/value/start_time/end_time；响应 sportReports[] 按 activityType 匹配取第一个条目；PB value 单位由华为定义——bestRunPartTime* 为秒、bestRunDistance 为米，原样返回；startTime/endTime 毫秒 _ms_to_datetime）。实现：client.py +query_athletic_performance/query_sport_reports（两个 GET，_get 错误契约）+ ATHLETIC_PERFORMANCE_URL/SPORT_REPORTS_URL 常量；models.py +AthleticPerformance/PersonalBestEntry/SportPersonalBests；performance_parser.py（parse_athletic_performance/parse_personal_bests，容错哲学一致：非 dict→空模型、垃圾值→None、predictedTimes 仅收数值项）；server.py +fetch_athletic_performance/fetch_personal_bests + 2 tool（第 13/14 个；activity_type 空白校验）；test_real_htc.py +performance/pb 两模式（100% 复用 fetch 层；pb 可选参数指定运动类型）；test_mcp_client.py EXPECTED_TOOLS 12→14；README（工具表 14 行/十一模式/254/14-14）；+10 测试（tests/test_performance.py：client GET 请求契约 2 / parser 真实证据 4（跑力五指数+六预测成绩精确断言；PB 3 条含毫秒时间戳 datetime 精确断言）/ 容错 2 / tool 结构 2）；254 passed；dummy 凭证冒烟两模式干净 401（URL/参数正确：timeZone=%2B0800、activityType=running）
- 全程代码审计 + 压力测试轮（Phase 8 验收后）：src 全部 10 文件人工审计 + 性能基准。**修复 4 处 P1 容错缺口**（全部位于 Phase 7/8 新 parser，与上轮审计在 parser.py 修复的问题同类）：① health/performance 两处 _to_float 对 NaN/inf 直接透传（Python json 默认接受 NaN 字面量，会产出非法 JSON 输出）且超大 int 转 float 抛 OverflowError 违反不抛错契约——补 math.isfinite 与 OverflowError 守卫（数值字符串仍拒绝，与 parser._as_float 宽松策略的差异是本域有意为之并写入 docstring）；② parse_health_metric_stats 的 `fieldName or field_name` 会把服务端非 str 值（数字/嵌套结构）塞进 Optional[str] 触发 Pydantic ValidationError 炸掉整次解析（真实路径：静息心率 field_name=None）——改 isinstance(str) 守卫；③ parse_personal_bests 的 startTime/endTime 未排除 bool（int 子类）会把 True 当 1ms 伪造 1970 时间——补 bool 排除；④ _day_str 接受 unicode 数字（"٢٠٢٦٠٨١٧".isdigit()=True）产出乱码日串——补 isascii()。**压力测试**：test_robustness.py +61（毒值矩阵 parametrize：stats/睡眠/能力/PB 四组 + 批量毒数据 5000 睡眠/730 天统计/万级 PB/万键 predictedTimes），36→97；全量 315 passed。**性能基准**（3 次取最优）：5000 条活动解析（5% 毒）52.6ms、聚合 10.0ms、104 周趋势 53.2ms、环比 1.4ms、5000 晚睡眠 38.3ms、730 天统计 2.7ms、万级 PB 16.3ms、万键预测成绩 2.3ms——最大真实负载（730 天窗口）毫秒级，无性能风险。**审计无发现的方面**：client 错误契约/必填校验、聚合与对比纯函数数学契约、server 全部 tool 的 try/finally 资源关闭与入口校验、安全边界（无硬编码凭证/无日志泄漏）均复核通过

## Code Status

```
src/huawei_health_mcp/
├── __init__.py   包标识 0.1.0
├── client.py     HTCClient：query_activity_records / query_activity_detail / query_sleep_records(GET) / query_health_record_stats / query_sample_set_stats / query_athletic_performance(GET) / query_sport_reports(GET)，只返回原始 JSON（_post 支持端点 url 参数，_get 与 _post 同错误契约）
├── models.py     ActivityRecord / SamplePoint / ActivityDetail / TrainingSummary 族 / SleepRecord / StatBlock / DailyStat / HealthMetricStats / AthleticPerformance / PersonalBestEntry / SportPersonalBests
├── parser.py     parse_activity_record（ms 契约）/ parse_activity_detail（顶层 ms + collector/sample ns）
├── health_parser.py   parse_sleep_records / parse_health_metric_stats（恢复状态域，复用 parser 加固时间助手）
├── performance_parser.py   parse_athletic_performance / parse_personal_bests（训练能力域，同一容错哲学）
├── analysis.py   summarize_activity_detail + compute_session_metrics（确定性聚合，双源，无 AI）
├── period_analysis.py   aggregate_activity_period：多 ActivityRecord → TrainingPeriodSummary
├── period_comparison.py   compare_training_periods：两个 TrainingPeriodSummary → TrainingPeriodComparison
├── trend_analysis.py   build_week_windows + aggregate_weekly_trend + compute_weekly_trend_delta：周趋势与环比（复用聚合层/比较层）
└── server.py     FastMCP：health_check / get_recent_activities / get_activity_detail / get_training_summary / get_session_metrics / get_training_period_summary / get_training_period_comparison / get_training_weekly_trend / get_training_weekly_trend_delta / get_sleep_records / get_resting_heart_rate / get_hrv_stats / get_athletic_performance / get_personal_bests
scripts/test_real_htc.py   人工真实请求脚本（list / detail / period / comparison / trend / trend-delta / sleep / resting-hr / hrv / performance / pb 十一模式，凭证只从环境变量）
scripts/test_mcp_client.py 脚本化 MCP 客户端验收（stdio 拉起服务 → initialize → list_tools → 逐个真实调用 14 tool；env 显式传 os.environ）
tests/   16 个测试文件 + 2 个 synthetic fixtures（无真实用户数据；真实 fixture 聚合/对比/趋势测试在无 data_temp 时自动 skip）
```

## Tests

`python -m pytest`（项目根，.venv）→ **315 passed**（parser 11 + client 8 + tools 12 + detail models 10 + detail parser 17 + detail tool 9 + analysis 8 + summary tool 2 + metrics 8 + period 27 + comparison 30 + trend 26 + trend delta 20 + real script 6 + robustness 97 + health 14 + performance 10；真实 fixture 测试在无 data_temp 时自动 skip）

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

### 健壮性契约（审计加固轮，2026-08-17）

- parse_activity_record / parse_activity_detail 对任意 malformed 输入不抛错（docstring 契约，由 tests/test_robustness.py 36 项合成 malformed 矩阵锁定）：不可解析标量 → None；类型/超界时间戳 → None；形状异常容器按缺失处理
- 单条毒记录不得炸掉整批列表解析（get_recent_activities 对每条记录调用 parser）
- 数字字符串（如 stringValue="5120"）宽松按数值接受；NaN/inf（JSON 非法值）→ None 不伪造
- 性能基线：5000 条记录解析+聚合+104 周趋势全链路 < 0.15s

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

### 认证头契约（Phase 6.14，真实 MCP 客户端验收确认）

- server.py build_htc_headers 映射全部四个环境变量：HTC_AUTHORIZATION→Authorization、HTC_CLIENT_ID→x-client-id、HTC_VERSION→x-version、HTC_COOKIE→Cookie（与 scripts/test_real_htc.py 的 ENV_TO_HEADER 同一契约）
- HTC_AUTHORIZATION / HTC_CLIENT_ID 必填：缺失时 ValueError 并指明变量名（清晰本地报错，绝不发出注定 401 的空认证请求——空认证请求的 "Invalid Credentials" 会误导排查方向）；HTC_VERSION / HTC_COOKIE 可选，缺失省略
- MCP stdio 子进程环境：StdioServerParameters 不传 env 时，MCP SDK 只给子进程安全默认环境（PATH/APPDATA 等系统变量），HTC_* 不会自动继承——任何脚本化客户端必须显式 env=dict(os.environ) 或逐项传入
- 历史教训：6.14 前 build_htc_headers 只映射 HTC_COOKIE，MCP 服务发出的请求从不带 Authorization；此前所有真实验证走 test_real_htc.py 自建 headers，完全掩盖该 P0 缺口。真实验收必须覆盖真实出站链路（test_mcp_client.py 或真实 MCP 客户端 App），仅直连脚本验证不充分

### 周期活跃时间与配速契约（Phase 6.13）

- activeTime 单位 = **毫秒**（证据 A：5 条真实记录 activeTime(ms) 与记录跨度（endTime-startTime）之比 0.625~0.999 全部 ∈(0,1]，物理自洽；证据 B：distance ÷ (activeTime_ms/1000) 与设备 paceSummary.avgPace 5/5 精确恒等——设备配速基于纯运动时间，非 elapsed time）；真实值原样保留不换算，字段名不变
- ActivityRecord.active_time_ms = 记录级顶层 'activeTime' 键解析的 int 毫秒（与既有 active_time 同源同值，显式单位命名的规范字段）；缺失即 None
- total_active_time_seconds = Σ(active_time_ms/1000)——**全仓唯一允许的毫秒→秒换算点**；仅统计有 active_time_ms 的活动（active_time_activity_count 标记有效数）；全缺 → None（不伪造 0）
- average_pace_seconds_per_km = ΣactiveTime(秒) / Σdistance(公里)，只在 distance 与 active_time_ms **双值活动子集**上计算（pace_activity_count 标记有效数）；无有效活动 → None；设备 avgPace 恒等式 activeTime_s/distance_km 是其单活动特例
- 配速是比率：**禁止对设备 per-activity 配速做算术平均**（口径错误；正确聚合 = 分子分母分别求和再相除）
- TrainingPeriodSummary 与 ActivityTypeSummary 对称提供上述字段；trend / comparison 层内嵌 summary 自动继承，compare_metric 与 transition 指标清单不变（周期对比是否加配速指标留待 Manager 决策）
- 既有字段与语义零改动：duration 仍为含间歇总历时（end-start），average_x 仍为有效活动算术平均

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
- HTC list API 的 startTime/endTime 毫秒参数契约自 Phase 5 真实验证；历史窗口（endTime<now）行为已于 Phase 6.13 真实验证（见 Known Issue 6）→ 防御性过滤保留

### 周期聚合扩展契约（Phase 6.8）

- ActivityRecord 新增 calories（kcal，设备口径原值）/ ascent / descent（米）/ steps（int 计数），全部来自嵌套 dataSummary，缺失即 None
- 周期聚合为设备原值求和（不做单位换算）；全缺→None 不伪造 0；各自带 *_activity_count；ascent_activity_count 同时覆盖 descent（同源 altitude.statistics）
- 平均值语义不变：average_x 一律为有效活动算术平均（无加权），加权平均若未来需要另建字段
- 明确不聚合：stride.avg（单位未确认）、resting_calories（跨活动语义不明）、steps.total 内嵌 duration（单位未知）

### 周期聚合契约（Phase 6.7）

- duration = 每条记录 end-start 总历时（含间歇），非纯运动时间；单位秒（纯运动时长自 Phase 6.13 起由 total_active_time_seconds 提供，契约见 6.13 段）
- 距离只聚合设备权威 summary（不积分采样）；缺失活动不计入，distance_activity_count 标记有效数；全缺→None（不伪造 0）
- HR 只聚合设备 summary per-activity avg/max；hr_activity_count 标记有效数
- activityType 原样 int 分组（56/90），不命名运动语义（映射待 Manager 决策）
- ~~不提供周期平均配速~~（Phase 6.13 起提供 average_pace_seconds_per_km，设备口径 activeTime/distance，契约见 6.13 段）
- 聚合纯函数不修改输入；空列表/单活动/乱序输入均稳定

### SessionMetrics 双源契约（Phase 6.6）

- HR/配速/距离：record 设备权威统计优先（device_summary），缺失回退采样聚合（samples）；速度：仅采样聚合；距离不积分（采样间隔不均匀）
- 每个指标带 source 字段；缺失即 None；单位：speed=m/s、pace=s/km、distance=m、duration=s
- active_time_raw 原样保留（毫秒，Phase 6.13 已裁决单位）

### 测试契约：真实 fixture 对拍禁止快照数值锚点（2026-08-17）

- data_temp 真实快照会被真实请求刷新（内容随时变），test_real_records_* 对拍用例的期望值必须由测试内手写独立计算推导（sum/Counter/整体聚合对拍），禁止硬编码具体数值/条数/分组锚点；记录数敏感的拆分用例需加最小记录数 skip guard

## Known Issues

1. 【已关闭，Phase 6.13】activeTime 单位 = 毫秒：证据 A——5 条真实记录 activeTime(ms) 与记录跨度之比 0.625~0.999 全 ∈(0,1]（活跃时间≤总跨度，物理自洽）；证据 B——distance÷(activeTime_ms/1000) 与设备 avgPace 5/5 精确恒等（如 8010m/2859s=356.93 vs 356.92883）。模型注释已改毫秒，真实值原样保留不换算；证据 B 已固化为永久回归测试（tests/test_period.py::test_real_records_active_time_pace_identity）
2. DETAIL_LOOKUP_LIMIT=50：单次列表定位最多 50 条；查询窗口已可由 lookback_days 扩到 730 天（Phase 6.4），但单窗口内活动数超 50 时仍可能漏定位
3. 真实 schema 基于有限真实响应（1 活动 / 6 collectors）验证，未验证字段语义不算确定事实
4. samplePoint endTime 丢弃（真实数据 startTime==endTime，暂无损失）
5. 真实数据观察（非 bug）：location 采样内嵌 altitude 字段恒 0，真实海拔以 altitude collector 为准；posture 高频采样部分字段恒 0、statistics 部分 -1 为无效标记
6. pagination（Phase 6.13 更新）：list 响应顶层为纯数组（无 page/hasNext/total 包裹字段）；历史窗口已真实验证（comparison 14：baseline [now-28d,now-14d) 返回 21 条真实活动，服务端完全支持历史区间）；limit=100 请求在 107 条归窗规模下未截断（trend 8 真实运行，且与 comparison 两独立请求拼合 18/18 项指标精确一致）→ 剩余风险仅 >107 条规模未验证；未实现分页（无真实证据不伪造）；comparison/trend 的窗口过滤防御性设计保留
7. HR Zone / 配速区间标准未定（Manager 决策）；SessionMetrics 已提供双源基础数据，zone 计算的标准确定后可在 analysis.py 增量实现
8. 观察项（审计轮记录，无 crash 风险、不修）：_duration_of 对 end<start 的负时长无防护（纯数学差契约，真实数据从未出现）；activityType=0 时 falsy fallback 到 type 键（0 值从未出现）；单条 distance≤0 仍进配速分子分母（总体 sum>0 已防除零）
9. stride.statistics(avg) 单位未确认（cm? m?）——数据存在但不聚合；resting_calories.statistics 跨活动语义不明——不聚合；steps.total 内嵌 duration 单位未知——不解析
10. Phase 7 观察项：deep_sleep_part（89）/ sleep_type（1）语义未公开——原样保留不解释；avgHrv 单位与算法口径（RMSSD/SDNN?）华为未声明——原样返回不换算；sleep 尾随窗口的服务端纳入规则（按记录 startTime 还是窗口相交）无公开契约——本地不过滤原样返回；睡眠 fragment（subData.samplePoints 分段细节）v1 不解析（summary 分段已满足）
11. 【已关闭，Phase 8 实现】athleticPerformance/latest 与 sportReports（PB）端点（原勘探留档 data_temp/htc探查.txt）已实现为 get_athletic_performance / get_personal_bests 两 tool；PB name 枚举语义（bestRunPartTimeHM/10KM/bestRunDistance 等）按华为命名原样返回不翻译

## Risks

- 凭证只在用户终端会话有效；Agent 无法真实联调（scripts/test_real_htc.py 由用户执行）
- 不同运动类型/设备的 detail schema 可能与本仓验证样本有差异

## Post-v1.1 Maintenance

- 2026-08-17 夹具漂移修复（非代码回归）：data_temp 真实快照被后续真实请求刷新后，8 个 test_real_records_* 用例中的硬编码快照锚点（5 条 / 22570m / 1154kcal / 25712 步 / 3×90+2×56 等）过期失效。修复原则：期望值全部改为测试内手写独立计算（sum / Counter / 整体聚合对拍），断言"聚合层输出 == 手写推导"，与快照内容解耦；记录数 <3 的拆分用例加显式 skip guard。防漂移自证：模拟快照刷新（删 1 条）后 9 个 real_records 用例全绿。教训入 Critical Contracts：真实 fixture 对拍用例禁止硬编码快照数值锚点。

## Next Action

1) 【发布后复验（非阻塞）】test_mcp_client.py 14/14（Phase 8 两 tool 已过 test_real_htc 真实数据验收，MCP 出站链路复验仅作记录）2) 【需 Manager 决策】activityType→运动名映射（13/56/90/127/161）；HR/配速 Zone 标准；周期对比/环比是否加活跃时间与配速指标；stride 单位 3) 【需用户/真实数据】>107 条归窗规模的 limit 截断行为验证（数据积累后）4) 【可选 v1.2 候选】训练负荷 stats 端点（activityRecords periodStatistics:calculate / sampleSet trainingLoad，勘探证据已留档）——属聚合域扩展，价值待 Manager 评估；工程债：三处数值/时间转换助手收敛为单一 converters.py（审计建议，非发布阻塞）。

**项目状态**：**v1.1 已发布（2026-08-17，git tag v1.1）**。14 个 MCP tool；12 个已通过真实 MCP 客户端出站链路验收（12/12）；Phase 8 两 tool 已过真实数据验收（test_real_htc performance/pb 模式），MCP 链路 14/14 为发布后非阻塞复验项。全程代码审计 + 压力测试轮完成（315 passed，4 处 P1 容错缺口修复，性能基准全绿）。README 含 401 认证过期排障指引。

## Last Updated

2026-08-17（GitHub 发布准备完成：LICENSE(MIT) 补齐、README 萌新友好版五步教程 + FAQ、全历史安全扫描零凭证泄漏）
