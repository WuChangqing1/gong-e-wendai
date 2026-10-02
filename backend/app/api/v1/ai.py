"""智能服务接口。

AI 只负责理解、提取、整理与表达；金额与时间约束全部由确定性引擎负责。
AI 关闭 / 未配置 / 超时 / 失败时统一返回 503 ``AI_UNAVAILABLE``，
前端提示「智能服务暂时不可用，请手动完成当前操作。」，核心功能不受影响。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.api.deps import client_ip, get_current_user, get_merchant_profile
from app.models.merchant import MerchantProfile
from app.models.user import User
from app.repositories.user_repo import AuditService
from app.core.database import get_db
from app.services.ai_service import AIService
from sqlalchemy.orm import Session

router = APIRouter(prefix="/ai", tags=["智能服务"])

#: 面向用户的统一降级文案（具体失败原因只在后端日志中体现）
AI_UNAVAILABLE_MESSAGE = "智能服务暂时不可用，请手动完成当前操作"

DISCLAIMER = "智能服务只负责理解与表达，金额、时间与结论均由确定性计算引擎给出。"


class AiStatusOut(BaseModel):
    enabled: bool
    configured: bool
    available: bool
    model: str | None = None
    provider: str = "openai-compatible"


class ExtractRequest(BaseModel):
    text: str = Field(min_length=4, max_length=2000)


class ExtractedEventOut(BaseModel):
    title: str
    direction: str
    amount_cents: int | None = None
    scheduled_at: str | None = None
    state: str = "scheduled"
    source_label: str | None = None
    confidence: dict = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class ExtractResponse(BaseModel):
    event: ExtractedEventOut
    raw_text: str
    filtered_amounts: list[str] = Field(default_factory=list)
    note: str = "提取结果需要你确认后才会写入收付款事项。"
    disclaimer: str = DISCLAIMER


class ExplainRequest(BaseModel):
    max_withdrawable_cents: int | None = None
    opening_balance_cents: int | None = None
    buffer_cents: int | None = None
    status: str | None = None
    limiting_timestamp: str | None = None
    limiting_balance_cents: int | None = None
    limiting_event_title: str | None = None
    limiting_reason: str | None = None
    payment_gap_cents: int | None = None
    buffer_gap_cents: int | None = None
    window_inflow_cents: int | None = None
    window_outflow_cents: int | None = None
    pending_inflows: list[dict[str, Any]] = Field(default_factory=list)


class ExplainResponse(BaseModel):
    explanation: str
    used_fields: list[str] = Field(default_factory=list)
    filtered_amounts: list[str] = Field(default_factory=list)
    disclaimer: str = DISCLAIMER


class DraftRequest(BaseModel):
    question_type: str = Field(max_length=32)
    question: str = Field(default="", max_length=2000)
    fields: dict[str, Any] = Field(default_factory=dict)


class DraftResponse(BaseModel):
    draft: str
    allowed_fields: list[str] = Field(default_factory=list)
    filtered_amounts: list[str] = Field(default_factory=list)
    disclaimer: str = DISCLAIMER


@router.get("/status", response_model=AiStatusOut, summary="智能服务状态")
def status(user: User = Depends(get_current_user)) -> AiStatusOut:
    _ = user
    payload = AIService().status()
    return AiStatusOut.model_validate(payload)


@router.post("/extract-cash-event", response_model=ExtractResponse, summary="智能录入")
def extract_cash_event(
    payload: ExtractRequest,
    request: Request,
    profile: MerchantProfile = Depends(get_merchant_profile),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ExtractResponse:
    result = AIService().extract_cash_event(payload.text)
    AuditService(db).record(
        "ai.extract_cash_event",
        actor=user,
        resource_type="cash_event",
        merchant_id=profile.id,
        metadata={
            "text_length": len(payload.text),
            "filtered_amounts": result.filtered_amounts,
            "direction": result.event.direction,
        },
        ip_address=client_ip(request),
    )
    db.commit()
    return ExtractResponse(
        event=ExtractedEventOut.model_validate(result.event.model_dump()),
        raw_text=result.raw_text,
        filtered_amounts=result.filtered_amounts,
    )


@router.post("/explain-analysis", response_model=ExplainResponse, summary="帮我讲清楚")
def explain_analysis(
    payload: ExplainRequest,
    request: Request,
    profile: MerchantProfile = Depends(get_merchant_profile),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ExplainResponse:
    result = AIService().explain_analysis(payload.model_dump(exclude_none=True))
    AuditService(db).record(
        "ai.explain_analysis",
        actor=user,
        merchant_id=profile.id,
        metadata={"used_fields": result.get("used_fields", [])},
        ip_address=client_ip(request),
    )
    db.commit()
    return ExplainResponse(
        explanation=result["explanation"],
        used_fields=result.get("used_fields", []),
        filtered_amounts=result.get("filtered_amounts", []),
    )


@router.post("/draft-consultation", response_model=DraftResponse, summary="咨询描述整理")
def draft_consultation(
    payload: DraftRequest,
    profile: MerchantProfile = Depends(get_merchant_profile),
    user: User = Depends(get_current_user),
) -> DraftResponse:
    result = AIService().draft_consultation(
        question_type=payload.question_type,
        question=payload.question,
        fields=payload.fields,
    )
    _ = (profile, user)
    return DraftResponse(
        draft=result["draft"],
        allowed_fields=result.get("allowed_fields", []),
        filtered_amounts=result.get("filtered_amounts", []),
    )
