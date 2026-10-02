"""分析结果数据结构。"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field, field_validator

MODE_CURRENT_PLAN = "current_plan"
MODE_DELAYED = "delayed"
MODE_JOINT = "joint"
MODE_SCENARIOS = "scenarios"

MODES = (MODE_CURRENT_PLAN, MODE_DELAYED, MODE_JOINT, MODE_SCENARIOS)


class AnalysisRunRequest(BaseModel):
    mode: str = MODE_CURRENT_PLAN
    snapshot_at: datetime | None = None
    scenario_ids: list[str] | None = None
    delay_days: int = Field(default=3, ge=1, le=30)
    buffer_cents: int | None = Field(default=None, ge=0)

    @field_validator("mode")
    @classmethod
    def _check_mode(cls, value: str) -> str:
        if value not in MODES:
            raise ValueError("不支持的分析模式")
        return value


class ScenarioCurve(BaseModel):
    label: str
    kind: str
    status: str
    status_label: str
    max_withdrawable_cents: int | None = None
    limiting_timestamp: datetime | None = None
    limiting_balance_cents: int | None = None
    limiting_event_id: str | None = None
    limiting_event_title: str | None = None
    limiting_reason: str
    payment_gap_cents: int
    buffer_gap_cents: int
    end_balance_cents: int | None = None
    points: list[dict] = Field(default_factory=list)
    pending_inflows_at_limit: list[dict] = Field(default_factory=list)
    window_inflow_cents: int = 0
    window_outflow_cents: int = 0


class AnalysisResultOut(BaseModel):
    id: str | None = None
    merchant_id: str
    mode: str
    mode_label: str
    status: str
    status_label: str
    max_withdrawable_cents: int | None = None
    binding_label: str | None = None
    opening_balance_cents: int
    buffer_cents: int
    currency: str
    snapshot_at: datetime
    window_end_at: datetime
    limiting_timestamp: datetime | None = None
    limiting_balance_cents: int | None = None
    limiting_event_id: str | None = None
    limiting_event_title: str | None = None
    limiting_reason: str
    pending_inflows_at_limit: list[dict] = Field(default_factory=list)
    payment_gap_cents: int
    buffer_gap_cents: int
    minimum_balance_cents: int | None = None
    balance_floor_cents: int | None = None
    end_balance_cents: int | None = None
    opening_covers_buffer: bool
    window_inflow_cents: int
    window_outflow_cents: int
    pending_settlement_cents: int
    scenarios: list[ScenarioCurve] = Field(default_factory=list)
    points: list[dict] = Field(default_factory=list)
    validation_errors: list[dict] = Field(default_factory=list)
    excluded_event_ids: list[str] = Field(default_factory=list)
    engine_version: str
    is_stale: bool = False
    generated_at: datetime


class ScenarioOverrideIn(BaseModel):
    cash_event_id: str
    scheduled_at: datetime | None = None
    amount_cents: int | None = Field(default=None, ge=0, le=10**13)
    direction: str | None = None
    state: str | None = None
    note: str | None = Field(default=None, max_length=255)


class ScenarioOverrideOut(BaseModel):
    cash_event_id: str
    scheduled_at: datetime | None = None
    amount_cents: int | None = None
    direction: str | None = None
    state: str | None = None
    note: str | None = None


class ScenarioCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    kind: str = Field(default="custom", max_length=32)
    description: str | None = Field(default=None, max_length=255)
    overrides: list[ScenarioOverrideIn] = Field(default_factory=list)


class ScenarioUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=255)
    overrides: list[ScenarioOverrideIn] | None = None


class ScenarioOut(BaseModel):
    id: str
    merchant_id: str
    name: str
    kind: str
    description: str | None = None
    is_primary: bool
    created_at: datetime
    updated_at: datetime
    overrides: list[ScenarioOverrideOut] = Field(default_factory=list)


class StaleStatus(BaseModel):
    is_stale: bool
    last_generated_at: datetime | None = None
    stale_reason: str | None = None
    max_withdrawable_cents: int | None = None


# ---------------------------------------------------------------------------
# 窗口分析聚合（供图表使用）
#
# 全部由确定性引擎的事件扫描结果聚合得出，不重新定义任何金额规则：
# * 每日收支合计与当日期末余额
# * 按事项类型的收支结构
# * 待结算资金的到账时间分布
# ---------------------------------------------------------------------------
class DailyTerm(BaseModel):
    """某一天的收付款合计与当日期末余额。"""

    day: date
    inflow_cents: int
    outflow_cents: int
    net_cents: int
    closing_balance_cents: int
    event_count: int


class CategoryTerm(BaseModel):
    """按事项类型汇总的收支结构。"""

    event_type: str
    label: str
    direction: str
    amount_cents: int
    event_count: int
    share_ratio: float


class ArrivalTerm(BaseModel):
    """待结算资金（尚未到账的计划收入）的到账时间分布。"""

    day: date
    amount_cents: int
    event_count: int
    titles: list[str] = Field(default_factory=list)


class WindowSummary(BaseModel):
    window_start: datetime
    window_end: datetime
    window_days: int
    opening_balance_cents: int
    closing_balance_cents: int
    buffer_cents: int
    scheduled_inflow_cents: int
    scheduled_outflow_cents: int
    net_change_cents: int
    daily_terms: list[DailyTerm] = Field(default_factory=list)
    category_terms: list[CategoryTerm] = Field(default_factory=list)
    arrival_terms: list[ArrivalTerm] = Field(default_factory=list)
    event_count: int = 0
