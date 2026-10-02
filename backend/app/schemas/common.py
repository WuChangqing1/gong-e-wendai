"""通用数据结构。"""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class HealthResponse(BaseModel):
    status: str
    database: str
    ai_enabled: bool
    version: str
    env: str


class ErrorResponse(BaseModel):
    code: str
    message: str
    details: dict = Field(default_factory=dict)


class PageMeta(BaseModel):
    page: int
    page_size: int
    total: int
    total_pages: int


class Page(BaseModel, Generic[T]):
    items: list[T]
    meta: PageMeta


class MessageResponse(BaseModel):
    message: str
    code: str = "OK"


class ORMModel(BaseModel):
    """可以直接由 ORM 对象构造的基类。"""

    model_config = ConfigDict(from_attributes=True)
