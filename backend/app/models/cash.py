"""Cash events, revisions, source records, scenarios and analysis results."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UTCDateTime
from app.models.merchant import (
    SOURCE_AI_EXTRACT,
    SOURCE_CONSULTATION,
    SOURCE_CSV_IMPORT,
    SOURCE_MANUAL,
    MerchantProfile,
)
from app.models.user import new_id

DIRECTION_INFLOW = "inflow"
DIRECTION_OUTFLOW = "outflow"
DIRECTIONS = (DIRECTION_INFLOW, DIRECTION_OUTFLOW)

STATE_SCHEDULED = "scheduled"
STATE_INCLUDED_IN_OPENING = "included_in_opening"
STATE_CANCELLED = "cancelled"
STATES = (STATE_SCHEDULED, STATE_INCLUDED_IN_OPENING, STATE_CANCELLED)

SOURCE_TYPES = (SOURCE_MANUAL, SOURCE_CSV_IMPORT, SOURCE_AI_EXTRACT, SOURCE_CONSULTATION)

EVENT_TYPES = (
    "settlement",
    "sale_receipt",
    "supplier_payment",
    "rent",
    "refund",
    "payroll",
    "utility",
    "tax",
    "loan_repayment",
    "platform_fee",
    "transfer_in",
    "transfer_out",
    "other_inflow",
    "other_outflow",
)

# 影响金额计算的字段：任何变化都必须产生新版本并触发重算
MATERIAL_FIELDS = ("amount_cents", "scheduled_at", "direction", "state")


class SourceRecord(Base, TimestampMixin):
    """Raw provenance for every cash event."""

    __tablename__ = "source_records"
    __table_args__ = (Index("ix_source_records_merchant_created", "merchant_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    merchant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("merchant_profiles.id", ondelete="CASCADE"), nullable=False
    )
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    file_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    stored_file_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    row_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    raw_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    import_batch_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)

    merchant: Mapped[MerchantProfile] = relationship()


class CashEvent(Base, TimestampMixin):
    """A normalised, already-confirmed cash movement."""

    __tablename__ = "cash_events"
    __table_args__ = (
        UniqueConstraint("merchant_id", "cash_key", name="uq_cash_events_merchant_cash_key"),
        Index("ix_cash_events_merchant_scheduled", "merchant_id", "scheduled_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    merchant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("merchant_profiles.id", ondelete="CASCADE"), nullable=False
    )
    cash_key: Mapped[str] = mapped_column(String(128), nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), default="other_inflow", nullable=False)
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    scheduled_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    state: Mapped[str] = mapped_column(String(24), default=STATE_SCHEDULED, nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), default=SOURCE_MANUAL, nullable=False)
    source_label: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_record_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("source_records.id", ondelete="SET NULL"), nullable=True
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    sequence_index_optional: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    current_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)

    source_record: Mapped[SourceRecord | None] = relationship(lazy="joined")
    revisions: Mapped[list["CashEventRevision"]] = relationship(
        back_populates="cash_event",
        cascade="all, delete-orphan",
        order_by="CashEventRevision.version.desc()",
    )


class CashEventRevision(Base):
    """Append-only change history for a cash event."""

    __tablename__ = "cash_event_revisions"
    __table_args__ = (
        UniqueConstraint("cash_event_id", "version", name="uq_cash_event_revisions_event_version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    cash_event_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("cash_events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    before_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    after_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    changed_fields: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    material: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    change_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    changed_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    changed_by_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    changed_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, index=True)

    cash_event: Mapped[CashEvent] = relationship(back_populates="revisions")


class Scenario(Base, TimestampMixin):
    """A what-if overlay; never mutates the underlying cash events."""

    __tablename__ = "scenarios"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    merchant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("merchant_profiles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), default="current_plan", nullable=False)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)

    overrides: Mapped[list["ScenarioEventOverride"]] = relationship(
        back_populates="scenario", cascade="all, delete-orphan", lazy="selectin"
    )


class ScenarioEventOverride(Base, TimestampMixin):
    __tablename__ = "scenario_event_overrides"
    __table_args__ = (
        UniqueConstraint(
            "scenario_id", "cash_event_id", name="uq_scenario_overrides_scenario_event"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    scenario_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scenarios.id", ondelete="CASCADE"), nullable=False, index=True
    )
    cash_event_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("cash_events.id", ondelete="CASCADE"), nullable=False
    )
    scheduled_at_override: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    amount_cents_override: Mapped[int | None] = mapped_column(Integer, nullable=True)
    direction_override: Mapped[str | None] = mapped_column(String(16), nullable=True)
    state_override: Mapped[str | None] = mapped_column(String(24), nullable=True)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)

    scenario: Mapped[Scenario] = relationship(back_populates="overrides")


class AnalysisResult(Base, TimestampMixin):
    """A persisted, reproducible engine run."""

    __tablename__ = "analysis_results"
    __table_args__ = (
        Index("ix_analysis_results_merchant_created", "merchant_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    merchant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("merchant_profiles.id", ondelete="CASCADE"), nullable=False
    )
    snapshot_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("business_account_snapshots.id", ondelete="SET NULL"), nullable=True
    )
    scenario_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("scenarios.id", ondelete="SET NULL"), nullable=True
    )
    scenario_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    mode: Mapped[str] = mapped_column(String(32), default="current_plan", nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    max_withdrawable_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    opening_balance_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    buffer_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    snapshot_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    window_end_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    limiting_timestamp: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    limiting_balance_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    limiting_event_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    payment_gap_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    buffer_gap_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    events_version_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    is_stale: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    stale_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: 生成该结果的引擎版本。2.0.0 之前的结果一律视为 stale：
    #: 旧口径把期初点排除在提用上限之外，结论不可继续作为决策依据。
    engine_version: Mapped[str] = mapped_column(String(16), default="2.0.0", nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
