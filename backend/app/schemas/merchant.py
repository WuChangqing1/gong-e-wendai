"""商户经营档案与经营账户数据结构。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.schemas.common import ORMModel


class MerchantProfileOut(ORMModel):
    id: str
    user_id: str
    business_name: str
    business_type: str
    contact_name: str | None = None
    phone_optional: str | None = None
    default_currency: str
    timezone: str
    default_buffer_amount_cents: int
    account_name: str
    account_masked_no: str | None = None
    created_at: datetime
    updated_at: datetime


class MerchantProfileUpdate(BaseModel):
    business_name: str | None = Field(default=None, min_length=1, max_length=128)
    business_type: str | None = Field(default=None, max_length=64)
    contact_name: str | None = Field(default=None, max_length=64)
    phone_optional: str | None = Field(default=None, max_length=32)
    account_name: str | None = Field(default=None, max_length=128)
    account_masked_no: str | None = Field(default=None, max_length=64)
    default_buffer_amount_cents: int | None = Field(default=None, ge=0)


class AccountSnapshotOut(ORMModel):
    id: str
    opening_balance_cents: int
    pending_settlement_cents: int
    snapshot_at: datetime
    currency: str
    source_type: str
    note: str | None = None
    created_at: datetime


class AccountSnapshotCreate(BaseModel):
    opening_balance_cents: int = Field(ge=0)
    pending_settlement_cents: int = Field(default=0, ge=0)
    snapshot_at: datetime | None = None
    note: str | None = Field(default=None, max_length=255)

    @field_validator("snapshot_at")
    @classmethod
    def _validate_snapshot_at(cls, value: datetime | None) -> datetime | None:
        return value


class AccountOverview(BaseModel):
    """首页资金卡片数据：当前可用 / 待结算 / 未来 7 天收入 / 未来 7 天支出。"""

    opening_balance_cents: int
    pending_settlement_cents: int
    window_inflow_cents: int
    window_outflow_cents: int
    buffer_cents: int
    currency: str
    snapshot_at: datetime | None = None
    snapshot_source: str | None = None
    has_snapshot: bool = False
