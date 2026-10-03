"""Users, roles and refresh sessions."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UTCDateTime

ROLE_MERCHANT = "merchant"
ROLE_FAMILY_MEMBER = "family_member"
ROLE_CONSULTANT = "consultant"

#: 全部业务身份。它们之间**没有等级关系**，只是不同角色。
#:
#: 工 e 稳袋按产品定位是上层银行 / 商户服务 App 中的一个业务模块，
#: 平台级用户与运维管理由上层系统承担，因此本系统**不存在 admin 身份**，
#: 也没有对应的用户管理后台、运行状态面板与 /admin API。
ALL_ROLES = (ROLE_MERCHANT, ROLE_FAMILY_MEMBER, ROLE_CONSULTANT)


def new_id() -> str:
    return str(uuid.uuid4())


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(64), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    roles: Mapped[list["UserRole"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", lazy="selectin"
    )
    merchant_profile: Mapped["MerchantProfile | None"] = relationship(  # noqa: F821
        back_populates="user", uselist=False, lazy="selectin"
    )

    def role_names(self) -> list[str]:
        return sorted({role.role for role in self.roles})

    def has_role(self, role: str) -> bool:
        return any(item.role == role for item in self.roles)


class UserRole(Base, TimestampMixin):
    __tablename__ = "user_roles"
    __table_args__ = (UniqueConstraint("user_id", "role", name="uq_user_roles_user_role"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)

    user: Mapped[User] = relationship(back_populates="roles")


class RefreshSession(Base, TimestampMixin):
    """Server-side record of an issued refresh token (rotation + revocation)."""

    __tablename__ = "refresh_sessions"
    __table_args__ = (Index("ix_refresh_sessions_user_active", "user_id", "revoked_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    rotated_to: Mapped[str | None] = mapped_column(String(36), nullable=True)
    remember: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
