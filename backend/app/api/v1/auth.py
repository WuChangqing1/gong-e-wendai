"""认证接口：注册 / 登录 / 刷新 / 退出。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import client_ip, get_current_user, refresh_token_from_request, user_agent
from app.core.database import get_db
from app.core.security import clear_auth_cookies, set_auth_cookies
from app.models.user import User
from app.schemas.common import MessageResponse
from app.schemas.user import LoginRequest, RegisterRequest, TokenResponse, UserPublic
from app.services.auth_service import AuthService, access_token_ttl_seconds

router = APIRouter(prefix="/auth", tags=["认证"])


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=201,
    summary="注册账户",
)
def register(
    payload: RegisterRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> TokenResponse:
    service = AuthService(db)
    user, access, refresh = service.register(
        payload, ip_address=client_ip(request), user_agent=user_agent(request)
    )
    set_auth_cookies(response, access, refresh)
    return TokenResponse(
        access_token=access,
        expires_in=access_token_ttl_seconds(),
        user=UserPublic(
            id=user.id,
            username=user.username,
            display_name=user.display_name,
            status=user.status,
            roles=user.role_names(),
            created_at=user.created_at,
            last_login_at=user.last_login_at,
        ),
    )


@router.post("/login", response_model=TokenResponse, summary="登录")
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> TokenResponse:
    service = AuthService(db)
    user, access, refresh = service.login(
        payload, ip_address=client_ip(request), user_agent=user_agent(request)
    )
    set_auth_cookies(response, access, refresh)
    return TokenResponse(
        access_token=access,
        expires_in=access_token_ttl_seconds(),
        user=UserPublic(
            id=user.id,
            username=user.username,
            display_name=user.display_name,
            status=user.status,
            roles=user.role_names(),
            created_at=user.created_at,
            last_login_at=user.last_login_at,
        ),
    )


@router.post("/refresh", response_model=TokenResponse, summary="刷新会话")
def refresh(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> TokenResponse:
    service = AuthService(db)
    user, access, new_refresh = service.refresh(
        refresh_token_from_request(request), user_agent=user_agent(request)
    )
    set_auth_cookies(response, access, new_refresh)
    return TokenResponse(
        access_token=access,
        expires_in=access_token_ttl_seconds(),
        user=UserPublic(
            id=user.id,
            username=user.username,
            display_name=user.display_name,
            status=user.status,
            roles=user.role_names(),
            created_at=user.created_at,
            last_login_at=user.last_login_at,
        ),
    )


@router.post("/logout", response_model=MessageResponse, summary="退出登录")
def logout(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> MessageResponse:
    from app.api.deps import get_optional_user

    actor: User | None = get_optional_user(request, db)
    AuthService(db).logout(refresh_token_from_request(request), actor)
    clear_auth_cookies(response)
    return MessageResponse(message="已退出登录", code="LOGGED_OUT")
