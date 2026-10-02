"""家庭协同接口。

商户侧：创建家庭、轮换邀请码、确认成员、分享协同卡片。
家庭成员侧：查看分享给自己的卡片、标记已读、同意 / 需要商量、评论。

隐私边界由后端强制校验：家庭成员无法访问收付款事项、经营账户与分析接口。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import (
    client_ip,
    get_current_user,
    get_merchant_profile,
)
from app.core.database import get_db
from app.core.errors import Forbidden, NotFound
from app.models.household import CARD_TYPES
from app.models.merchant import MerchantProfile
from app.models.user import ROLE_MERCHANT, User
from app.repositories.user_repo import AuditService
from app.schemas.common import MessageResponse
from app.schemas.household import (
    CardCreate,
    CardOut,
    CardPreviewOut,
    CardPreviewRequest,
    CardUpdate,
    CommentIn,
    HouseholdCreate,
    HouseholdJoin,
    HouseholdOut,
    InviteCodeOut,
    MemberOut,
    MyMembershipOut,
    ReactionIn,
)
from app.services.analysis_service import AnalysisService
from app.services.household_service import SHAREABLE_FIELDS, SENSITIVE_FIELDS, HouseholdService

router = APIRouter(tags=["家庭协同"])


def _require_merchant(user: User, db: Session) -> MerchantProfile:
    if not user.has_role(ROLE_MERCHANT):
        raise Forbidden("只有经营主体账户可以管理家庭")
    from app.services.merchant_service import MerchantService

    return MerchantService(db).require_by_user(user.id)


# ---------------------------------------------------------------------------
# 家庭
# ---------------------------------------------------------------------------
@router.get("/households/current", response_model=HouseholdOut | None, summary="我的家庭")
def current_household(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> HouseholdOut | None:
    if not user.has_role(ROLE_MERCHANT):
        return None
    profile = _require_merchant(user, db)
    service = HouseholdService(db)
    household = service.get_for_owner(profile.id)
    return service.to_out(household, viewer=user) if household else None


@router.post("/households", response_model=HouseholdOut, status_code=201, summary="创建家庭")
def create_household(
    payload: HouseholdCreate,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> HouseholdOut:
    profile = _require_merchant(user, db)
    service = HouseholdService(db)
    result = service.create_household(profile, user, payload.name)
    AuditService(db).record(
        "household.created",
        actor=user,
        resource_type="household",
        resource_id=result.id,
        merchant_id=profile.id,
        metadata={"name": result.name},
        ip_address=client_ip(request),
    )
    db.commit()
    return result


@router.post("/households/invite-code/rotate", response_model=InviteCodeOut, summary="轮换邀请码")
def rotate_invite_code(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> InviteCodeOut:
    profile = _require_merchant(user, db)
    service = HouseholdService(db)
    household = service.require_for_owner(profile.id)
    code = service.rotate_invite_code(household)
    AuditService(db).record(
        "household.invite_rotated",
        actor=user,
        resource_type="household",
        resource_id=household.id,
        merchant_id=profile.id,
        metadata={},
        ip_address=client_ip(request),
    )
    db.commit()
    return InviteCodeOut(invite_code=code)


@router.post("/households/join", summary="使用邀请码申请加入")
def join_household(
    payload: HouseholdJoin,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    service = HouseholdService(db)
    household, membership = service.join_by_invite(
        user, payload.invite_code, payload.relation_label
    )
    AuditService(db).record(
        "household.join_requested",
        actor=user,
        resource_type="household_membership",
        resource_id=membership.id,
        merchant_id=household.merchant_id,
        metadata={"household": household.name},
    )
    db.commit()
    return {
        "membership_id": membership.id,
        "status": membership.status,
        "household_name": household.name,
    }


@router.get("/households/members", response_model=list[MemberOut], summary="成员列表")
def list_members(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[MemberOut]:
    profile = _require_merchant(user, db)
    service = HouseholdService(db)
    household = service.require_for_owner(profile.id)
    return service.to_out(household, viewer=user).members


@router.post(
    "/households/members/{membership_id}/approve",
    response_model=MessageResponse,
    summary="确认成员加入",
)
def approve_member(
    membership_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MessageResponse:
    profile = _require_merchant(user, db)
    service = HouseholdService(db)
    household = service.require_for_owner(profile.id)
    membership = service.approve_member(household, membership_id, user)
    AuditService(db).record(
        "household.member_approved",
        actor=user,
        resource_type="household_membership",
        resource_id=membership.id,
        merchant_id=profile.id,
        metadata={},
        ip_address=client_ip(request),
    )
    db.commit()
    return MessageResponse(message="已通过加入申请", code="MEMBER_APPROVED")


@router.post(
    "/households/members/{membership_id}/remove",
    response_model=MessageResponse,
    summary="移除成员",
)
def remove_member(
    membership_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MessageResponse:
    profile = _require_merchant(user, db)
    service = HouseholdService(db)
    household = service.require_for_owner(profile.id)
    membership = service.remove_member(household, membership_id, user)
    AuditService(db).record(
        "household.member_removed",
        actor=user,
        resource_type="household_membership",
        resource_id=membership.id,
        merchant_id=profile.id,
        metadata={},
        ip_address=client_ip(request),
    )
    db.commit()
    return MessageResponse(message="成员已移除", code="MEMBER_REMOVED")


@router.get(
    "/households/memberships/mine",
    response_model=list[MyMembershipOut],
    summary="我加入的家庭",
)
def my_memberships(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[MyMembershipOut]:
    service = HouseholdService(db)
    return [
        MyMembershipOut(
            household_id=household.id, household_name=household.name, status=membership.status
        )
        for membership, household in service.memberships_for_user(user.id)
    ]


@router.get("/households/shareable-fields", summary="可分享字段白名单")
def shareable_fields(user: User = Depends(get_current_user)) -> dict:
    _ = user
    return {
        "shareable": list(SHAREABLE_FIELDS),
        "sensitive_default_off": list(SENSITIVE_FIELDS),
    }


# ---------------------------------------------------------------------------
# 协同卡
# ---------------------------------------------------------------------------
@router.post(
    "/household-cards/preview",
    response_model=CardPreviewOut,
    summary="分享预览（确认前的所见即所得）",
)
def preview_card(
    payload: CardPreviewRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CardPreviewOut:
    profile = _require_merchant(user, db)
    title, summary, body, fields = HouseholdService(db).build_preview(
        profile,
        card_type=payload.card_type,
        shared_fields=payload.shared_fields,
        analysis_result_id=payload.analysis_result_id,
        cash_event_id=payload.cash_event_id,
        planned_amount_cents=payload.planned_household_amount_cents,
    )
    return CardPreviewOut(title=title, summary=summary, payload=body, shared_fields=fields)


@router.post(
    "/household-cards", response_model=CardOut, status_code=201, summary="分享给家庭"
)
def create_card(
    payload: CardCreate,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CardOut:
    profile = _require_merchant(user, db)
    service = HouseholdService(db)
    household = service.require_for_owner(profile.id)
    card = service.create_card(
        profile,
        household,
        user,
        card_type=payload.card_type,
        shared_fields=payload.shared_fields,
        title=payload.title,
        summary=payload.summary,
        analysis_result_id=payload.analysis_result_id,
        cash_event_id=payload.cash_event_id,
        planned_amount_cents=payload.planned_household_amount_cents,
    )
    AuditService(db).record(
        "household.card_shared",
        actor=user,
        resource_type="household_card",
        resource_id=card.id,
        merchant_id=profile.id,
        metadata={"card_type": card.card_type, "shared_fields": list(card.shared_fields or [])},
        ip_address=client_ip(request),
    )
    db.commit()
    return service.card_to_out(card, viewer=user)


@router.get("/household-cards", response_model=list[CardOut], summary="协同卡列表")
def list_cards(
    card_type: str | None = Query(default=None),
    unread_only: bool = Query(default=False),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[CardOut]:
    if card_type and card_type not in CARD_TYPES:
        card_type = None
    service = HouseholdService(db)

    if user.has_role(ROLE_MERCHANT):
        profile = _require_merchant(user, db)
        cards = service.list_cards_for_owner(profile.id, card_type=card_type)
    else:
        # 家庭成员只看到分享给自己的卡片；管理员不参与家庭协同
        cards = service.list_cards_for_recipient(
            user.id, card_type=card_type, unread_only=unread_only
        )
    return [service.card_to_out(card, viewer=user) for card in cards]


@router.get("/household-cards/revision-notices", response_model=list[CardOut], summary="更正通知")
def revision_notices(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[CardOut]:
    service = HouseholdService(db)
    if user.has_role(ROLE_MERCHANT):
        profile = _require_merchant(user, db)
        cards = service.list_cards_for_owner(profile.id, card_type="revision")
    else:
        cards = service.list_cards_for_recipient(user.id, card_type="revision")
    return [service.card_to_out(card, viewer=user) for card in cards]


@router.get("/household-cards/{card_id}", response_model=CardOut, summary="卡片详情")
def get_card(
    card_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CardOut:
    service = HouseholdService(db)
    if user.has_role(ROLE_MERCHANT):
        profile = _require_merchant(user, db)
        card = service.require_card_for_owner(card_id, profile.id)
        return service.card_to_out(card, viewer=user)
    card, _recipient = service.require_card_for_recipient(card_id, user.id)
    return service.card_to_out(card, viewer=user)


@router.patch("/household-cards/{card_id}", response_model=CardOut, summary="更新计划提用金额")
def update_card(
    card_id: str,
    payload: CardUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CardOut:
    profile = _require_merchant(user, db)
    service = HouseholdService(db)
    card = service.require_card_for_owner(card_id, profile.id)
    if payload.planned_household_amount_cents is not None:
        card.planned_household_amount_cents = payload.planned_household_amount_cents
        card.payload = {
            **(card.payload or {}),
            "planned_household_amount_cents": payload.planned_household_amount_cents,
        }
        db.commit()
        db.refresh(card)
    return service.card_to_out(card, viewer=user)


@router.delete("/household-cards/{card_id}", response_model=MessageResponse, summary="撤回卡片")
def delete_card(
    card_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MessageResponse:
    profile = _require_merchant(user, db)
    service = HouseholdService(db)
    card = service.require_card_for_owner(card_id, profile.id)
    service.delete_card(card)
    return MessageResponse(message="卡片已撤回", code="CARD_DELETED")


@router.post("/household-cards/{card_id}/read", response_model=CardOut, summary="标记已读")
def mark_read(
    card_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CardOut:
    service = HouseholdService(db)
    card, recipient = service.require_card_for_recipient(card_id, user.id)
    updated = service.mark_read(card, recipient)
    return service.card_to_out(updated, viewer=user)


@router.post("/household-cards/{card_id}/react", response_model=CardOut, summary="同意 / 需要商量")
def react(
    card_id: str,
    payload: ReactionIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CardOut:
    service = HouseholdService(db)
    card, recipient = service.require_card_for_recipient(card_id, user.id)
    updated = service.react(card, recipient, payload.reaction)
    return service.card_to_out(updated, viewer=user)


@router.post(
    "/household-cards/{card_id}/comments", response_model=CardOut, summary="发表评论"
)
def add_comment(
    card_id: str,
    payload: CommentIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CardOut:
    service = HouseholdService(db)

    if user.has_role(ROLE_MERCHANT):
        profile = _require_merchant(user, db)
        card = service.require_card_for_owner(card_id, profile.id)
    else:
        card, _recipient = service.require_card_for_recipient(card_id, user.id)
    service.add_comment(card, user, payload.content)
    db.refresh(card)
    return service.card_to_out(card, viewer=user)


# ---------------------------------------------------------------------------
# 明确阻止家庭成员访问经营数据的路由（返回 403，便于前端提示）
# ---------------------------------------------------------------------------
@router.get("/households/merchant-data", include_in_schema=False)
def merchant_data_forbidden(user: User = Depends(get_current_user)) -> None:
    raise Forbidden("家庭成员无法查看经营流水与账户余额")


def analysis_or_404(db: Session, merchant_id: str):
    result = AnalysisService(db).latest_result(merchant_id)
    if result is None:
        raise NotFound("尚未生成分析结果")
    return result
