"""现金事件（收付款事项）接口。"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import client_ip, get_current_user, get_merchant_profile
from app.core.database import get_db
from app.models.merchant import MerchantProfile
from app.models.user import User
from app.repositories.base import paginate
from app.repositories.cash_event_repo import CashEventRepository, RevisionRepository
from app.repositories.user_repo import AuditService
from app.schemas.cash_event import (
    CancelRequest,
    CashEventCreate,
    CashEventDetail,
    CashEventOut,
    CashEventRevisionOut,
    CashEventStats,
    CashEventUpdate,
    RevisionDiff,
    RevisionWithEvent,
)
from app.schemas.common import MessageResponse, Page
from app.services.event_service import (
    ACTION_EVENT_CANCELLED,
    ACTION_EVENT_CREATED,
    ACTION_EVENT_UPDATED,
    CashEventService,
)

router = APIRouter(prefix="/cash-events", tags=["收付款事项"])


@router.get("", response_model=Page[CashEventOut], summary="事项列表")
def list_events(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    search: str | None = Query(default=None, max_length=128),
    direction: str | None = Query(default=None, pattern="^(inflow|outflow)$"),
    state: str | None = Query(default=None, max_length=24),
    event_type: str | None = Query(default=None, max_length=32),
    source_type: str | None = Query(default=None, max_length=32),
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    profile: MerchantProfile = Depends(get_merchant_profile),
    db: Session = Depends(get_db),
) -> Page[CashEventOut]:
    repository = CashEventRepository(db)
    statement = repository.build_query(
        profile.id,
        search=search,
        direction=direction,
        state=state,
        event_type=event_type,
        source_type=source_type,
        start=start,
        end=end,
    )
    rows, meta = paginate(db, statement, page=page, page_size=page_size)
    service = CashEventService(db)
    return Page[CashEventOut](
        items=[service.build_out(item) for item in rows], meta=meta
    )


@router.get("/stats", response_model=CashEventStats, summary="事项统计")
def event_stats(
    profile: MerchantProfile = Depends(get_merchant_profile),
    db: Session = Depends(get_db),
) -> CashEventStats:
    from sqlalchemy import func, select

    from app.models.cash import CashEvent as Model

    rows = db.execute(
        select(Model.state, Model.direction, func.coalesce(func.sum(Model.amount_cents), 0), func.count(Model.id))
        .where(Model.merchant_id == profile.id)
        .group_by(Model.state, Model.direction)
    ).all()

    stats = CashEventStats(
        total=0, scheduled=0, included_in_opening=0, cancelled=0, inflow_cents=0, outflow_cents=0
    )
    for state, direction, total_cents, count in rows:
        stats.total += int(count)
        if state == "scheduled":
            stats.scheduled += int(count)
            if direction == "inflow":
                stats.inflow_cents += int(total_cents or 0)
            else:
                stats.outflow_cents += int(total_cents or 0)
        elif state == "included_in_opening":
            stats.included_in_opening += int(count)
        elif state == "cancelled":
            stats.cancelled += int(count)
    return stats


@router.get("/revisions", response_model=list[RevisionWithEvent], summary="全部修改记录")
def list_all_revisions(
    limit: int = Query(default=50, ge=1, le=200),
    profile: MerchantProfile = Depends(get_merchant_profile),
    db: Session = Depends(get_db),
) -> list[RevisionWithEvent]:
    rows = RevisionRepository(db).list_for_merchant(profile.id, limit=limit)
    return [
        RevisionWithEvent(
            revision=CashEventRevisionOut.model_validate(revision),
            event_id=event.id,
            event_title=event.title,
            cash_key=event.cash_key,
        )
        for revision, event in rows
    ]


@router.get("/{event_id}", response_model=CashEventDetail, summary="事项详情")
def read_event(
    event_id: str,
    profile: MerchantProfile = Depends(get_merchant_profile),
    db: Session = Depends(get_db),
) -> CashEventDetail:
    service = CashEventService(db)
    event = service.require_event(event_id, profile.id)
    return service.build_detail(event)


@router.post("", response_model=CashEventDetail, status_code=201, summary="新增事项")
def create_event(
    payload: CashEventCreate,
    request: Request,
    profile: MerchantProfile = Depends(get_merchant_profile),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CashEventDetail:
    service = CashEventService(db)
    event = service.create_event(profile, payload, actor=user)
    AuditService(db).record(
        ACTION_EVENT_CREATED,
        actor=user,
        resource_type="cash_event",
        resource_id=event.id,
        merchant_id=profile.id,
        metadata={
            "cash_key": event.cash_key,
            "direction": event.direction,
            "amount_cents": event.amount_cents,
            "scheduled_at": event.scheduled_at.isoformat(),
        },
        ip_address=client_ip(request),
    )
    db.commit()
    db.refresh(event)
    return service.build_detail(event)


@router.patch("/{event_id}", response_model=CashEventDetail, summary="修改事项")
def update_event(
    event_id: str,
    payload: CashEventUpdate,
    request: Request,
    profile: MerchantProfile = Depends(get_merchant_profile),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CashEventDetail:
    service = CashEventService(db)
    event = service.require_event(event_id, profile.id)
    updated, revision = service.update_event(event, payload, actor=user)
    AuditService(db).record(
        ACTION_EVENT_UPDATED,
        actor=user,
        resource_type="cash_event",
        resource_id=updated.id,
        merchant_id=profile.id,
        metadata={
            "cash_key": updated.cash_key,
            "version": updated.current_version,
            "changed_fields": list(revision.changed_fields) if revision else [],
            "material": bool(revision.material) if revision else False,
        },
        ip_address=client_ip(request),
    )
    db.commit()
    db.refresh(updated)
    return service.build_detail(updated)


@router.post("/{event_id}/cancel", response_model=CashEventDetail, summary="取消事项")
def cancel_event(
    event_id: str,
    request: Request,
    payload: CancelRequest | None = None,
    profile: MerchantProfile = Depends(get_merchant_profile),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CashEventDetail:
    """取消事项：保留全部历史，仅把状态置为 cancelled，绝不物理删除。"""
    service = CashEventService(db)
    event = service.require_event(event_id, profile.id)
    reason = payload.reason if payload else None
    updated = service.cancel_event(event, actor=user, reason=reason)
    AuditService(db).record(
        ACTION_EVENT_CANCELLED,
        actor=user,
        resource_type="cash_event",
        resource_id=updated.id,
        merchant_id=profile.id,
        metadata={"cash_key": updated.cash_key, "reason": reason},
        ip_address=client_ip(request),
    )
    db.commit()
    db.refresh(updated)
    return service.build_detail(updated)


@router.get(
    "/{event_id}/revisions",
    response_model=list[RevisionDiff],
    summary="事项版本历史与对比",
)
def list_revisions(
    event_id: str,
    profile: MerchantProfile = Depends(get_merchant_profile),
    db: Session = Depends(get_db),
) -> list[RevisionDiff]:
    service = CashEventService(db)
    event = service.require_event(event_id, profile.id)
    return service.build_revision_diffs(event)


@router.get(
    "/{event_id}/source",
    response_model=CashEventDetail,
    summary="来源抽屉数据",
)
def read_source(
    event_id: str,
    profile: MerchantProfile = Depends(get_merchant_profile),
    db: Session = Depends(get_db),
) -> CashEventDetail:
    """结果 -> 限制事件 -> CashEvent -> SourceRecord 完整可追踪。"""
    service = CashEventService(db)
    event = service.require_event(event_id, profile.id)
    return service.build_detail(event)


@router.delete(
    "/{event_id}",
    response_model=MessageResponse,
    summary="不提供物理删除",
    include_in_schema=False,
)
def no_hard_delete(
    event_id: str,
    profile: MerchantProfile = Depends(get_merchant_profile),
) -> MessageResponse:  # pragma: no cover - 明确拒绝
    return MessageResponse(
        message="历史业务记录不支持删除，请使用「取消事项」（状态置为已取消）",
        code="HARD_DELETE_NOT_SUPPORTED",
    )


__all__ = ["CashEvent", "PageMeta", "router"]
