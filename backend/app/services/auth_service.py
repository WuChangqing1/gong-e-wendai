"""认证与账户服务。"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import Conflict, Forbidden, NotFound, Unauthenticated, ValidationFailed
from app.core.logging import get_logger
from app.core.security import (
    create_token,
    hash_password,
    needs_rehash,
    token_fingerprint,
    verify_password,
)
from app.models.user import (
    ROLE_ADMIN,
    ROLE_CONSULTANT,
    ROLE_FAMILY_MEMBER,
    ROLE_MERCHANT,
    User,
)
from app.repositories.user_repo import AuditRepository, RefreshSessionRepository, UserRepository
from app.schemas.user import LoginRequest, RegisterRequest, UserMe, UserPublic
from app.services.merchant_service import MerchantService
from app.utils.timeutil import as_utc, utcnow

logger = get_logger(__name__)

ACTION_LOGIN = "auth.login"
ACTION_LOGIN_FAILED = "auth.login_failed"
ACTION_LOGOUT = "auth.logout"
ACTION_REGISTER = "auth.register"
ACTION_REFRESH = "auth.refresh"
ACTION_PASSWORD_CHANGED = "auth.password_changed"

#: 允许自助注册的角色。咨询人员与管理员都只能由管理员或安全脚本创建。
SELF_REGISTER_ROLES = (ROLE_MERCHANT, ROLE_FAMILY_MEMBER)


class AuthService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.users = UserRepository(db)
        self.sessions = RefreshSessionRepository(db)
        self.audit = AuditRepository(db)

    # ------------------------------------------------------------------
    def register(
        self,
        payload: RegisterRequest,
        *,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> tuple[User, str, str]:
        """创建账户，返回 ``(user, access_token, refresh_token)``。"""
        if self.users.exists(payload.username):
            raise Conflict(
                "该用户名已被使用，请更换后重试",
                code="USERNAME_TAKEN",
                details={"field": "username"},
            )

        roles = list(payload.roles)
        # 公开注册只允许经营主体与家庭成员。
        # 咨询人员必须由管理员创建或授予；管理员只能由已有管理员或安全脚本创建。
        if ROLE_ADMIN in roles and not settings.allow_admin_registration:
            raise Forbidden("管理员账户不能自助注册")
        forbidden = [role for role in roles if role not in SELF_REGISTER_ROLES]
        if forbidden:
            raise Forbidden(
                "该角色不能自助注册，请联系管理员开通",
                code="ROLE_NOT_SELF_REGISTERABLE",
                details={"roles": forbidden, "allowed": list(SELF_REGISTER_ROLES)},
            )

        user = self.users.create(
            username=payload.username,
            password_hash=hash_password(payload.password),
            display_name=payload.display_name,
            roles=roles,
            phone=payload.phone,
            email=payload.email,
        )

        if ROLE_MERCHANT in roles:
            business_name = (payload.business_name or "").strip() or f"{payload.display_name}的经营主体"
            MerchantService(self.db).ensure_profile(
                user=user,
                business_name=business_name,
                business_type=payload.business_type or "个体工商户",
                contact_name=payload.display_name,
                phone=payload.phone,
            )

        self.audit.log(
            action=ACTION_REGISTER,
            actor_id=user.id,
            actor_name=user.display_name,
            resource_type="user",
            resource_id=user.id,
            metadata={"roles": user.role_names()},
            ip_address=ip_address,
        )
        self.db.commit()
        self.db.refresh(user)

        access, refresh = self._issue_tokens(user, user_agent=user_agent)
        self.db.commit()
        return user, access, refresh

    # ------------------------------------------------------------------
    def login(
        self,
        payload: LoginRequest,
        *,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> tuple[User, str, str]:
        user = self.users.get_by_username(payload.username)
        if user is None or not verify_password(payload.password, user.password_hash):
            self.audit.log(
                action=ACTION_LOGIN_FAILED,
                actor_name=payload.username,
                metadata={"reason": "invalid_credentials"},
                ip_address=ip_address,
            )
            self.db.commit()
            raise Unauthenticated("用户名或密码不正确")

        if user.status != "active":
            raise Forbidden("账户已被停用，请联系管理员")

        if needs_rehash(user.password_hash):
            user.password_hash = hash_password(payload.password)

        self.users.touch_last_login(user)
        self.audit.log(
            action=ACTION_LOGIN,
            actor_id=user.id,
            actor_name=user.display_name,
            resource_type="user",
            resource_id=user.id,
            ip_address=ip_address,
        )
        self.db.commit()
        self.db.refresh(user)

        access, refresh = self._issue_tokens(user, user_agent=user_agent)
        self.db.commit()
        return user, access, refresh

    # ------------------------------------------------------------------
    def refresh(self, refresh_token: str | None, *, user_agent: str | None = None) -> tuple[User, str, str]:
        if not refresh_token:
            raise Unauthenticated("登录状态已失效，请重新登录")

        record = self.sessions.get_by_hash(token_fingerprint(refresh_token))
        if record is None or record.revoked_at is not None:
            raise Unauthenticated("登录状态已失效，请重新登录")

        expires_at = as_utc(record.expires_at)
        if expires_at is not None and expires_at <= utcnow():
            self.sessions.revoke(record)
            self.db.commit()
            raise Unauthenticated("登录状态已过期，请重新登录")

        user = self.users.get(record.user_id)
        if user is None or user.status != "active":
            raise Unauthenticated("登录状态已失效，请重新登录")

        self.sessions.revoke(record)
        self.audit.log(
            action=ACTION_REFRESH,
            actor_id=user.id,
            actor_name=user.display_name,
            metadata={},
        )
        self.db.commit()

        access, new_refresh = self._issue_tokens(user, user_agent=user_agent)
        self.db.commit()
        return user, access, new_refresh

    # ------------------------------------------------------------------
    def logout(self, refresh_token: str | None, actor: User | None = None) -> None:
        if refresh_token:
            record = self.sessions.get_by_hash(token_fingerprint(refresh_token))
            if record is not None and record.revoked_at is None:
                self.sessions.revoke(record)
        if actor is not None:
            self.audit.log(
                action=ACTION_LOGOUT,
                actor_id=actor.id,
                actor_name=actor.display_name,
                metadata={},
            )
        self.db.commit()

    # ------------------------------------------------------------------
    def change_password(self, user: User, *, current_password: str, new_password: str) -> None:
        if not verify_password(current_password, user.password_hash):
            raise ValidationFailed(
                "当前密码不正确", code="INVALID_CURRENT_PASSWORD", details={"field": "current_password"}
            )
        if verify_password(new_password, user.password_hash):
            raise ValidationFailed("新密码不能与当前密码相同", code="PASSWORD_UNCHANGED")

        user.password_hash = hash_password(new_password)
        revoked = self.sessions.revoke_all_for_user(user.id)
        self.audit.log(
            action=ACTION_PASSWORD_CHANGED,
            actor_id=user.id,
            actor_name=user.display_name,
            resource_type="user",
            resource_id=user.id,
            metadata={"revoked_sessions": revoked},
        )
        self.db.commit()

    # ------------------------------------------------------------------
    def build_me(self, user: User) -> UserMe:
        profile = MerchantService(self.db).get_by_user(user.id)
        merchant = None
        if profile is not None:
            from app.schemas.user import MerchantBrief

            merchant = MerchantBrief(
                id=profile.id,
                business_name=profile.business_name,
                business_type=profile.business_type,
                default_currency=profile.default_currency,
                timezone=profile.timezone,
                default_buffer_amount_cents=profile.default_buffer_amount_cents,
            )

        roles = user.role_names()
        return UserMe(
            id=user.id,
            username=user.username,
            display_name=user.display_name,
            status=user.status,
            roles=roles,
            created_at=user.created_at,
            last_login_at=user.last_login_at,
            phone=user.phone,
            email=user.email,
            merchant=merchant,
            permissions=permissions_for(roles),
            ai_enabled=settings.ai_configured,
        )

    # ------------------------------------------------------------------
    def _issue_tokens(self, user: User, *, user_agent: str | None) -> tuple[str, str]:
        access_token, _ = create_token(user.id, "access")
        refresh_token, refresh_expires = create_token(user.id, "refresh")
        self.sessions.create(
            user_id=user.id,
            token_hash=token_fingerprint(refresh_token),
            expires_at=refresh_expires,
            user_agent=user_agent,
        )
        return access_token, refresh_token


ROLE_PERMISSIONS: dict[str, tuple[str, ...]] = {
    "merchant": (
        "merchant:read",
        "merchant:write",
        "account:read",
        "account:write",
        "event:read",
        "event:write",
        "import:write",
        "analysis:read",
        "analysis:run",
        "scenario:write",
        "household:manage",
        "consultation:create",
        "consultation:read",
        "ai:use",
    ),
    "family_member": (
        "household:read",
        "household:react",
    ),
    "consultant": (
        "consultation:review",
    ),
    "admin": (
        "admin:read",
        "admin:write",
        "user:manage",
        "audit:read",
    ),
}


def permissions_for(roles: list[str]) -> list[str]:
    seen: list[str] = []
    for role in roles:
        for item in ROLE_PERMISSIONS.get(role, ()):
            if item not in seen:
                seen.append(item)
    return seen


def get_user_or_404(db: Session, user_id: str) -> User:
    user = UserRepository(db).get(user_id)
    if user is None:
        raise NotFound("用户不存在")
    return user


def access_token_ttl_seconds() -> int:
    return int(timedelta(minutes=settings.jwt_access_expire_minutes).total_seconds())
