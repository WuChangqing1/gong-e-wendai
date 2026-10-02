"""API v1 路由装配。"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import auth, health, me, merchant

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(me.router)
api_router.include_router(merchant.router)
