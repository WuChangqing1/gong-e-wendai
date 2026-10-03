"""FastAPI 依赖：认证、角色与商户上下文。

所有资源访问都必须经过这里的校验，绝不依赖前端隐藏按钮。
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.errors import Forbidden, Unauthenticated
from app.core.security import ACCESS_COOKIE_NAME, REFRESH_COOKIE_NAME, decode_token
from app.models.merchant import MerchantProfile
from app.models.user import (
    ROLE_CONSULTANT,
    ROLE_FAMILY_MEMBER,
    ROLE_MERCHANT,
    User,
)
from app.repositories.user_repo import UserRepository
from app.services.merchant_service import MerchantService


def client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    return request.client.host if request.client else None


def user_agent(request: Request) -> str | None:
    value = request.headers.get("user-agent")
    return value[:255] if value else None


def _bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization")
    if not header:
        return None
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def _access_token(request: Request) -> str | None:
    return _bearer_token(request) or request.cookies.get(ACCESS_COOKIE_NAME)


def refresh_token_from_request(request: Request) -> str | None:
    return request.cookies.get(REFRESH_COOKIE_NAME)


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = _access_token(request)
    if not token:
        raise Unauthenticated("请先登录")
    payload = decode_token(token, expected_type="access")
    user = UserRepository(db).get(str(payload.get("sub")))
    if user is None:
        raise Unauthenticated("登录状态已失效，请重新登录")
    if user.status != "active":
        raise Forbidden("账户已被停用，请联系管理员")
    return user


def get_optional_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    token = _access_token(request)
    if not token:
        return None
    try:
        payload = decode_token(token, expected_type="access")
    except Unauthenticated:
        return None
    user = UserRepository(db).get(str(payload.get("sub")))
    if user is None or user.status != "active":
        return None
    return user


def require_roles(*roles: str) -> Callable[..., User]:
    """生成一个要求指定角色之一的依赖。"""

    def _dependency(user: User = Depends(get_current_user)) -> User:
        if not any(user.has_role(role) for role in roles):
            raise Forbidden("当前账户没有执行该操作的权限")
        return user

    return _dependency


require_merchant = require_roles(ROLE_MERCHANT)
require_consultant = require_roles(ROLE_CONSULTANT)
require_family_member = require_roles(ROLE_FAMILY_MEMBER)


def get_merchant_profile(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> MerchantProfile:
    """当前用户作为经营者时的经营档案（商户视角资源根）。"""
    if not user.has_role(ROLE_MERCHANT):
        raise Forbidden("当前账户不是经营主体账户")
    return MerchantService(db).require_by_user(user.id)


def get_merchant_or_404(db: Session, merchant_id: str) -> MerchantProfile:
    return MerchantService(db).require_by_id(merchant_id)
