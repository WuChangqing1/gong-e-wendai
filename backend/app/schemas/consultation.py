"""经营咨询数据结构。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.models.consultation import CASE_STATUSES, QUESTION_TYPES


class ConsultationCreate(BaseModel):
    cash_event_id: str
    question_type: str
    question: str = Field(min_length=2, max_length=2000)
    ai_draft: str | None = Field(default=None, max_length=4000)
    status: str = "submitted"

    @field_validator("question_type")
    @classmethod
    def _check_type(cls, value: str) -> str:
        if value not in QUESTION_TYPES:
            raise ValueError("不支持的问题类型")
        return value

    @field_validator("status")
    @classmethod
    def _check_status(cls, value: str) -> str:
        if value not in ("draft", "submitted"):
            raise ValueError("创建时只能是草稿或已提交")
        return value

    @field_validator("question")
    @classmethod
    def _strip(cls, value: str) -> str:
        cleaned = value.strip()
        if len(cleaned) < 2:
            raise ValueError("请把问题描述清楚")
        return cleaned


class ConsultationUpdateOut(BaseModel):
    id: str
    actor_name: str | None = None
    actor_role: str | None = None
    action: str
    from_status: str | None = None
    to_status: str | None = None
    content: str | None = None
    created_at: datetime


class ConsultationOut(BaseModel):
    id: str
    case_no: str
    merchant_id: str
    cash_event_id: str | None = None
    cash_event_version: int | None = None
    question_type: str
    question: str
    ai_draft: str | None = None
    shared_fields: dict = Field(default_factory=dict)
    allowed_field_names: list[str] = Field(default_factory=list)
    status: str
    resolution_summary: str | None = None
    resolution_fields: dict = Field(default_factory=dict)
    submitted_at: datetime | None = None
    closed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    updates: list[ConsultationUpdateOut] = Field(default_factory=list)


class AllowedFieldsOut(BaseModel):
    allowed_fields: list[str]
    forbidden_fields: list[str]
    preview: dict


class RequestInfoIn(BaseModel):
    content: str = Field(min_length=2, max_length=2000)

    @field_validator("content")
    @classmethod
    def _strip(cls, value: str) -> str:
        cleaned = value.strip()
        if len(cleaned) < 2:
            raise ValueError("请说明需要补充的资料")
        return cleaned


class VerifyIn(BaseModel):
    resolution_summary: str = Field(min_length=2, max_length=4000)
    resolution_fields: dict = Field(default_factory=dict)

    @field_validator("resolution_summary")
    @classmethod
    def _strip(cls, value: str) -> str:
        cleaned = value.strip()
        if len(cleaned) < 2:
            raise ValueError("请填写核实结果")
        return cleaned


class CloseIn(BaseModel):
    resolution_summary: str | None = Field(default=None, max_length=4000)


class ApplyUpdateOut(BaseModel):
    cash_event_id: str
    new_version: int
    changed_fields: list[str] = Field(default_factory=list)


__all__ = [
    "AllowedFieldsOut",
    "ApplyUpdateOut",
    "CASE_STATUSES",
    "CloseIn",
    "ConsultationCreate",
    "ConsultationOut",
    "ConsultationUpdateOut",
    "RequestInfoIn",
    "VerifyIn",
]
