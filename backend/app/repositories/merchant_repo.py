"""商户档案与经营账户快照的数据访问。"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.merchant import BusinessAccountSnapshot, MerchantProfile


class MerchantRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get(self, merchant_id: str) -> MerchantProfile | None:
        return self.db.get(MerchantProfile, merchant_id)

    def get_by_user(self, user_id: str) -> MerchantProfile | None:
        return self.db.scalar(
            select(MerchantProfile).where(MerchantProfile.user_id == user_id)
        )

    def create(
        self,
        *,
        user_id: str,
        business_name: str,
        business_type: str = "个体工商户",
        contact_name: str | None = None,
        phone_optional: str | None = None,
        default_buffer_amount_cents: int = 0,
    ) -> MerchantProfile:
        profile = MerchantProfile(
            user_id=user_id,
            business_name=business_name.strip(),
            business_type=business_type or "个体工商户",
            contact_name=contact_name,
            phone_optional=phone_optional,
            default_buffer_amount_cents=default_buffer_amount_cents,
        )
        self.db.add(profile)
        self.db.flush()
        return profile


class AccountSnapshotRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def latest(self, merchant_id: str) -> BusinessAccountSnapshot | None:
        return self.db.scalar(
            select(BusinessAccountSnapshot)
            .where(BusinessAccountSnapshot.merchant_id == merchant_id)
            .order_by(BusinessAccountSnapshot.snapshot_at.desc())
            .limit(1)
        )

    def list_for_merchant(
        self, merchant_id: str, *, limit: int = 30
    ) -> list[BusinessAccountSnapshot]:
        return list(
            self.db.scalars(
                select(BusinessAccountSnapshot)
                .where(BusinessAccountSnapshot.merchant_id == merchant_id)
                .order_by(BusinessAccountSnapshot.snapshot_at.desc())
                .limit(limit)
            ).all()
        )

    def create(
        self,
        *,
        merchant_id: str,
        opening_balance_cents: int,
        pending_settlement_cents: int,
        snapshot_at,
        source_type: str,
        created_by: str | None,
        note: str | None = None,
    ) -> BusinessAccountSnapshot:
        snapshot = BusinessAccountSnapshot(
            merchant_id=merchant_id,
            opening_balance_cents=int(opening_balance_cents),
            pending_settlement_cents=int(pending_settlement_cents),
            snapshot_at=snapshot_at,
            source_type=source_type,
            created_by=created_by,
            note=note,
        )
        self.db.add(snapshot)
        self.db.flush()
        return snapshot
