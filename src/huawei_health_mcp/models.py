"""标准化后的单次运动数据模型（与华为原始字段解耦）。

这里只保留“训练分析”需要的标准化字段，不暴露华为原始 JSON 里的所有字段。
所有时间字段都使用 Python datetime（纳秒时间戳已由 parser 转换好）。

单位说明（需结合你的真实数据再确认）：
- active_time: 秒
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
    active_time: Optional[int] = None  # 秒
    distance: Optional[float] = None   # 米

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
    active_time: Optional[int] = None  # 秒
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
    active_time: Optional[int] = None  # 单位疑问见 PROJECT_STATE Known Issues #1
    time_zone: Optional[str] = None
    collectors: list[CollectorSummary] = Field(default_factory=list)