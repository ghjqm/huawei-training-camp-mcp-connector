"""标准化后的单次运动数据模型（与华为原始字段解耦）。

这里只保留“训练分析”需要的标准化字段，不暴露华为原始 JSON 里的所有字段。
所有时间字段都使用 Python datetime（纳秒时间戳已由 parser 转换好）。

单位说明（activeTime 单位已由 Phase 6.13 真实数据确认）：
- active_time / active_time_ms: 毫秒（原样保留，不换算）
- distance: 米
- *heart_rate: bpm
- avg_pace / best_pace: 秒/公里
- recovery_time: 小时
- 采样点 value: 心率=bpm，步频=step_rate(spm)
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class SamplePoint(BaseModel):
    """单个高频采样点，例如某一刻的心率或步频。"""

    time: datetime
    value: float


class ActivityRecord(BaseModel):
    """标准化后的单次运动（ActivityRecord）。

    由 parser.parse_activity_record() 从华为原始 JSON 转换得到。
    任何在原始数据里缺失的字段都为 None 或空列表。
    """

    # 基础信息
    activity_id: Optional[str] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    activity_type: Optional[int] = None  # HTC 实际返回整数（如 90、56），已由真实响应确认
    active_time: Optional[int] = None  # 毫秒（Phase 6.13 真实数据确认；原样保留不换算）
    active_time_ms: Optional[int] = None  # 毫秒（记录级顶层 activeTime，Phase 6.13 新增的显式单位字段，与 active_time 同源同值）
    distance: Optional[float] = None   # 米
    calories: Optional[float] = None   # kcal（activitySummary calories_total，设备口径原值）
    ascent: Optional[float] = None     # 米（累计爬升，altitude.statistics ascent_total）
    descent: Optional[float] = None    # 米（累计下降，descent_total）
    steps: Optional[int] = None        # 步数（steps.total，计数语义）

    # 心率
    avg_heart_rate: Optional[float] = None  # bpm
    max_heart_rate: Optional[float] = None
    min_heart_rate: Optional[float] = None

    # 配速
    avg_pace: Optional[float] = None   # 秒/公里
    best_pace: Optional[float] = None  # 秒/公里
    pace_map: list[SamplePoint] = Field(default_factory=list)

    # 表现摘要
    vo2_max: Optional[float] = None
    recovery_time: Optional[int] = None     # 小时
    training_load: Optional[float] = None   # 有氧训练压力 aerobicTrainingStress

    # 高频采样序列
    heart_rate_samples: list[SamplePoint] = Field(default_factory=list)
    cadence_samples: list[SamplePoint] = Field(default_factory=list)

class DetailSamplePoint(BaseModel):
    """ActivityDetail 高频采样点（通用 field-value 结构）。

    真实 schema 确认：一个 sample 的 value 数组可包含多个 fieldName
    （如 location 的 latitude/longitude/precision/altitude/coordinate），
    因此不用单一 float 表达。values 键为 fieldName，值为 float 或 int
    （integerValue 保真为 int，floatValue 保真为 float）。
    timestamp 由 parser 按 nanosecond 契约转换好。
    """

    timestamp: datetime
    data_type_name: str
    values: dict[str, float | int] = Field(default_factory=dict)


class DetailCollector(BaseModel):
    """ActivityDetail 中的一个 dataCollector 时间序列。

    dataCollectorId 必须保留（真实 response 提供该字段，且内含数据类型
    与设备信息）。sample_points 允许为空——真实 API 已证明请求的
    dataType 不保证全部返回。
    """

    data_collector_id: Optional[str] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    sample_points: list[DetailSamplePoint] = Field(default_factory=list)


class ActivityDetail(BaseModel):
    """标准化后的单次活动详情（高频采样数据容器）。

    只保留后续 parser / MCP 真正需要的最小字段（name/desc/deviceInfo/
    appInfo/activitySummary 等暂不进入，需要时再扩展）。
    details 允许为空（缺失 collector 是合法状态）。

    时间契约（由真实响应确认，不按位数猜测；6.2-C 真实数据修正）：
    - ActivityRecord 记录级时间：毫秒（parser._ms_to_datetime）
    - ActivityDetail 顶层时间：毫秒（parser._ms_to_datetime；6.2-C 真实数据修正，
      顶层毫秒值与同一活动列表记录的 startTime 完全一致，交叉验证过）
    - ActivityDetail 的 collector / samplePoints 时间：纳秒（parser._ns_to_datetime）
    """

    activity_id: Optional[str] = None
    activity_type: Optional[int] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    active_time: Optional[int] = None  # 毫秒（Phase 6.13 真实数据确认；原样保留不换算）
    time_zone: Optional[str] = None
    details: list[DetailCollector] = Field(default_factory=list)

class FieldStats(BaseModel):
    """单个 fieldName 在一个 collector 内的确定性统计（Phase 6.3）。

    min/max/first/last 保留原始类型（int 字段保持 int），avg 恒为 float。
    """

    field_name: str
    count: int
    min_value: float | int
    max_value: float | int
    avg_value: float
    first_value: Optional[float | int] = None
    last_value: Optional[float | int] = None


class CollectorSummary(BaseModel):
    """一个 dataCollector 的统计摘要。

    data_type_name 从首个 samplePoint 推断（collector 本身无该字段）；
    时间范围优先用 collector 自带时间，缺失时退回首末 sample timestamp。
    """

    data_collector_id: Optional[str] = None
    data_type_name: Optional[str] = None
    sample_count: int = 0
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    fields: list[FieldStats] = Field(default_factory=list)


class TrainingSummary(BaseModel):
    """单次训练的确定性统计摘要（无 AI 推断，全部由采样数据聚合）。"""

    activity_id: Optional[str] = None
    activity_type: Optional[int] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    active_time: Optional[int] = None  # 毫秒（Phase 6.13 真实数据确认，原样保留）
    time_zone: Optional[str] = None
    collectors: list[CollectorSummary] = Field(default_factory=list)

class SessionMetrics(BaseModel):
    """单次训练的会话级指标（Phase 6.6，纯数学聚合，无业务标准）。

    双源策略（source 字段标记数据来源，便于判断可信度）：
    - device_summary：设备侧权威统计（activitySummary），优先采用
    - samples：高频采样点聚合（与设备统计互为交叉验证）
    - None：该指标无数据

    单位契约：
    - *_speed：米/秒；*_pace：秒/公里；distance：米；duration：秒
    - active_time_raw 保留原始 activeTime 值（毫秒，Phase 6.13 真实数据确认）
    """

    activity_id: Optional[str] = None
    duration_seconds: Optional[float] = None
    active_time_raw: Optional[int] = None

    avg_heart_rate: Optional[float] = None
    max_heart_rate: Optional[float] = None
    min_heart_rate: Optional[float] = None
    heart_rate_source: Optional[str] = None

    avg_speed: Optional[float] = None
    max_speed: Optional[float] = None
    speed_source: Optional[str] = None

    avg_pace: Optional[float] = None
    best_pace: Optional[float] = None
    pace_source: Optional[str] = None

    distance: Optional[float] = None
    distance_source: Optional[str] = None

class ActivityTypeSummary(BaseModel):
    """周期内单一 activityType 的基础统计（Phase 6.7）。

    activity_type 只保留 HTC 原始值（int，如 56/90），不自行命名语义——
    类型→运动名的映射未经真实证据确认，属 Manager 决策。
    Phase 6.13 起与 TrainingPeriodSummary 对称提供活跃时间与周期配速字段
    （契约见 TrainingPeriodSummary 注释）。
    """

    activity_type: int
    count: int = 0
    total_distance: Optional[float] = None  # 米；None=该类型无任何距离数据
    total_duration_seconds: float = 0.0
    total_active_time_seconds: Optional[float] = None  # 秒（Σ active_time_ms/1000；全缺→None）
    active_time_activity_count: int = 0
    average_pace_seconds_per_km: Optional[float] = None  # ΣactiveTime_s/Σdistance_km（双值活动子集）
    pace_activity_count: int = 0


class TrainingPeriodSummary(BaseModel):
    """一段查询周期内多次训练的聚合统计（Phase 6.7，纯数学，无业务标准）。

    单位契约：distance=米，duration=秒。
    语义契约：
    - duration = 每条记录 end_time - start_time 的总历时（含间歇），
      非纯运动时间（纯运动时长见 total_active_time_seconds）
    - distance 只聚合设备权威 summary 值（不积分采样）；缺失活动不计入，
      由 distance_activity_count 标记有效样本数
    - HR 只聚合设备 summary 的 per-activity avg/max；hr_activity_count 标记
      有效样本数；类型与来源不混（无 samples 来源混入）
    - Phase 6.13 活跃时间契约：activeTime 单位 = 毫秒（真实数据确认）；
      total_active_time_seconds = Σ(active_time_ms/1000)（唯一单位换算点），
      仅统计有 active_time_ms 的活动，全缺 → None（不伪造 0）
    - Phase 6.13 周期配速契约：average_pace_seconds_per_km =
      ΣactiveTime(秒) / Σdistance(公里)，只在 distance 与 active_time_ms
      双值活动子集上计算（设备口径，证据 B：设备 avgPace 恒等
      activeTime_s/distance_km）；配速是比率，禁止对 per-activity
      配速做算术平均
    """

    activity_count: int = 0
    period_start: Optional[datetime] = None  # 最早活动 start_time
    period_end: Optional[datetime] = None     # 最晚活动 end_time

    total_distance: Optional[float] = None
    distance_activity_count: int = 0
    total_calories: Optional[float] = None    # kcal（设备口径原值求和）
    calories_activity_count: int = 0
    total_ascent: Optional[float] = None      # 米
    total_descent: Optional[float] = None     # 米（与 ascent 同源同可用性）
    ascent_activity_count: int = 0            # 同时覆盖 descent 的有效样本数
    total_steps: Optional[int] = None
    steps_activity_count: int = 0
    total_duration_seconds: float = 0.0
    duration_activity_count: int = 0
    total_active_time_seconds: Optional[float] = None  # 秒（Σ active_time_ms/1000；全缺→None）
    active_time_activity_count: int = 0
    average_pace_seconds_per_km: Optional[float] = None  # 秒/公里（ΣactiveTime_s/Σdistance_km，双值活动子集）
    pace_activity_count: int = 0

    average_distance: Optional[float] = None
    average_duration_seconds: Optional[float] = None
    max_distance: Optional[float] = None
    max_duration_seconds: Optional[float] = None

    average_heart_rate: Optional[float] = None
    max_heart_rate: Optional[float] = None
    hr_activity_count: int = 0

    activity_types: list[ActivityTypeSummary] = Field(default_factory=list)

class MetricComparison(BaseModel):
    """单一指标的跨周期对比（Phase 6.9，纯数学，无业务标准）。

    数学契约：
    - delta = current - baseline；任一侧为 None → delta=None
      （None=无可靠数据，不得当作 0 参与运算；例如 baseline 距离
      全缺时无法证明基线是 0，delta 只能是 None）
    - percentage_change = (current - baseline) / baseline * 100；
      baseline 为 None 或 ==0 → None（不制造除零，不伪造基线）
    - count 类指标（int，0=真实零而非缺失）delta 恒可计算；
      percentage_change 仍受 baseline==0 约束
    - 类型保真：int 输入（如 activity_count）在 delta 中保持 int
    """

    baseline: Optional[float | int] = None
    current: Optional[float | int] = None
    delta: Optional[float | int] = None
    percentage_change: Optional[float] = None


class TrainingPeriodComparison(BaseModel):
    """两个训练周期的可验证对比（Phase 6.9）。

    - baseline/current 两个完整 TrainingPeriodSummary 原样内嵌：
      各 *_activity_count（含 hr_activity_count）与 activity_types
      分组随 summary 保真，不单独重复展开对比
    - MetricComparison 层只覆盖总量与 HR 指标；average/max 单次值
      不对比（可由内嵌 summary 直接读取，避免语义重复）
    - HR 对比仅基于 per-activity device summary 聚合（继承 Phase 6.7
      契约，不混入采样来源）；任一周期 HR 全缺 → 对比为 None
    - 不包含周期配速对比（周期配速语义未决，见 PROJECT_STATE）
    """

    baseline_summary: TrainingPeriodSummary
    current_summary: TrainingPeriodSummary

    activity_count: MetricComparison = Field(default_factory=MetricComparison)
    total_distance: MetricComparison = Field(default_factory=MetricComparison)
    total_calories: MetricComparison = Field(default_factory=MetricComparison)
    total_ascent: MetricComparison = Field(default_factory=MetricComparison)
    total_descent: MetricComparison = Field(default_factory=MetricComparison)
    total_steps: MetricComparison = Field(default_factory=MetricComparison)
    total_duration_seconds: MetricComparison = Field(default_factory=MetricComparison)
    average_heart_rate: MetricComparison = Field(default_factory=MetricComparison)
    max_heart_rate: MetricComparison = Field(default_factory=MetricComparison)


class TrainingWeekSummary(BaseModel):
    """单个连续 7 天周窗口的聚合（Phase 6.10）。

    - 窗口为滚动 7 天连续区间 [window_start, window_end)，半开区间；
      非自然周（不引入时区 / 周起始日语义，避免擅自决定业务标准）
    - 所有窗口由同一 now 锚点切分：相邻窗口共享边界值但不重叠
    - summary 为该窗口内 ActivityRecord 的完整 TrainingPeriodSummary，
      Phase 6.7/6.8 全字段契约继承（total_* / *_activity_count /
      activity_types 分组 / HR 设备 summary 来源 + hr_activity_count）
    """

    window_start: datetime
    window_end: datetime
    summary: TrainingPeriodSummary


class TrainingTrendSummary(BaseModel):
    """最近 N 个连续周窗口的训练趋势（Phase 6.10，纯聚合，无业务标准）。

    - weeks 按时间升序：weeks[0] 最旧，weeks[-1] 最近 [now-7d, now)
    - 所有窗口由同一 anchor 切分；活动按自身 start_time 归属唯一窗口，
      start_time 缺失或落在全部窗口之外的记录被排除
    - 不含周期配速、不解释 activityType 语义（契约见 PROJECT_STATE）
    """

    anchor: datetime
    window_days: int = 7
    weeks: list[TrainingWeekSummary] = Field(default_factory=list)


class WeekDeltaTransition(BaseModel):
    """相邻两个 7 天周窗口的环比变化（Phase 6.11，older → newer）。

    - 数学契约完全复用 MetricComparison / compare_metric（Phase 6.9）：
      delta = current - baseline；任一侧 None → delta=None（None≠0）；
      baseline 为 None 或 ==0 → percentage_change=None；count 类保持 int
    - baseline=older 周，current=newer 周；transitions 按时间升序排列
    - HR 继承 6.7 设备 summary 聚合来源；有效样本数不在此重复展开，
      由 trend.weeks[].summary.hr_activity_count 保真（同 6.9 契约）
    """

    baseline_window_start: datetime
    baseline_window_end: datetime
    current_window_start: datetime
    current_window_end: datetime

    activity_count: MetricComparison = Field(default_factory=MetricComparison)
    total_distance: MetricComparison = Field(default_factory=MetricComparison)
    total_calories: MetricComparison = Field(default_factory=MetricComparison)
    total_steps: MetricComparison = Field(default_factory=MetricComparison)
    total_ascent: MetricComparison = Field(default_factory=MetricComparison)
    total_descent: MetricComparison = Field(default_factory=MetricComparison)
    total_duration_seconds: MetricComparison = Field(default_factory=MetricComparison)
    average_heart_rate: MetricComparison = Field(default_factory=MetricComparison)
    max_heart_rate: MetricComparison = Field(default_factory=MetricComparison)


class TrainingTrendDelta(BaseModel):
    """周趋势环比容器（Phase 6.11，纯聚合，无业务标准）。

    - 窗口边界/顺序完全继承 TrainingTrendSummary（Phase 6.10 同一锚点
      半开区间），本层不重新定义任何窗口语义
    - transitions 按时间升序（最旧的相邻对在前），weeks=N 最多 N-1 个；
      weeks=1 时为空列表（无上一周可比较）
    - 不含周期配速；不解释 activityType 语义
    """

    anchor: datetime
    window_days: int = 7
    transitions: list[WeekDeltaTransition] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Phase 7：恢复状态数据域（睡眠 / 静息心率 / HRV）
# 单位契约（真实数据自洽验证，见 data_temp/htc探查.txt 勘探证据）：
# - 睡眠分段时间：分钟（light 199 + deep 165 + dream 106 = all 470 自洽）
# - restBpm：bpm；avgHrv：原样返回（典型 R-R 间期毫秒量级，华为未
#   在响应中声明单位，不擅自换算）
# - sleep_efficiency：百分比整数（98 = 98%）
# ---------------------------------------------------------------------------


class SleepRecord(BaseModel):
    """标准化后的单晚睡眠记录。

    由 health_parser.parse_sleep_records() 从 GET /healthRecords 原始
    JSON 转换得到。缺失字段为 None。时间契约：
    - start_time / end_time：纳秒时间戳（记录顶层，parser 转换）
    - go_bed / fall_asleep / wakeup：毫秒时间戳（value 字段）
    """

    record_id: Optional[str] = None
    start_time: Optional[datetime] = None  # 记录顶层 startTime（纳秒）
    end_time: Optional[datetime] = None    # 记录顶层 endTime（纳秒）
    go_bed_time: Optional[datetime] = None      # 上床时间（毫秒）
    fall_asleep_time: Optional[datetime] = None  # 入睡时间（毫秒）
    wakeup_time: Optional[datetime] = None       # 醒来时间（毫秒）

    light_sleep_minutes: Optional[int] = None    # 浅睡
    deep_sleep_minutes: Optional[int] = None     # 深睡
    dream_sleep_minutes: Optional[int] = None    # 快速眼动（dream_time）
    awake_minutes: Optional[int] = None          # 清醒时长
    total_sleep_minutes: Optional[int] = None    # all_sleep_time 总睡眠

    sleep_score: Optional[int] = None             # 睡眠得分
    sleep_efficiency_percent: Optional[int] = None  # 睡眠效率（%）
    sleep_latency_minutes: Optional[int] = None   # 入睡用时
    wakeup_count: Optional[int] = None            # 夜醒次数
    deep_sleep_part: Optional[int] = None  # 语义未确认（deep_sleep_part 原样保留）
    sleep_type: Optional[int] = None      # 语义未确认（sleep_type 原样保留）


class StatBlock(BaseModel):
    """periodStatistics 端点的通用统计块（服务端口径原样）。"""

    avg: Optional[float] = None
    max: Optional[float] = None
    min: Optional[float] = None
    count: Optional[int] = None


class DailyStat(BaseModel):
    """单个自然日的统计（来自 groupResults.groupStatistics）。

    day 为 "YYYY-MM-DD" 字符串（自然日语义由服务端按账号时区解释，
    本地不重新定义边界）。
    """

    day: str
    stats: StatBlock


class HealthMetricStats(BaseModel):
    """恢复状态指标统计容器（Phase 7，服务端聚合，无本地计算）。

    overall 来自响应 results（整个窗口汇总），daily 来自 groupResults
    （按天分组，时间升序）。全部为服务端口径，本层不做任何换算。
    """

    data_type: str
    field_name: Optional[str] = None  # 服务端返回的 fieldName（如 restBpm / avgHrv）
    days: int
    start_day: str  # "YYYY-MM-DD"（请求窗口起点，含）
    end_day: str    # "YYYY-MM-DD"（请求窗口终点，含）
    overall: StatBlock = Field(default_factory=StatBlock)
    daily: list[DailyStat] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Phase 8：训练能力域（运动能力评估 / PB 个人纪录）
# 单位契约（真实数据自洽验证，见 data_temp/htc探查.txt 勘探证据）：
# - predictedTimes：秒（halfMarathon 6701s=1:51:41 / marathon
#   14028s=3:53:48，业余跑者量级自洽）
# - PB value 与 name 耦合：*Time* 后缀 → 秒（bestRunPartTimeHM
#   7303.189s≈2:01:43 / 10KM 2746s=45:46），*Distance* 后缀 → 米
#   （bestRunDistance 26220m）——名称语义由华为定义，原样保留
# - runningAbility / condition / fitness / fatigue / ranking：华为
#   指数口径，无量纲，原样返回
# ---------------------------------------------------------------------------


class AthleticPerformance(BaseModel):
    """最新运动能力评估（服务端口径，无本地计算）。

    - 五项指数缺失为 None（不伪造 0）；condition 有正负
      （正=状态好，负=疲劳累积，量纲华为未公开）
    - predicted_times 键集合原样保留（证据：km1/km3/km5/km10/
      halfMarathon/marathon，秒）；不硬编码键清单，服务端增减
      距离项时自动透传
    """

    running_ability: Optional[float] = None
    condition: Optional[float] = None
    fitness: Optional[float] = None
    fatigue: Optional[float] = None
    ranking: Optional[float] = None
    predicted_times: dict[str, float] = Field(default_factory=dict)


class PersonalBestEntry(BaseModel):
    """单条个人纪录（name 语义由华为定义，原样保留）。"""

    name: str
    value: Optional[float] = None    # 单位与 name 耦合（见模块注释）
    start_time: Optional[datetime] = None  # 达成时段开始（ms）
    end_time: Optional[datetime] = None    # 达成时段结束（ms）


class SportPersonalBests(BaseModel):
    """单运动类型的 PB 容器（personalBest 数组原样逐条解析）。"""

    activity_type: str
    personal_bests: list[PersonalBestEntry] = Field(default_factory=list)
