"""Unified error types and API error responses.

Every business failure carries a stable machine code plus a Chinese message so
clients never have to parse prose.  ``500`` is reserved for genuine unexpected
defects.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_logger

logger = get_logger(__name__)


class AppError(Exception):
    """Base class for expected, business-level failures."""

    status_code: int = 400
    code: str = "BAD_REQUEST"
    message: str = "请求无法处理"

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.message = message or self.message
        self.code = code or self.code
        self.status_code = status_code or self.status_code
        self.details: dict[str, Any] = details or {}
        super().__init__(self.message)

    def to_payload(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}


class ValidationFailed(AppError):
    status_code = 422
    code = "VALIDATION_FAILED"
    message = "提交的数据不符合要求"


class NotFound(AppError):
    status_code = 404
    code = "NOT_FOUND"
    message = "未找到对应数据"


class Forbidden(AppError):
    status_code = 403
    code = "FORBIDDEN"
    message = "没有访问该数据的权限"


class Unauthenticated(AppError):
    status_code = 401
    code = "UNAUTHENTICATED"
    message = "登录状态已失效，请重新登录"


class Conflict(AppError):
    status_code = 409
    code = "CONFLICT"
    message = "数据状态冲突"


class DuplicateCashKey(Conflict):
    code = "DUPLICATE_CASH_KEY"
    message = "该收付款事项已经存在"


class InputIncomplete(AppError):
    status_code = 422
    code = "INPUT_INCOMPLETE"
    message = "资料不完整，暂时无法给出可提用金额"


class RateLimited(AppError):
    status_code = 429
    code = "RATE_LIMITED"
    message = "操作过于频繁，请稍后再试"


class AIServiceError(AppError):
    """AI capability unavailable — never blocks core functionality."""

    status_code = 503
    code = "AI_UNAVAILABLE"
    message = "智能服务暂时不可用，请手动完成当前操作"


#: 面向用户的统一降级文案（所有 AI 失败原因对外都是这一句）
AI_FALLBACK_MESSAGE = "智能服务暂时不可用，请手动完成当前操作"


def register_ai_error_handler(app: FastAPI, *, message: str = AI_FALLBACK_MESSAGE) -> None:
    """把 :class:`AIServiceError` 统一转换成 503 + 标准降级文案。

    具体失败原因（超时 / 无 Key / HTTP 500 / 非法 JSON）只写日志，
    不下发给前端，避免泄露内部细节。
    """

    @app.exception_handler(AIServiceError)
    async def _ai_error(_: Request, exc: AIServiceError) -> JSONResponse:
        logger.info("AI capability unavailable: %s", exc.message)
        return _json(
            {"code": AIServiceError.code, "message": message, "details": {}},
            AIServiceError.status_code,
        )


def _json(payload: dict[str, Any], status_code: int) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=payload)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return _json(exc.to_payload(), exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = []
        for item in exc.errors():
            loc = ".".join(str(part) for part in item.get("loc", ()) if part != "body")
            details.append({"field": loc or "body", "reason": item.get("msg", "格式不正确")})
        return _json(
            {
                "code": ValidationFailed.code,
                "message": ValidationFailed.message,
                "details": {"errors": details},
            },
            422,
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {
            400: "BAD_REQUEST",
            401: "UNAUTHENTICATED",
            403: "FORBIDDEN",
            404: "NOT_FOUND",
            405: "METHOD_NOT_ALLOWED",
            409: "CONFLICT",
            413: "PAYLOAD_TOO_LARGE",
            415: "UNSUPPORTED_MEDIA_TYPE",
            429: "RATE_LIMITED",
        }.get(exc.status_code, "HTTP_ERROR")
        message = exc.detail if isinstance(exc.detail, str) else "请求无法处理"
        return _json({"code": code, "message": message, "details": {}}, exc.status_code)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error on %s %s", request.method, request.url.path)
        return _json(
            {
                "code": "INTERNAL_ERROR",
                "message": "服务处理失败，请稍后重试",
                "details": {},
            },
            500,
        )
