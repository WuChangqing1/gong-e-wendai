"""健康检查。"""

from __future__ import annotations

from fastapi import APIRouter

from app.core.config import settings
from app.core.database import check_database
from app.schemas.common import HealthResponse

router = APIRouter(tags=["系统"])


@router.get("/health", response_model=HealthResponse, summary="健康检查")
def health() -> HealthResponse:
    """返回服务与依赖状态。

    仅返回布尔状态，不暴露数据库路径、密钥或任何凭据。
    """
    return HealthResponse(
        status="ok",
        database="ok" if check_database() else "unavailable",
        ai_enabled=settings.ai_configured,
        version=settings.app_version,
        env=settings.app_env,
    )
