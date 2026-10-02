"""Consultation cases, timeline updates and audit logs."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UTCDateTime
from app.models.merchant import MerchantProfile
from app.models.user import new_id

CASE_DRAFT = "draft"
CASE_SUBMITTED = "submitted"
CASE_UNDER_REVIEW = "under_review"
CASE_NEED_MORE_INFORMATION = "need_more_information"
CASE_VERIFIED = "verified"
CASE_CLOSED = "closed"

CASE_STATUSES = (
    CASE_DRAFT,
    CASE_SUBMITTED,
    CASE_UNDER_REVIEW,
    CASE_NEED_MORE_INFORMATION,
    CASE_VERIFIED,
    CASE_CLOSED,
)

CASE_STATUS_LABELS = {
    CASE_DRAFT: "草稿",
    CASE_SUBMITTED: "已提交",
    CASE_UNDER_REVIEW: "处理中",
    CASE_NEED_MORE_INFORMATION: "待补充资料",
    CASE_VERIFIED: "已核实",
    CASE_CLOSED: "已完成",
}

# 咨询字段白名单：只有这些字段可以进入咨询事项
CONSULTATION_ALLOWED_FIELDS = (
    "case_no",
    "event_type",
    "event_title",
    "amount_cents",
    "scheduled_at",
    "event_state",
    "source_summary",
    "question",
    "event_version",
)

CONSULTATION_FORBIDDEN_FIELDS = (
    "opening_balance_cents",
    "buffer_cents",
    "max_withdrawable_cents",
    "full_balance_curve",
    "household_info",
    "household_comments",
    "household_members",
    "credit_score",
)

QUESTION_TYPES = (
    "settlement_time",
    "amount_mismatch",
    "missing_arrival",
    "fee_unknown",
    "other",
)

QUESTION_TYPE_LABELS = {
    "settlement_time": "到账/结算时间不明确",
    "amount_mismatch": "金额与预期不一致",
    "missing_arrival": "款项未到账",
    "fee_unknown": "手续费/费用不清楚",
    "other": "其他经营资金事项",
}


class ConsultationCase(Base, TimestampMixin):
    __tablename__ = "consultation_cases"
    __table_args__ = (
        Index("ix_consultation_cases_merchant_created", "merchant_id", "created_at"),
        Index("ix_consultation_cases_status", "status", "updated_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_no: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    merchant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("merchant_profiles.id", ondelete="CASCADE"), nullable=False
    )
    cash_event_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("cash_events.id", ondelete="SET NULL"), nullable=True
    )
    cash_event_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    question_type: Mapped[str] = mapped_column(String(32), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    ai_draft: Mapped[str | None] = mapped_column(Text, nullable=True)
    shared_fields: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    allowed_field_names: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default=CASE_DRAFT, nullable=False)
    assignee_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    resolution_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolution_fields: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    provider_key: Mapped[str] = mapped_column(String(32), default="internal", nullable=False)
    external_reference: Mapped[str | None] = mapped_column(String(64), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)

    merchant: Mapped[MerchantProfile] = relationship()
    updates: Mapped[list["ConsultationUpdate"]] = relationship(
        back_populates="case",
        cascade="all, delete-orphan",
        order_by="ConsultationUpdate.created_at",
        lazy="selectin",
    )


class ConsultationUpdate(Base, TimestampMixin):
    __tablename__ = "consultation_updates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("consultation_cases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    actor_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    actor_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    actor_role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    from_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    visible_to_merchant: Mapped[bool] = mapped_column(default=True, nullable=False)

    case: Mapped[ConsultationCase] = relationship(back_populates="updates")


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_actor_created", "actor_id", "created_at"),
        Index("ix_audit_logs_resource", "resource_type", "resource_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    actor_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    actor_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    resource_type: Mapped[str | None] = mapped_column(String(48), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    merchant_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, index=True)
