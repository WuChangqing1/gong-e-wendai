"""增强模块的请求 / 响应数据结构。"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# 历史经营数据
# ---------------------------------------------------------------------------
class DailyHistoryOut(BaseModel):
    id: str
    day: date
    complete: bool
    inflow_cents: int
    outflow_cents: int
    net_cents: int
    source_refs: list[str] = Field(default_factory=list)
    completeness_confirmed: bool = False
    note: str | None = None
    created_at: datetime
    updated_at: datetime


class DailyHistoryListOut(BaseModel):
    items: list[DailyHistoryOut] = Field(default_factory=list)
    complete_days: int = 0
    first_day: date | None = None
    last_day: date | None = None
    total_inflow_cents: int = 0
    total_outflow_cents: int = 0
    missing_days: list[date] = Field(default_factory=list)
    continuity_warning: str | None = None


class HistoryImportRow(BaseModel):
    day: date
    inflow_cents: int = Field(default=0, ge=0)
    outflow_cents: int = Field(default=0, ge=0)
    source_label: str = Field(default="", max_length=128)
    complete: bool = True


class HistoryImportPreviewRequest(BaseModel):
    rows: list[HistoryImportRow] = Field(min_length=1, max_length=5000)
    #: 用户必须明确确认该日期范围内数据完整
    completeness_confirmed: bool = False
    range_start: date | None = None
    range_end: date | None = None
    file_name: str | None = Field(default=None, max_length=255)
    #: 允许把范围内缺失的交易日记为 0（仅在 completeness_confirmed 为真时生效）
    fill_missing_days: bool = True


class HistoryImportIssue(BaseModel):
    row: int | None = None
    field: str | None = None
    reason: str
    level: str = "error"


class HistoryImportPreviewOut(BaseModel):
    total_rows: int
    valid_rows: int
    invalid_rows: int
    duplicate_rows: int
    missing_row_count: int
    range_start: date | None = None
    range_end: date | None = None
    completeness_confirmed: bool
    can_confirm: bool
    requires_completeness_confirmation: bool
    issues: list[HistoryImportIssue] = Field(default_factory=list)
    sample: list[HistoryImportRow] = Field(default_factory=list)
    preview_token: str


class HistoryImportConfirmRequest(BaseModel):
    preview_token: str
    rows: list[HistoryImportRow] = Field(min_length=1, max_length=5000)
    completeness_confirmed: bool = False
    fill_missing_days: bool = True


class HistoryImportConfirmOut(BaseModel):
    created: int
    updated: int
    skipped: int
    filled_zero_days: int
    history_revision: int
    message: str


# ---------------------------------------------------------------------------
# 结算记录
# ---------------------------------------------------------------------------
class SettlementRecordOut(BaseModel):
    id: str
    external_key: str
    channel: str
    scheduled_at: datetime
    actual_at: datetime | None = None
    known_at: datetime
    status: str
    source_ref: str
    note: str | None = None
    delay_days: int | None = None
    created_at: datetime


class SettlementRecordListOut(BaseModel):
    items: list[SettlementRecordOut] = Field(default_factory=list)
    open_count: int = 0
    completed_count: int = 0
    cancelled_count: int = 0
    channels: list[str] = Field(default_factory=list)


class SettlementImportRow(BaseModel):
    external_key: str = Field(min_length=1, max_length=128)
    channel: str = Field(min_length=1, max_length=64)
    scheduled_at: datetime
    actual_at: datetime | None = None
    known_at: datetime | None = None
    status: str = "open"
    source_ref: str = Field(default="", max_length=255)
    note: str | None = Field(default=None, max_length=255)

    @field_validator("status")
    @classmethod
    def _check_status(cls, value: str) -> str:
        if value not in {"open", "completed", "cancelled"}:
            raise ValueError("结算状态不合法")
        return value


class SettlementImportPreviewRequest(BaseModel):
    rows: list[SettlementImportRow] = Field(min_length=1, max_length=5000)
    file_name: str | None = Field(default=None, max_length=255)


class SettlementImportConfirmRequest(BaseModel):
    preview_token: str
    rows: list[SettlementImportRow] = Field(min_length=1, max_length=5000)


class SettlementImportConfirmOut(BaseModel):
    created: int
    updated: int
    skipped: int
    history_revision: int
    message: str


# ---------------------------------------------------------------------------
# 增强总览
# ---------------------------------------------------------------------------
class ForecastDailyOut(BaseModel):
    day: date
    inflow_cents: int
    outflow_cents: int
    net_cents: int
    inflow_text: str
    outflow_text: str
    net_text: str
    inflow_method: str
    inflow_method_label: str
    outflow_method: str
    outflow_method_label: str
    training_end: date
    source_refs: list[str] = Field(default_factory=list)
    provenance: str = "forecast"
    confirmed: bool = False


class ForecastOut(BaseModel):
    available: bool
    reason_code: str | None = None
    message: str | None = None
    history_days: int = 0
    required_days: int = 0
    training_end: date | None = None
    needs_review: bool = False
    needs_review_reason: str | None = None
    method_inflow: str | None = None
    method_inflow_label: str | None = None
    method_outflow: str | None = None
    method_outflow_label: str | None = None
    daily: list[ForecastDailyOut] = Field(default_factory=list)
    summary: dict[str, int] = Field(default_factory=dict)
    diagnostics: dict = Field(default_factory=dict)
    source_refs: list[str] = Field(default_factory=list)
    disclosure: str = ""
    #: 恒定 false：历史参考永远不会影响今天可提用金额
    forecast_affects_withdrawable: bool = False


class SettlementPressureScenarioOut(BaseModel):
    id: str
    label: str
    delay_days: int
    basis: str
    status: str
    status_label: str
    max_withdrawable_cents: int | None = None
    payment_gap_cents: int = 0
    buffer_gap_cents: int = 0
    minimum_balance_cents: int | None = None
    end_balance_cents: int | None = None
    limiting_timestamp: datetime | None = None
    limiting_event_title: str | None = None
    points: list[dict] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)


class SettlementPressureStatsOut(BaseModel):
    channel: str
    completed_count: int
    open_count: int
    overdue_open_count: int
    status: str
    quantile: float
    min_samples: int
    empirical_delay_days: int | None = None
    has_sample: bool
    headline: str
    source_refs: list[str] = Field(default_factory=list)
    open_source_refs: list[str] = Field(default_factory=list)
    disclosure: str


class SettlementPressureOut(BaseModel):
    available: bool
    message: str | None = None
    manual_delay_days: int = 2
    targets: list[dict] = Field(default_factory=list)
    scenarios: list[SettlementPressureScenarioOut] = Field(default_factory=list)
    stats: SettlementPressureStatsOut | None = None
    disclosure: str = ""


class ReserveAdviceOut(BaseModel):
    status: str
    current_reserve_cents: int
    suggested_reserve_cents: int
    extra_cents: int
    empirical_error_cents: int | None = None
    block_count: int
    quantile: float
    rounding_cents: int
    min_blocks: int
    basis_hash: str
    basis: dict = Field(default_factory=dict)
    source_refs: list[str] = Field(default_factory=list)
    disclosure: str
    requires_confirmation: bool = True
    has_suggestion: bool
    suggests_increase: bool
    headline: str


class EnhancementOverviewOut(BaseModel):
    merchant_id: str
    ledger_revision: int
    history_revision: int
    basis_hash: str
    generated_at: datetime
    needs_review: bool = False
    baseline: dict
    settlement_pressure: SettlementPressureOut
    forecast: ForecastOut
    reserve_advice: ReserveAdviceOut
    history: dict
    reserve_changed_since_advice: bool = False
    run_id: str | None = None
    #: 恒定 false
    forecast_affects_withdrawable: bool = False


class ReserveConfirmRequest(BaseModel):
    """确认采用建议留底。

    客户端必须回传生成建议时的依据版本，任一不一致都返回 409。

    ``run_id`` 指向生成这条建议的 ``EnhancementRun``。服务端**必须**从这条
    运行记录里读回当时的计算参数（延后天数、分位数、渠道、期初时点）后重新校验，
    否则用默认参数重算会得到另一个 ``basis_hash``，导致用户永远无法确认
    非默认参数下看到的建议。
    """

    suggested_reserve_cents: int = Field(ge=0)
    basis_hash: str = Field(min_length=8, max_length=64)
    ledger_revision: int = Field(ge=0)
    history_revision: int = Field(ge=0)
    run_id: str | None = Field(default=None, max_length=36)


class ReserveConfirmOut(BaseModel):
    confirmed_reserve_cents: int
    previous_reserve_cents: int
    history_revision: int
    ledger_revision: int
    stale_analysis_results: int
    message: str
    analysis: dict
