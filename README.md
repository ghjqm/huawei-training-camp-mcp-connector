# Huawei Training Camp MCP

让 AI 通过 [MCP（Model Context Protocol）](https://modelcontextprotocol.io) 查询并分析
Huawei Training Camp / Huawei Health 网页端的运动训练数据。

> **安全边界**：本项目不实现任何绕过华为权限、破解认证、窃取 Cookie、
> 绕过访问控制的功能。认证信息只由用户本人通过环境变量显式提供
> （来自用户自己已登录的浏览器会话），不写入代码 / 配置 / Git。

## 功能（MCP Tools）

| 工具 | 说明 |
|---|---|
| `health_check` | 服务连通性检查 |
| `get_recent_activities(limit=5, lookback_days=30)` | 最近训练列表（活动 id / 时间 / 类型 / 距离 / 心率 / 配速 / VO2max / 训练负荷等摘要） |
| `get_activity_detail(activity_id, lookback_days=30)` | 单次训练高频采样详情（心率 / 速度 / 步频 / 海拔 / 位置 / 跑姿，通用 field-value 结构） |
| `get_training_summary(activity_id, lookback_days=30)` | 单次训练确定性统计摘要（每采集器样本数 / 时间范围 / 各字段 min-max-avg-first-last），无 AI 推断 |
| `get_session_metrics(activity_id, lookback_days=30)` | 单次训练核心指标（时长 / 心率 / 速度 / 配速 / 距离，带 device_summary/samples 来源标记） |
| `get_training_period_summary(lookback_days=7, limit=100)` | 一段周期内多次训练聚合（次数 / 总距离 / 总历时 / 平均与最大单次 / 心率 / 卡路里 / 累计爬升下降 / 步数 / 按 activityType 分组），类型值原样保留不命名语义；另含总纯运动时长（activeTime 毫秒→秒，全缺为 None）与周期平均配速（Σ纯运动秒/Σ公里，仅统计距离与纯运动时长双值活动，设备口径） |
| `get_training_period_comparison(window_days=7, limit=100)` | 两个连续周期对比：最近 N 天 vs 之前 N 天（每指标 baseline / current / delta / percentage_change；缺失≠0，baseline 为 0 或缺失时 pct 为 None 不除零） |
| `get_training_weekly_trend(weeks=4, limit=100)` | 最近 N 个连续周窗口的训练量趋势（滚动 7 天窗口、同一时间锚点、不重叠；每周完整周期聚合：次数/距离/历时/卡路里/步数/爬降/心率/类型分组，含总纯运动时长与周期配速） |
| `get_training_weekly_trend_delta(weeks=4, limit=100)` | 相邻周环比（older → newer，最多 weeks-1 对；复用周期对比的 delta/percentage_change 数学契约；weeks=1 返回空数组；环比指标清单暂不含周期配速，周聚合新字段见 get_training_weekly_trend） |
| `get_sleep_records(days=14)` | 最近 N 天睡眠记录（每晚：上床/入睡/醒来时刻，浅睡/深睡/快速眼动/清醒分段分钟，总睡眠、得分、效率、入睡用时、夜醒次数；服务端解析口径，无本地推断） |
| `get_resting_heart_rate(days=28)` | 最近 N 天静息心率统计（服务端聚合：全窗口 avg/max/min/count + 按天明细；fieldName=restBpm，bpm） |
| `get_hrv_stats(days=28)` | 最近 N 天 HRV 统计（服务端聚合：全窗口 + 按天明细；fieldName=avgHrv，数值原样返回——华为未声明单位与算法口径，不擅自换算） |

- `lookback_days`：查询窗口天数，默认 30，范围 1~730；查更早的训练时调大。
- `weeks`（周趋势）：周窗口数，默认 4，范围 1~104（总跨度 ≤728 天 ≤730 上限）；
  滚动 7 天连续窗口（非自然周，不引入时区/周起始日语义），单次列表请求整个
  跨度后本地归窗；恰在窗口边界的活动归较新窗口。
- `window_days`（周期对比）：单窗口天数，默认 7，范围 1~365；当前窗口
  `[now-N, now)`、基线窗口 `[now-2N, now-N)`，相邻不重叠；返回记录还会按
  自身开始时间做窗口过滤，保证窗口语义（历史区间的服务端行为尚未真实验证）。
- `activity_id` 来自 `get_recent_activities` 返回；详情查询内部先在列表中
  定位该活动（HTC API 不支持按 id 直查详情），再拉取高频数据。

## 环境变量（认证，全部由用户显式提供）

| 变量 | 必填 | 说明 |
|---|---|---|
| `HTC_AUTHORIZATION` | 是 | 浏览器成功请求中的 `Authorization` 头的值（如 `Bearer ...`） |
| `HTC_CLIENT_ID` | 是 | 浏览器成功请求中的 `x-client-id` 头的值 |
| `HTC_VERSION` | 否 | `x-version` 头的值（如 `HealthKitRunningGroupPortal_6.26.4.200`） |
| `HTC_COOKIE` | 否 | `Cookie` 头的值 |

取值方式：在自己浏览器开发者工具 → Network 中，找到网页端对
`activityRecord:query` 的成功请求，复制其请求头的值。

## 安装与运行

```powershell
# 1. 创建虚拟环境并安装依赖（Python 3.12+）
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt

# 2. 设置认证环境变量（当前终端会话有效；绝不写入文件）
$env:HTC_AUTHORIZATION = "<你的 Authorization 值>"
$env:HTC_CLIENT_ID     = "<你的 x-client-id 值>"

# 2b.（可选）补全请求头仿真，降低被服务端拒绝的概率
$env:HTC_VERSION = "HealthKitRunningGroupPortal_6.26.4.200"   # x-version 头
$env:HTC_COOKIE  = "<浏览器请求头里 Cookie 的完整值>"          # Cookie 头

# 3. （可选）真实 API 连通性自检（九种模式，任选）
.\.venv\Scripts\python.exe scripts\test_real_htc.py                    # list：最近训练列表
.\.venv\Scripts\python.exe scripts\test_real_htc.py detail             # detail：单次训练采样详情
.\.venv\Scripts\python.exe scripts\test_real_htc.py period             # period：周期聚合
.\.venv\Scripts\python.exe scripts\test_real_htc.py comparison 7       # comparison：连续两窗口对比
.\.venv\Scripts\python.exe scripts\test_real_htc.py trend 4            # trend：滚动周窗口趋势
.\.venv\Scripts\python.exe scripts\test_real_htc.py trend-delta 4      # trend-delta：相邻周环比
.\.venv\Scripts\python.exe scripts\test_real_htc.py sleep 14           # sleep：睡眠记录 + 自洽检查
.\.venv\Scripts\python.exe scripts\test_real_htc.py resting-hr 28      # resting-hr：静息心率统计
.\.venv\Scripts\python.exe scripts\test_real_htc.py hrv 28             # hrv：HRV 统计

# 4. 启动 MCP 服务（stdio）
.\.venv\Scripts\python.exe -m src.huawei_health_mcp.server
```

## PowerShell 快速测试卡（常用指令速查）

> 全部命令在项目根目录执行；`$env:` 设置的变量只在当前终端窗口有效，
> 新开窗口需要重新设置。四个凭证值的来源：浏览器 F12 → Network →
> 任意一条对 `activityRecord:query` 的成功请求 → Request Headers。

```powershell
# ---------- 环境配置（每次新开终端的第一步） ----------
$env:HTC_AUTHORIZATION = "<Authorization 值>"      # 必填
$env:HTC_CLIENT_ID     = "<x-client-id 值>"        # 必填
$env:HTC_VERSION       = "<x-version 值>"          # 可选
$env:HTC_COOKIE        = "<Cookie 值>"             # 可选

# 检查凭证是否已设置（只看前 20 个字符，避免整段 token 留在屏幕/日志里）
($env:HTC_AUTHORIZATION ?? "<未设置>").Substring(0, 20)
$env:HTC_CLIENT_ID ?? "<未设置>"

# 清空本终端凭证（测试结束后建议执行）
Remove-Item Env:HTC_AUTHORIZATION, Env:HTC_CLIENT_ID -ErrorAction SilentlyContinue

# ---------- 快速健康检查 ----------
.\.venv\Scripts\python.exe -m pytest -q                     # 全量单元测试（无需凭证，预期 244 passed）
.\.venv\Scripts\python.exe scripts\test_real_htc.py         # 最快连通性检查（list 模式，5 条记录）

# ---------- 常用验收组合 ----------
.\.venv\Scripts\python.exe scripts\test_mcp_client.py       # MCP 客户端全工具验收（预期 12/12 passed）
.\.venv\Scripts\python.exe scripts\test_real_htc.py sleep 7         # 昨晚睡眠（含自洽恒等式检查）
.\.venv\Scripts\python.exe scripts\test_real_htc.py resting-hr 7    # 最近一周静息心率
.\.venv\Scripts\python.exe scripts\test_real_htc.py hrv 7           # 最近一周 HRV
.\.venv\Scripts\python.exe scripts\test_real_htc.py trend 8         # 最近 8 周训练趋势
.\.venv\Scripts\python.exe scripts\test_real_htc.py trend-delta 8   # 相邻周环比

# ---------- 结果速判 ----------
# SUCCESS / passed = 链路正常
# status=401 Invalid Credentials = token 过期 → 重新抓 Authorization（刷新页面后抓"新"请求）
# WARNING: HTC_VERSION not set = 只是提示，不影响请求（可选头未配置）
# WARNING: HTC_COOKIE not set = 同上
```

### 接入 MCP 客户端（如 Claude Desktop / Cherry Studio 等）

在客户端的 MCP 配置中加入（路径按实际位置调整）：

```json
{
  "mcpServers": {
    "huawei-training-camp": {
      "command": "C:\\path\\to\\huawei-health-mcp\\.venv\\Scripts\\python.exe",
      "args": ["-m", "src.huawei_health_mcp.server"],
      "cwd": "C:\\path\\to\\huawei-health-mcp",
      "env": {
        "HTC_AUTHORIZATION": "<你的值>",
        "HTC_CLIENT_ID": "<你的值>"
      }
    }
  }
}
```

> 注意：凭证失效后工具会返回清晰的 401/HTTP 错误；重新从浏览器获取并更新
> 环境变量即可，本项目不做自动刷新。

## 开发

```powershell
# 运行全部测试（全部 Mock，不联网、不需要凭证）
.\.venv\Scripts\python.exe -m pytest
```

```
src/huawei_health_mcp/
├─ client.py             # HTCClient：HTTP 取数，返回原始 JSON（不解析）
├─ parser.py             # 训练原始 JSON → 标准模型（时间戳契约：记录 ms / 采样 ns）
├─ health_parser.py      # 恢复状态解析（睡眠记录 / 静息心率 / HRV 统计）
├─ models.py             # Pydantic 模型（ActivityRecord / TrainingSummary / SleepRecord / HealthMetricStats 族）
├─ analysis.py           # 确定性统计分析（ActivityDetail → TrainingSummary）
├─ period_analysis.py    # 周期聚合（多 ActivityRecord → TrainingPeriodSummary）
├─ period_comparison.py  # 周期对比（两个 TrainingPeriodSummary → TrainingPeriodComparison）
├─ trend_analysis.py     # 滚动周趋势与相邻周环比（复用聚合层与对比层）
└─ server.py             # FastMCP 服务与工具接线
scripts/test_real_htc.py   # 人工真实请求脚本（list / detail / period / comparison / trend / trend-delta / sleep / resting-hr / hrv 九模式，需要环境变量凭证）
scripts/test_mcp_client.py # 真实 MCP 客户端验收（12 个 tool 全调用，需要环境变量凭证）
tests/                     # 单元测试（synthetic fixture，无真实用户数据）
PROJECT_STATE.md           # 项目长期状态账本（新贡献者/Agent 从这里恢复上下文）
```

开发约定：认证只经环境变量；真实响应只存 `data_temp/`（git-ignored）；
测试全部使用 Mock / synthetic 数据；每个 Phase 完成后更新 `PROJECT_STATE.md`。

## 已验证的真实 API 契约（摘要）

- 列表 / 详情响应顶层均为 `list`
- 记录级与详情顶层时间为毫秒；采集器 / 采样点时间为纳秒
- 采样值 `floatValue` → float、`integerValue` → int
- 请求的数据类型不保证全部返回（`details` / `samplePoints` 可为空，属正常）
- 恢复状态端点（Phase 7 勘探确认）：`GET /healthRecords` 时间戳为纳秒；
  `healthRecords` 统计的 startDay/endDay 为字符串、`sampleSet` 统计为数字
  （两端口径不混用）；睡眠分段为分钟（light+deep+dream=all 自洽）

## 许可证

计划以开源协议发布，具体协议待定。

## 免责声明

本项目仅用于分析用户**自己账号、自己授权**的运动数据。不实现任何绕过
华为权限、破解认证、窃取 Cookie、绕过访问控制的功能。