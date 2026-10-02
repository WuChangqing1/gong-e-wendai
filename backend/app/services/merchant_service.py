"""商户经营档案与经营账户服务。"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import NotFound, ValidationFailed
from app.models.merchant import SOURCE_MANUAL, BusinessAccountSnapshot, MerchantProfile
from app.models.user import User
from app.repositories.merchant_repo import AccountSnapshotRepository, MerchantRepository
from app.schemas.merchant import AccountOverview, AccountSnapshotCreate, MerchantProfileUpdate
from app.services.cashflow_service import CashflowService
from app.utils.timeutil import WINDOW_DAYS, to_utc, utcnow


class MerchantService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.merchants = MerchantRepository(db)
        self.snapshots = AccountSnapshotRepository(db)

    # ------------------------------------------------------------------
    def get_by_user(self, user_id: str) -> MerchantProfile | None:
        return self.merchants.get_by_user(user_id)

    def require_by_user(self, user_id: str) -> MerchantProfile:
        profile = self.merchants.get_by_user(user_id)
        if profile is None:
            raise NotFound("尚未建立经营档案，请先完善经营信息")
        return profile

    def require_by_id(self, merchant_id: str) -> MerchantProfile:
        profile = self.merchants.get(merchant_id)
        if profile is None:
            raise NotFound("经营档案不存在")
        return profile

    # ------------------------------------------------------------------
    def ensure_profile(
        self,
        *,
        user: User,
        business_name: str,
        business_type: str = "个体工商户",
        contact_name: str | None = None,
        phone: str | None = None,
    ) -> MerchantProfile:
        existing = self.merchants.get_by_user(user.id)
        if existing is not None:
            return existing
        return self.merchants.create(
            user_id=user.id,
            business_name=business_name,
            business_type=business_type,
            contact_name=contact_name or user.display_name,
            phone_optional=phone,
        )

    # ------------------------------------------------------------------
    def update_profile(self, profile: MerchantProfile, payload: MerchantProfileUpdate) -> MerchantProfile:
        data = payload.model_dump(exclude_unset=True)
        for key, value in data.items():
            if value is None:
                continue
            if key == "business_name" and not str(value).strip():
                raise ValidationFailed("经营名称不能为空", details={"field": "business_name"})
            setattr(profile, key, value)
        self.db.commit()
        self.db.refresh(profile)
        return profile

    # ------------------------------------------------------------------
    def latest_snapshot(self, merchant_id: str) -> BusinessAccountSnapshot | None:
        return self.snapshots.latest(merchant_id)

    def list_snapshots(self, merchant_id: str, *, limit: int = 30) -> list[BusinessAccountSnapshot]:
        return self.snapshots.list_for_merchant(merchant_id, limit=limit)

    def create_snapshot(
        self,
        profile: MerchantProfile,
        payload: AccountSnapshotCreate,
        *,
        created_by: str,
        source_type: str = SOURCE_MANUAL,
    ) -> BusinessAccountSnapshot:
        snapshot_at = to_utc(payload.snapshot_at) if payload.snapshot_at else utcnow()
        if snapshot_at > utcnow():
            raise ValidationFailed(
                "资金时点不能晚于当前时间", details={"field": "snapshot_at"}
            )
        snapshot = self.snapshots.create(
            merchant_id=profile.id,
            opening_balance_cents=payload.opening_balance_cents,
            pending_settlement_cents=payload.pending_settlement_cents,
            snapshot_at=snapshot_at,
            source_type=source_type,
            created_by=created_by,
            note=payload.note,
        )
        self.db.commit()
        self.db.refresh(snapshot)
        return snapshot

    # ------------------------------------------------------------------
    def build_overview(self, profile: MerchantProfile, *, at=None) -> AccountOverview:
        """首页四张资金卡片的数据。

        待结算资金**不会**并入当前可用经营资金。
        """
        snapshot = self.latest_snapshot(profile.id)
        reference = to_utc(at) if at else (snapshot.snapshot_at if snapshot else utcnow())
        window_end = reference + timedelta(days=WINDOW_DAYS)

        totals = CashflowService(self.db).window_totals(
            profile.id, start=reference, end=window_end
        )

        opening = snapshot.opening_balance_cents if snapshot else 0
        pending = snapshot.pending_settlement_cents if snapshot else 0

        return AccountOverview(
            opening_balance_cents=opening,
            pending_settlement_cents=pending,
            window_inflow_cents=totals["inflow_cents"],
            window_outflow_cents=totals["outflow_cents"],
            buffer_cents=profile.default_buffer_amount_cents,
            currency=profile.default_currency,
            snapshot_at=snapshot.snapshot_at if snapshot else None,
            snapshot_source=snapshot.source_type if snapshot else None,
            has_snapshot=snapshot is not None,
        )

    @property
    def ai_enabled(self) -> bool:
        return settings.ai_configured
