"""Household collaboration: household, membership and decision cards."""

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
from app.models.merchant import MerchantProfile
from app.models.user import User, new_id

MEMBERSHIP_PENDING = "pending"
MEMBERSHIP_ACTIVE = "active"
MEMBERSHIP_REMOVED = "removed"
MEMBERSHIP_STATUSES = (MEMBERSHIP_PENDING, MEMBERSHIP_ACTIVE, MEMBERSHIP_REMOVED)

CARD_DECISION = "decision"
CARD_RISK = "risk"
CARD_REVISION = "revision"
CARD_TYPES = (CARD_DECISION, CARD_RISK, CARD_REVISION)

REACTION_READ = "read"
REACTION_AGREE = "agree"
REACTION_DISCUSS = "discuss"
REACTION_TYPES = (REACTION_READ, REACTION_AGREE, REACTION_DISCUSS)


class Household(Base, TimestampMixin):
    __tablename__ = "households"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    owner_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    merchant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("merchant_profiles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    invite_code: Mapped[str] = mapped_column(String(16), unique=True, nullable=False, index=True)
    invite_code_rotated_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    invite_code_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    memberships: Mapped[list["HouseholdMembership"]] = relationship(
        back_populates="household", cascade="all, delete-orphan", lazy="selectin"
    )


class HouseholdMembership(Base, TimestampMixin):
    __tablename__ = "household_memberships"
    __table_args__ = (
        UniqueConstraint("household_id", "user_id", name="uq_household_memberships_household_user"),
        Index("ix_household_memberships_user_status", "user_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    household_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("households.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(32), default="member", nullable=False)
    relation_label: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default=MEMBERSHIP_PENDING, nullable=False)
    joined_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    household: Mapped[Household] = relationship(back_populates="memberships")
    user: Mapped[User] = relationship(lazy="selectin")


class HouseholdCard(Base, TimestampMixin):
    """A collaboration card explicitly shared by the merchant."""

    __tablename__ = "household_cards"
    __table_args__ = (Index("ix_household_cards_merchant_created", "merchant_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    household_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("households.id", ondelete="CASCADE"), nullable=False, index=True
    )
    merchant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("merchant_profiles.id", ondelete="CASCADE"), nullable=False
    )
    card_type: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    shared_fields: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    analysis_result_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    cash_event_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    planned_household_amount_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    system_max_withdrawable_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)

    recipients: Mapped[list["HouseholdCardRecipient"]] = relationship(
        back_populates="card", cascade="all, delete-orphan", lazy="selectin"
    )
    comments: Mapped[list["HouseholdCardComment"]] = relationship(
        back_populates="card",
        cascade="all, delete-orphan",
        order_by="HouseholdCardComment.created_at",
        lazy="selectin",
    )


class HouseholdCardRecipient(Base, TimestampMixin):
    __tablename__ = "household_card_recipients"
    __table_args__ = (
        UniqueConstraint("card_id", "user_id", name="uq_household_card_recipients_card_user"),
        Index("ix_household_card_recipients_user", "user_id", "is_read"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    card_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("household_cards.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    reaction: Mapped[str | None] = mapped_column(String(16), nullable=True)
    reacted_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    card: Mapped[HouseholdCard] = relationship(back_populates="recipients")
    user: Mapped[User] = relationship(lazy="selectin")


class HouseholdCardReaction(Base):
    """Append-only reaction history (audit friendly)."""

    __tablename__ = "household_card_reactions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    card_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("household_cards.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    reaction: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)

    user: Mapped[User] = relationship(lazy="selectin")


class HouseholdCardComment(Base):
    __tablename__ = "household_card_comments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    card_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("household_cards.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)

    card: Mapped[HouseholdCard] = relationship(back_populates="comments")
    user: Mapped[User] = relationship(lazy="selectin")


__all__ = [
    "CARD_DECISION",
    "CARD_REVISION",
    "CARD_RISK",
    "CARD_TYPES",
    "MEMBERSHIP_ACTIVE",
    "MEMBERSHIP_PENDING",
    "MEMBERSHIP_REMOVED",
    "MEMBERSHIP_STATUSES",
    "REACTION_AGREE",
    "REACTION_DISCUSS",
    "REACTION_READ",
    "REACTION_TYPES",
    "Household",
    "HouseholdCard",
    "HouseholdCardComment",
    "HouseholdCardReaction",
    "HouseholdCardRecipient",
    "HouseholdMembership",
    "MerchantProfile",
]
