"""经营咨询接口。

商户侧：创建咨询、提交、查看结果、根据结果更正事项。
咨询人员侧：受理、要求补充、填写核实结果、完成事项。

权限边界由后端强制：咨询人员不能读取家庭数据、经营余额、可提用金额与留底金额，
也不能直接修改商户的收付款事项。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import (
    client_ip,
    get_current_user,
    get_merchant_profile,
)
from app.core.database import get_db
from app.models.consultation import CASE_STATUSES
from app.models.merchant import MerchantProfile
from app.models.user import ROLE_CONSULTANT, ROLE_MERCHANT, User
from app.core.errors import Forbidden
from app.repositories.user_repo import AuditService
from app.schemas.common import Page, PageMeta
from app.schemas.consultation import (
    AllowedFieldsOut,
    ApplyUpdateOut,
    CloseIn,
    ConsultationCreate,
    ConsultationOut,
    RequestInfoIn,
    VerifyIn,
)
from app.services.consultation_service import ConsultationService

router = APIRouter(prefix="/consultations", tags=["经营咨询"])


def _require_merchant_user(user: User) -> None:
    if not user.has_role(ROLE_MERCHANT):
        raise Forbidden("只有经营主体账户可以发起经营咨询")


def _require_consultant_user(user: User) -> None:
    if not user.has_role(ROLE_CONSULTANT):
        raise Forbidden("只有咨询人员可以执行该操作")


def _page(rows: list, total: int, page: int, page_size: int) -> PageMeta:
    total_pages = (total + page_size - 1) // page_size if total else 0
    return PageMeta(page=page, page_size=page_size, total=total, total_pages=total_pages)


# ---------------------------------------------------------------------------
# 元数据
# ---------------------------------------------------------------------------
@router.get("/allowed-fields", response_model=AllowedFieldsOut, summary="咨询字段白名单预览")
def allowed_fields(
    cash_event_id: str = Query(...),
    profile: MerchantProfile = Depends(get_merchant_profile),
    db: Session = Depends(get_db),
) -> AllowedFieldsOut:
    from app.models.cash import CashEvent

    event = db.get(CashEvent, cash_event_id)
    if event is None or event.merchant_id != profile.id:
        from app.core.errors import NotFound

        raise NotFound("收付款事项不存在")
    payload = ConsultationService.allowed_fields_preview(event)
    return AllowedFieldsOut.model_validate(payload)


@router.get("/meta", summary="咨询状态与类型")
def meta(user: User = Depends(get_current_user)) -> dict:
    from app.models.consultation import (
        CASE_STATUS_LABELS,
        QUESTION_TYPE_LABELS,
    )

    _ = user
    return {
        "statuses": [
            {"value": item, "label": CASE_STATUS_LABELS[item]} for item in CASE_STATUSES
        ],
        "question_types": [
            {"value": key, "label": value} for key, value in QUESTION_TYPE_LABELS.items()
        ],
    }


# ---------------------------------------------------------------------------
# 咨询人员工作台（放在 /{case_id} 之前，避免路由冲突）
# ---------------------------------------------------------------------------
@router.get("/queue", response_model=Page[ConsultationOut], summary="咨询队列")
def queue(
    bucket: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Page[ConsultationOut]:
    _require_consultant_user(user)
    if bucket and bucket not in CASE_STATUSES:
        bucket = None
    service = ConsultationService(db)
    rows, total = service.queue(bucket=bucket, page=page, page_size=page_size, consultant=user)
    return Page[ConsultationOut](
        items=[service.to_out(item) for item in rows],
        meta=_page(rows, total, page, page_size),
    )


# ---------------------------------------------------------------------------
# 商户侧
# ---------------------------------------------------------------------------
@router.get("", response_model=Page[ConsultationOut], summary="我的咨询列表")
def list_consultations(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
    status: str | None = Query(default=None),
    profile: MerchantProfile = Depends(get_merchant_profile),
    db: Session = Depends(get_db),
) -> Page[ConsultationOut]:
    if status and status not in CASE_STATUSES:
        status = None
    service = ConsultationService(db)
    rows, total = service.list_for_merchant(profile.id, page, page_size, status)
    return Page[ConsultationOut](
        items=[service.to_out(item) for item in rows],
        meta=_page(rows, total, page, page_size),
    )


@router.post("", response_model=ConsultationOut, status_code=201, summary="发起经营咨询")
def create_consultation(
    payload: ConsultationCreate,
    request: Request,
    profile: MerchantProfile = Depends(get_merchant_profile),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ConsultationOut:
    _require_merchant_user(user)
    service = ConsultationService(db)
    case = service.create_case(
        profile,
        user,
        cash_event_id=payload.cash_event_id,
        question_type=payload.question_type,
        question=payload.question,
        ai_draft=payload.ai_draft,
        status=payload.status,
    )
    AuditService(db).record(
        "consultation.created",
        actor=user,
        resource_type="consultation_case",
        resource_id=case.id,
        merchant_id=profile.id,
        metadata={
            "case_no": case.case_no,
            "question_type": case.question_type,
            "status": case.status,
        },
        ip_address=client_ip(request),
    )
    db.commit()
    return service.to_out(case)


@router.get("/{case_id}", response_model=ConsultationOut, summary="咨询详情")
def get_consultation(
    case_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ConsultationOut:
    service = ConsultationService(db)
    if user.has_role(ROLE_CONSULTANT):
        case = service.require_case_for_consultant(case_id, user)
    else:
        _require_merchant_user(user)
        profile = db.scalar(select(MerchantProfile).where(MerchantProfile.user_id == user.id))
        if profile is None:
            raise Forbidden("尚未建立经营档案")
        case = service.require_case_for_merchant(case_id, profile.id)
    return service.to_out(case)


@router.post("/{case_id}/submit", response_model=ConsultationOut, summary="提交咨询")
def submit_consultation(
    case_id: str,
    profile: MerchantProfile = Depends(get_merchant_profile),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ConsultationOut:
    _require_merchant_user(user)
    service = ConsultationService(db)
    case = service.require_case_for_merchant(case_id, profile.id)
    updated = service.submit(case, user)
    AuditService(db).record(
        "consultation.submitted",
        actor=user,
        resource_type="consultation_case",
        resource_id=case.id,
        merchant_id=profile.id,
        metadata={"case_no": case.case_no},
        ip_address=client_ip(request),
    )
    db.commit()
    return service.to_out(updated)


@router.post(
    "/{case_id}/apply-update",
    response_model=ApplyUpdateOut,
    summary="根据咨询结果更正事项",
)
def apply_update(
    case_id: str,
    request: Request,
    profile: MerchantProfile = Depends(get_merchant_profile),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApplyUpdateOut:
    """更正必须由商户确认；系统会保存旧版本并触发重新计算。"""
    _require_merchant_user(user)
    service = ConsultationService(db)
    case = service.require_case_for_merchant(case_id, profile.id)
    event, changed = service.apply_resolution_to_event(case, user)
    AuditService(db).record(
        "consultation.applied_to_event",
        actor=user,
        resource_type="cash_event",
        resource_id=event.id,
        merchant_id=profile.id,
        metadata={
            "case_no": case.case_no,
            "new_version": event.current_version,
            "changed_fields": changed,
        },
        ip_address=client_ip(request),
    )
    db.commit()
    return ApplyUpdateOut(
        cash_event_id=event.id, new_version=event.current_version, changed_fields=changed
    )


# ---------------------------------------------------------------------------
# 咨询人员动作
# ---------------------------------------------------------------------------
@router.post("/{case_id}/start", response_model=ConsultationOut, summary="受理并开始处理")
def start_review(
    case_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ConsultationOut:
    service = ConsultationService(db)
    case = service.require_case_for_consultant(case_id, user)
    updated = service.start_review(case, user)
    AuditService(db).record(
        "consultation.status_changed",
        actor=user,
        resource_type="consultation_case",
        resource_id=case.id,
        merchant_id=case.merchant_id,
        metadata={"to": "under_review"},
        ip_address=client_ip(request),
    )
    db.commit()
    return service.to_out(updated)


@router.post(
    "/{case_id}/request-info", response_model=ConsultationOut, summary="要求补充资料"
)
def request_info(
    case_id: str,
    payload: RequestInfoIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ConsultationOut:
    service = ConsultationService(db)
    case = service.require_case_for_consultant(case_id, user)
    updated = service.request_information(case, user, payload.content)
    AuditService(db).record(
        "consultation.status_changed",
        actor=user,
        resource_type="consultation_case",
        resource_id=case.id,
        merchant_id=case.merchant_id,
        metadata={"to": "need_more_information"},
        ip_address=client_ip(request),
    )
    db.commit()
    return service.to_out(updated)


@router.post("/{case_id}/verify", response_model=ConsultationOut, summary="填写核实结果")
def verify(
    case_id: str,
    payload: VerifyIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ConsultationOut:
    service = ConsultationService(db)
    case = service.require_case_for_consultant(case_id, user)
    updated = service.verify(
        case,
        user,
        resolution_summary=payload.resolution_summary,
        resolution_fields=payload.resolution_fields,
    )
    AuditService(db).record(
        "consultation.status_changed",
        actor=user,
        resource_type="consultation_case",
        resource_id=case.id,
        merchant_id=case.merchant_id,
        metadata={"to": "verified"},
        ip_address=client_ip(request),
    )
    db.commit()
    return service.to_out(updated)


@router.post("/{case_id}/close", response_model=ConsultationOut, summary="完成事项")
def close_case(
    case_id: str,
    payload: CloseIn | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ConsultationOut:
    service = ConsultationService(db)
    if user.has_role(ROLE_CONSULTANT):
        case = service.require_case_for_consultant(case_id, user)
    else:
        _require_merchant_user(user)
        from sqlalchemy import select

        profile = db.scalar(select(MerchantProfile).where(MerchantProfile.user_id == user.id))
        if profile is None:
            raise Forbidden("尚未建立经营档案")
        case = service.require_case_for_merchant(case_id, profile.id)
    updated = service.close(case, user, payload.resolution_summary if payload else None)
    return service.to_out(updated)
