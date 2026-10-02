"""现金事件数据结构。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.models.cash import (
    DIRECTIONS,
    EVENT_TYPES,
    SOURCE_TYPES,
    STATES,
)
from app.schemas.common import ORMModel


class SourceRecordOut(ORMModel):
    id: str
    source_type: str
    file_name: str | None = None
    row_number: int | None = None
    raw_content: str | None = None
    content_hash: str | None = None
    import_batch_id: str | None = None
    created_at: datetime
    created_by: str | None = None


class SourceSummary(BaseModel):
    source_type: str
    source_label: str | None = None
    file_name: str | None = None
    row_number: int | None = None
    created_at: datetime | None = None


class CashEventCreate(BaseModel):
    cash_key: str | None = Field(default=None, max_length=128)
    title: str = Field(min_length=1, max_length=128)
    direction: str
    amount_cents: int = Field(ge=0, le=10**13)
    scheduled_at: datetime
    event_type: str = "other_inflow"
    state: str = "scheduled"
    source_label: str | None = Field(default=None, max_length=128)
    note: str | None = Field(default=None, max_length=2000)
    sequence_index_optional: int | None = Field(default=None, ge=0, le=10**6)

    @field_validator("direction")
    @classmethod
    def _check_direction(cls, value: str) -> str:
        if value not in DIRECTIONS:
            raise ValueError("收支方向只能是 inflow 或 outflow")
        return value

    @field_validator("state")
    @classmethod
    def _check_state(cls, value: str) -> str:
        if value not in STATES:
            raise ValueError("状态只能是 scheduled / included_in_opening / cancelled")
        return value

    @field_validator("event_type")
    @classmethod
    def _check_event_type(cls, value: str) -> str:
        if value not in EVENT_TYPES:
            raise ValueError("不支持的事项类型")
        return value

    @field_validator("title")
    @classmethod
    def _strip_title(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("事项名称不能为空")
        return cleaned


class CashEventUpdate(BaseModel):
    """修改现金事件。金额、时间、方向、状态的修改都会产生新版本。"""

    title: str | None = Field(default=None, min_length=1, max_length=128)
    direction: str | None = None
    amount_cents: int | None = Field(default=None, ge=0, le=10**13)
    scheduled_at: datetime | None = None
    event_type: str | None = None
    state: str | None = None
    source_label: str | None = Field(default=None, max_length=128)
    note: str | None = Field(default=None, max_length=2000)
    sequence_index_optional: int | None = Field(default=None, ge=0, le=10**6)
    change_reason: str | None = Field(default=None, max_length=255)

    @field_validator("direction")
    @classmethod
    def _check_direction(cls, value: str | None) -> str | None:
        if value is not None and value not in DIRECTIONS:
            raise ValueError("收支方向只能是 inflow 或 outflow")
        return value

    @field_validator("state")
    @classmethod
    def _check_state(cls, value: str | None) -> str | None:
        if value is not None and value not in STATES:
            raise ValueError("状态只能是 scheduled / included_in_opening / cancelled")
        return value

    @field_validator("event_type")
    @classmethod
    def _check_event_type(cls, value: str | None) -> str | None:
        if value is not None and value not in EVENT_TYPES:
            raise ValueError("不支持的事项类型")
        return value


class CashEventOut(ORMModel):
    id: str
    merchant_id: str
    cash_key: str
    event_type: str
    title: str
    amount_cents: int
    direction: str
    scheduled_at: datetime
    state: str
    source_type: str
    source_label: str | None = None
    source_record_id: str | None = None
    note: str | None = None
    sequence_index_optional: int | None = None
    confirmed: bool
    current_version: int
    created_by: str | None = None
    created_at: datetime
    updated_at: datetime
    source: SourceSummary | None = None


class CashEventDetail(CashEventOut):
    source_record: SourceRecordOut | None = None


class CashEventRevisionOut(ORMModel):
    id: str
    cash_event_id: str
    version: int
    before_json: dict | None = None
    after_json: dict | None = None
    changed_fields: list[str] = Field(default_factory=list)
    material: bool
    change_reason: str | None = None
    changed_by: str | None = None
    changed_by_name: str | None = None
    changed_at: datetime


class RevisionWithEvent(BaseModel):
    revision: CashEventRevisionOut
    event_id: str
    event_title: str
    cash_key: str


class RevisionDiff(BaseModel):
    version: int
    changed_fields: list[str]
    material: bool
    change_reason: str | None = None
    changed_by_name: str | None = None
    changed_at: datetime
    before: dict | None = None
    after: dict | None = None
    changes: list[dict] = Field(default_factory=list)


class CancelRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=255)


class CashEventStats(BaseModel):
    total: int
    scheduled: int
    included_in_opening: int
    cancelled: int
    inflow_cents: int
    outflow_cents: int


__all__ = [
    "CancelRequest",
    "CashEventCreate",
    "CashEventDetail",
    "CashEventOut",
    "CashEventRevisionOut",
    "CashEventStats",
    "CashEventUpdate",
    "RevisionDiff",
    "RevisionWithEvent",
    "SOURCE_TYPES",
    "SourceRecordOut",
    "SourceSummary",
]
