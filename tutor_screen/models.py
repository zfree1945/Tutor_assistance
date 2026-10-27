from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


Grade = Literal["小学", "初中", "高中"]
Mode = Literal["线上", "线下", "线上/线下均可", "未知"]
Gender = Literal["男", "女", "不限", "未知"]
Slot = Literal["上午", "下午", "晚上"]
Weekday = Annotated[int, Field(strict=True, ge=1, le=7)]
DayOption = Annotated[list[Weekday], Field(min_length=1)]


class ScheduleOption(BaseModel):
    """星期和时段必须来自同一个备选安排。"""

    model_config = ConfigDict(extra="forbid")
    days: DayOption
    time_slots: list[Slot] = Field(default_factory=list)


class ScheduleWindow(BaseModel):
    """原文中的钟点窗口；窗口长度不必等于一节课的长度。"""

    model_config = ConfigDict(extra="forbid")
    days: list[Weekday] = Field(default_factory=list)
    start_minute: int = Field(strict=True, ge=0, lt=1440)
    end_minute: int = Field(strict=True, gt=0, le=1440)
    source: str = ""

    @model_validator(mode="after")
    def validate_window(self):
        if self.end_minute <= self.start_minute:
            raise ValueError("钟点窗口必须在同一天内按先后顺序排列")
        return self


class OrderBoundary(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    line: int = Field(ge=1)
    anchor: str = Field(min_length=2, max_length=120)


class BoundaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    boundaries: list[OrderBoundary]


class ExtractedOrder(BaseModel):
    """模型允许返回的字段；extra=forbid 防止意外内容混入。"""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    order_number: str | None = None
    grade: Grade | None = None
    subjects: list[str] = Field(default_factory=list)
    hourly_rate: float | None = Field(default=None, ge=0)
    hourly_rate_min: float | None = Field(default=None, ge=0)
    hourly_rate_max: float | None = Field(default=None, ge=0)
    price_incomplete: bool = False
    price_text: str | None = None
    mode: Mode = "未知"
    days: list[Weekday] = Field(default_factory=list)
    day_options: list[DayOption] = Field(default_factory=list)
    schedule_options: list[ScheduleOption] = Field(default_factory=list)
    schedule_windows: list[ScheduleWindow] = Field(default_factory=list)
    weekly_sessions: int | None = Field(default=None, ge=1)
    lesson_hours: float | None = Field(default=None, gt=0)
    time_slots: list[Slot] = Field(default_factory=list)
    address: str | None = None
    excluded_times: list[str] = Field(default_factory=list)
    excluded_days: list[Weekday] = Field(default_factory=list)
    teacher_gender: Gender = "未知"
    uncertain_fields: list[str] = Field(default_factory=list)
    field_sources: dict[str, str] = Field(default_factory=dict)

    @field_validator("days", "day_options", "schedule_options", "time_slots", "excluded_times", "excluded_days", "subjects", "uncertain_fields", mode="before")
    @classmethod
    def normalize_unknown_lists(cls, value):
        if value is None or isinstance(value, str) and value.strip() in {"", "未知", "待定", "不详", "可协商", "时间可协商", "不限"}:
            return []
        return value

    @field_validator("field_sources", mode="before")
    @classmethod
    def normalize_unknown_sources(cls, value):
        return {} if value is None else value

    @field_validator("mode", "teacher_gender", mode="before")
    @classmethod
    def normalize_unknown_requirement(cls, value):
        return "未知" if value is None else value

    @field_validator("price_incomplete", mode="before")
    @classmethod
    def normalize_unknown_price_completeness(cls, value):
        return True if value is None else value

    @model_validator(mode="after")
    def validate_price_range(self):
        low, high = self.hourly_rate_min, self.hourly_rate_max
        if (low is None) != (high is None):
            raise ValueError("薪资范围必须同时提供上下限")
        if low is not None and (low > high or self.hourly_rate is not None and not low <= self.hourly_rate <= high):
            raise ValueError("薪资范围无效")
        return self

    @model_validator(mode="after")
    def validate_schedule_consistency(self):
        options = [option.days for option in self.schedule_options] or self.day_options
        if options:
            union = {day for option in options for day in option}
            if self.days and set(self.days) != union:
                raise ValueError("备选安排与星期字段不一致")
            if self.day_options and {tuple(option) for option in self.day_options} != {tuple(option) for option in options}:
                raise ValueError("备选星期与完整安排不一致")
            if not self.days:
                self.days = sorted(union)
        if set(self.days) & set(self.excluded_days):
            raise ValueError("固定星期与排除星期冲突")
        return self


class Order(ExtractedOrder):
    order_id: str
    raw_text: str
    split_uncertain: bool = False
    extraction_source: Literal["model", "local", "failed"] = "local"
    extraction_note: str = ""
    match_status: Literal["明确匹配", "可能匹配", "无法解析", "已排除"] = "可能匹配"
    match_reason: str = ""


class Criteria(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    teacher_gender: Literal["不限", "男", "女"] = "不限"
    grades: list[Grade] = Field(default_factory=list)
    subjects: list[str] = Field(default_factory=list)
    mode: Literal["不限", "线上", "线下"] = "不限"
    days: list[Weekday] = Field(default_factory=list)
    max_weekly_sessions: int | None = Field(default=None, ge=1, le=7)
    time_slots: list[Slot] = Field(default_factory=list)
    min_hourly_rate: float | None = Field(default=None, ge=0)
