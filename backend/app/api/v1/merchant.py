"""商户经营档案与经营账户接口。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import client_ip, get_current_user, get_merchant_profile
from app.core.database import get_db
from app.models.merchant import MerchantProfile
from app.models.user import User
from app.repositories.user_repo import AuditRepository
from app.schemas.common import MessageResponse
from app.schemas.merchant import (
    AccountOverview,
    AccountSnapshotCreate,
    AccountSnapshotOut,
    MerchantProfileOut,
    MerchantProfileUpdate,
)
from app.services.merchant_service import MerchantService

router = APIRouter(tags=["经营档案"])


@router.get("/merchant/profile", response_model=MerchantProfileOut, summary="读取经营档案")
def read_profile(profile: MerchantProfile = Depends(get_merchant_profile)) -> MerchantProfileOut:
    return MerchantProfileOut.model_validate(profile)


@router.patch("/merchant/profile", response_model=MerchantProfileOut, summary="更新经营档案")
def update_profile(
    payload: MerchantProfileUpdate,
    profile: MerchantProfile = Depends(get_merchant_profile),
    db: Session = Depends(get_db),
) -> MerchantProfileOut:
    updated = MerchantService(db).update_profile(profile, payload)
    return MerchantProfileOut.model_validate(updated)


@router.get("/account/overview", response_model=AccountOverview, summary="首页资金概览")
def account_overview(
    profile: MerchantProfile = Depends(get_merchant_profile),
    db: Session = Depends(get_db),
) -> AccountOverview:
    """当前可用 / 待结算 / 未来 7 天收入 / 未来 7 天支出。

    当前可用与待结算分开返回，不做合并。
    """
    return MerchantService(db).build_overview(profile)


@router.get(
    "/account/snapshots",
    response_model=list[AccountSnapshotOut],
    summary="资金时点列表",
)
def list_snapshots(
    limit: int = Query(default=30, ge=1, le=200),
    profile: MerchantProfile = Depends(get_merchant_profile),
    db: Session = Depends(get_db),
) -> list[AccountSnapshotOut]:
    rows = MerchantService(db).list_snapshots(profile.id, limit=limit)
    return [AccountSnapshotOut.model_validate(item) for item in rows]


@router.post(
    "/account/snapshots",
    response_model=AccountSnapshotOut,
    status_code=201,
    summary="登记当前经营资金",
)
def create_snapshot(
    payload: AccountSnapshotCreate,
    request: Request,
    profile: MerchantProfile = Depends(get_merchant_profile),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AccountSnapshotOut:
    service = MerchantService(db)
    snapshot = service.create_snapshot(profile, payload, created_by=user.id)
    AuditRepository(db).log(
        action="account.snapshot_created",
        actor_id=user.id,
        actor_name=user.display_name,
        resource_type="business_account_snapshot",
        resource_id=snapshot.id,
        merchant_id=profile.id,
        metadata={"opening_balance_cents": snapshot.opening_balance_cents},
        ip_address=client_ip(request),
    )
    db.commit()
    return AccountSnapshotOut.model_validate(snapshot)


@router.get(
    "/account/snapshots/latest",
    response_model=AccountSnapshotOut | None,
    summary="最近一次资金时点",
)
def latest_snapshot(
    profile: MerchantProfile = Depends(get_merchant_profile),
    db: Session = Depends(get_db),
) -> AccountSnapshotOut | None:
    row = MerchantService(db).latest_snapshot(profile.id)
    return AccountSnapshotOut.model_validate(row) if row else None


@router.delete(
    "/account/snapshots/latest",
    response_model=MessageResponse,
    summary="不提供历史删除（保留登记记录）",
    include_in_schema=False,
)
def _no_delete() -> MessageResponse:  # pragma: no cover
    return MessageResponse(message="资金时点记录不支持删除", code="NOT_SUPPORTED")
