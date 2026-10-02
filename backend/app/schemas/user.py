"""用户与认证相关的数据结构。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.models.user import ALL_ROLES, ROLE_ADMIN
from app.schemas.common import ORMModel

USERNAME_PATTERN = r"^[A-Za-z0-9_.-]{4,32}$"
SELF_SERVICE_ROLES = ("merchant", "family_member", "consultant")


class UserPublic(ORMModel):
    id: str
    username: str
    display_name: str
    status: str
    roles: list[str] = Field(default_factory=list)
    created_at: datetime
    last_login_at: datetime | None = None


class MerchantBrief(ORMModel):
    id: str
    business_name: str
    business_type: str
    default_currency: str
    timezone: str
    default_buffer_amount_cents: int


class UserMe(UserPublic):
    phone: str | None = None
    email: str | None = None
    merchant: MerchantBrief | None = None
    permissions: list[str] = Field(default_factory=list)
    ai_enabled: bool = False


class RegisterRequest(BaseModel):
    username: str = Field(min_length=4, max_length=32, pattern=USERNAME_PATTERN)
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(min_length=1, max_length=32)
    phone: str | None = Field(default=None, max_length=32)
    email: str | None = Field(default=None, max_length=255)
    roles: list[str] = Field(default_factory=lambda: ["merchant"])
    business_name: str | None = Field(default=None, max_length=128)
    business_type: str | None = Field(default=None, max_length=64)

    @field_validator("username")
    @classmethod
    def _lowercase_username(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("roles")
    @classmethod
    def _validate_roles(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip().lower() for item in value if item and item.strip()]
        if not cleaned:
            raise ValueError("至少需要选择一个业务角色")
        for item in cleaned:
            if item not in ALL_ROLES:
                raise ValueError(f"不支持的业务角色：{item}")
            if item == ROLE_ADMIN:
                raise ValueError("管理员账户不能自助注册")
        seen: list[str] = []
        for item in cleaned:
            if item not in seen:
                seen.append(item)
        return seen

    @field_validator("password")
    @classmethod
    def _validate_password(cls, value: str) -> str:
        if len(value) < 8:
            raise ValueError("密码至少 8 位")
        if value.isdigit() or value.isalpha():
            raise ValueError("密码需要同时包含字母和数字")
        return value


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("username")
    @classmethod
    def _lowercase_username(cls, value: str) -> str:
        return value.strip().lower()


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserPublic


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)

    @field_validator("new_password")
    @classmethod
    def _validate_password(cls, value: str) -> str:
        if value.isdigit() or value.isalpha():
            raise ValueError("密码需要同时包含字母和数字")
        return value


class UpdateProfileRequest(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=32)
    phone: str | None = Field(default=None, max_length=32)
    email: str | None = Field(default=None, max_length=255)
