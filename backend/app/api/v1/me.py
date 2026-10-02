"""当前用户接口。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.security import clear_auth_cookies
from app.models.user import RefreshSession, User
from app.schemas.common import MessageResponse
from app.schemas.user import ChangePasswordRequest, UpdateProfileRequest, UserMe
from app.services.auth_service import AuthService
from app.utils.timeutil import utcnow

router = APIRouter(tags=["当前用户"])


@router.get("/me", response_model=UserMe, summary="查看当前用户")
def read_me(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> UserMe:
    return AuthService(db).build_me(user)


@router.patch("/me", response_model=UserMe, summary="更新个人资料")
def update_me(
    payload: UpdateProfileRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UserMe:
    data = payload.model_dump(exclude_unset=True)
    for key, value in data.items():
        if value is not None:
            setattr(user, key, value)
    db.commit()
    db.refresh(user)
    return AuthService(db).build_me(user)


@router.post("/me/password", response_model=MessageResponse, summary="修改密码")
def change_password(
    payload: ChangePasswordRequest,
    response: Response,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MessageResponse:
    AuthService(db).change_password(
        user, current_password=payload.current_password, new_password=payload.new_password
    )
    # 修改密码后所有会话失效，需要重新登录
    clear_auth_cookies(response)
    return MessageResponse(message="密码已更新，请使用新密码重新登录", code="PASSWORD_CHANGED")


@router.get("/me/sessions", summary="查看当前有效会话数")
def list_sessions(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> dict[str, int]:
    rows = db.scalars(
        select(RefreshSession).where(
            RefreshSession.user_id == user.id,
            RefreshSession.revoked_at.is_(None),
            RefreshSession.expires_at > utcnow().replace(tzinfo=None),
        )
    ).all()
    return {"active_sessions": len(list(rows))}
