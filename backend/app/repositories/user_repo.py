"""用户、角色、刷新会话与审计日志的数据访问。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.consultation import AuditLog
from app.models.user import RefreshSession, User, UserRole
from app.utils.timeutil import utcnow


class UserRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get(self, user_id: str) -> User | None:
        return self.db.get(User, user_id)

    def get_by_username(self, username: str) -> User | None:
        return self.db.scalar(select(User).where(User.username == username.strip().lower()))

    def exists(self, username: str) -> bool:
        return self.get_by_username(username) is not None

    def create(
        self,
        *,
        username: str,
        password_hash: str,
        display_name: str,
        roles: list[str],
        phone: str | None = None,
        email: str | None = None,
        status: str = "active",
    ) -> User:
        user = User(
            username=username.strip().lower(),
            password_hash=password_hash,
            display_name=display_name.strip(),
            phone=phone,
            email=email,
            status=status,
        )
        user.roles = [UserRole(role=role) for role in roles]
        self.db.add(user)
        self.db.flush()
        return user

    def touch_last_login(self, user: User) -> None:
        user.last_login_at = utcnow()


class RefreshSessionRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(
        self,
        *,
        user_id: str,
        token_hash: str,
        expires_at: datetime,
        user_agent: str | None = None,
    ) -> RefreshSession:
        session = RefreshSession(
            user_id=user_id,
            token_hash=token_hash,
            expires_at=expires_at,
            user_agent=(user_agent or "")[:255] or None,
        )
        self.db.add(session)
        self.db.flush()
        return session

    def get_by_hash(self, token_hash: str) -> RefreshSession | None:
        return self.db.scalar(
            select(RefreshSession).where(RefreshSession.token_hash == token_hash)
        )

    def revoke(self, session: RefreshSession, *, rotated_to: str | None = None) -> None:
        session.revoked_at = utcnow()
        session.rotated_to = rotated_to

    def revoke_all_for_user(self, user_id: str) -> int:
        sessions = self.db.scalars(
            select(RefreshSession).where(
                RefreshSession.user_id == user_id, RefreshSession.revoked_at.is_(None)
            )
        ).all()
        now = utcnow()
        for item in sessions:
            item.revoked_at = now
        return len(sessions)


class AuditRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def log(
        self,
        *,
        action: str,
        actor_id: str | None = None,
        actor_name: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        merchant_id: str | None = None,
        metadata: dict | None = None,
        ip_address: str | None = None,
    ) -> AuditLog:
        entry = AuditLog(
            actor_id=actor_id,
            actor_name=actor_name,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            merchant_id=merchant_id,
            metadata_json=metadata or {},
            ip_address=ip_address,
            created_at=utcnow(),
        )
        self.db.add(entry)
        return entry

    def list_recent(self, *, limit: int = 50, merchant_id: str | None = None) -> list[AuditLog]:
        statement = select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)
        if merchant_id:
            statement = statement.where(AuditLog.merchant_id == merchant_id)
        return list(self.db.scalars(statement).all())


class AuditService:
    """审计日志写入的统一入口。

    禁止记录密码、Refresh Token、完整 API Key —— 由 :func:`app.core.logging.redact`
    在写入前统一脱敏。
    """

    def __init__(self, db: Session) -> None:
        self.db = db
        self.repository = AuditRepository(db)

    def record(
        self,
        action: str,
        *,
        actor: User | None = None,
        actor_id: str | None = None,
        actor_name: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        merchant_id: str | None = None,
        metadata: dict | None = None,
        ip_address: str | None = None,
    ) -> AuditLog:
        from app.core.logging import redact

        return self.repository.log(
            action=action,
            actor_id=actor.id if actor else actor_id,
            actor_name=actor.display_name if actor else actor_name,
            resource_type=resource_type,
            resource_id=resource_id,
            merchant_id=merchant_id,
            metadata=redact(metadata or {}),
            ip_address=ip_address,
        )
