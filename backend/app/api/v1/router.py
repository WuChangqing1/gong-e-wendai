"""API v1 路由装配。"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import (
    admin,
    ai,
    analysis,
    auth,
    cash_events,
    consultations,
    enhancements,
    health,
    households,
    imports,
    me,
    merchant,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(me.router)
api_router.include_router(merchant.router)
api_router.include_router(cash_events.router)
api_router.include_router(analysis.router)
api_router.include_router(enhancements.router)
api_router.include_router(imports.router)
api_router.include_router(households.router)
api_router.include_router(consultations.router)
api_router.include_router(ai.router)
api_router.include_router(admin.router)
