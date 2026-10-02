"""家庭协同数据结构。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.models.household import CARD_TYPES, REACTION_TYPES


class HouseholdCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)

    @field_validator("name")
    @classmethod
    def _strip(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("家庭名称不能为空")
        return cleaned


class HouseholdJoin(BaseModel):
    invite_code: str = Field(min_length=4, max_length=16)
    relation_label: str | None = Field(default=None, max_length=32)

    @field_validator("invite_code")
    @classmethod
    def _upper(cls, value: str) -> str:
        return value.strip().upper()


class MemberOut(BaseModel):
    membership_id: str
    user_id: str
    display_name: str
    username: str
    role: str
    relation_label: str | None = None
    status: str
    joined_at: datetime | None = None


class HouseholdOut(BaseModel):
    id: str
    name: str
    owner_id: str
    merchant_id: str
    invite_code: str | None = None
    invite_code_active: bool
    created_at: datetime
    members: list[MemberOut] = Field(default_factory=list)


class MyMembershipOut(BaseModel):
    household_id: str
    household_name: str
    status: str


class InviteCodeOut(BaseModel):
    invite_code: str


class CardRecipientOut(BaseModel):
    user_id: str
    display_name: str
    is_read: bool
    read_at: datetime | None = None
    reaction: str | None = None
    reacted_at: datetime | None = None


class CardCommentOut(BaseModel):
    id: str
    user_id: str
    display_name: str
    content: str
    created_at: datetime


class CardOut(BaseModel):
    id: str
    household_id: str
    card_type: str
    title: str
    summary: str | None = None
    payload: dict = Field(default_factory=dict)
    shared_fields: list[str] = Field(default_factory=list)
    system_max_withdrawable_cents: int | None = None
    planned_household_amount_cents: int | None = None
    cash_event_id: str | None = None
    created_at: datetime
    updated_at: datetime
    recipients: list[CardRecipientOut] = Field(default_factory=list)
    comments: list[CardCommentOut] = Field(default_factory=list)
    my_reaction: str | None = None
    is_read: bool = False


class CardPreviewRequest(BaseModel):
    card_type: str = "decision"
    shared_fields: list[str] = Field(default_factory=list)
    analysis_result_id: str | None = None
    cash_event_id: str | None = None
    planned_household_amount_cents: int | None = Field(default=None, ge=0)

    @field_validator("card_type")
    @classmethod
    def _check_type(cls, value: str) -> str:
        if value not in CARD_TYPES:
            raise ValueError("不支持的协同卡类型")
        return value


class CardPreviewOut(BaseModel):
    title: str
    summary: str
    payload: dict
    shared_fields: list[str]


class CardCreate(CardPreviewRequest):
    title: str | None = Field(default=None, max_length=128)
    summary: str | None = Field(default=None, max_length=2000)


class CardUpdate(BaseModel):
    planned_household_amount_cents: int | None = Field(default=None, ge=0)


class ReactionIn(BaseModel):
    reaction: str

    @field_validator("reaction")
    @classmethod
    def _check(cls, value: str) -> str:
        if value not in REACTION_TYPES:
            raise ValueError("不支持的反馈类型")
        return value


class CommentIn(BaseModel):
    content: str = Field(min_length=1, max_length=500)

    @field_validator("content")
    @classmethod
    def _strip(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("评论不能为空")
        return cleaned
