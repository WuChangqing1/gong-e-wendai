"""Merchant profile and business account snapshots."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UTCDateTime
from app.models.user import User, new_id

CURRENCY_CNY = "CNY"
TIMEZONE_SHANGHAI = "Asia/Shanghai"

SOURCE_MANUAL = "manual"
SOURCE_CSV_IMPORT = "csv_import"
SOURCE_AI_EXTRACT = "ai_extract"
SOURCE_CONSULTATION = "consultation_update"

SNAPSHOT_SOURCE_TYPES = (SOURCE_MANUAL, SOURCE_CSV_IMPORT, SOURCE_CONSULTATION, "system")


class MerchantProfile(Base, TimestampMixin):
    __tablename__ = "merchant_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    business_name: Mapped[str] = mapped_column(String(128), nullable=False)
    business_type: Mapped[str] = mapped_column(String(64), default="个体工商户", nullable=False)
    contact_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    phone_optional: Mapped[str | None] = mapped_column(String(32), nullable=True)
    default_currency: Mapped[str] = mapped_column(String(8), default=CURRENCY_CNY, nullable=False)
    timezone: Mapped[str] = mapped_column(
        String(64), default=TIMEZONE_SHANGHAI, nullable=False
    )
    default_buffer_amount_cents: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    # 第一阶段：单一核心经营支付账户
    account_name: Mapped[str] = mapped_column(String(128), default="经营收款账户", nullable=False)
    account_masked_no: Mapped[str | None] = mapped_column(String(64), nullable=True)

    user: Mapped[User] = relationship(back_populates="merchant_profile")


class BusinessAccountSnapshot(Base, TimestampMixin):
    __tablename__ = "business_account_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    merchant_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("merchant_profiles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    opening_balance_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    pending_settlement_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    snapshot_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, index=True)
    currency: Mapped[str] = mapped_column(String(8), default=CURRENCY_CNY, nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), default=SOURCE_MANUAL, nullable=False)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)

    merchant: Mapped[MerchantProfile] = relationship()
