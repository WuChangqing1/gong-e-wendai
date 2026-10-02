"""FastAPI 应用入口。

职责边界：
* 装配中间件、异常处理、路由、SPA 静态资源挂载
* 不承载业务逻辑（业务逻辑位于 ``app/services``）

SPA 回退规则：
* ``/api/*`` 未命中 -> 正常返回 API 404（JSON），绝不回退到 ``index.html``
* 其它未命中的 GET 路径 -> 返回 ``index.html``，保证前端路由刷新不 404
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.v1.router import api_router
from app.api.v1.ai import AI_UNAVAILABLE_MESSAGE
from app.core.config import settings
from app.core.database import check_database
from app.core.errors import register_ai_error_handler, register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.security import CSRF_HEADER, CSRF_HEADER_VALUE

logger = get_logger(__name__)

API_PREFIX = "/api"

DESCRIPTION = """
「工 e 稳袋」面向小餐饮、小零售、夫妻店、个体工商户等经营者，
回答一个具体问题：**今天到底可以从经营资金中拿多少钱用于家庭，
同时不影响未来 7 天已经确认的经营付款。**

金额、时间、余额、可提用金额、缺口均由确定性计算引擎完成，
智能服务只负责理解、提取、整理与表达，不参与任何金额计算。
""".strip()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    settings.ensure_runtime_dirs()

    problems = settings.validate_production()
    if problems:
        for item in problems:
            logger.error("生产配置检查未通过：%s", item)
        raise RuntimeError("生产配置检查未通过：" + "；".join(problems))

    logger.info(
        "启动 %s (env=%s) 数据库可用=%s AI=%s",
        settings.app_name,
        settings.app_env,
        check_database(),
        "配置已就绪" if settings.ai_configured else "未启用",
    )
    yield
    logger.info("服务已停止")


def create_app() -> FastAPI:
    configure_logging()
    settings.ensure_runtime_dirs()

    app = FastAPI(
        title="工 e 稳袋 API",
        description=DESCRIPTION,
        version=settings.app_version,
        docs_url=None if settings.is_production else "/api/docs",
        redoc_url=None,
        openapi_url=None if settings.is_production else "/api/openapi.json",
        lifespan=lifespan,
    )

    # ---------------- CORS ----------------
    # 生产为同源部署（前端 build 由本服务托管），因此不需要放开来源。
    origins = settings.cors_origin_list
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
            allow_headers=["Content-Type", "Authorization", CSRF_HEADER],
        )

    # ---------------- CSRF 防护 ----------------
    # 认证信息放在 HttpOnly + SameSite=Lax 的 Cookie 中；对写操作额外要求
    # 自定义请求头，跨站表单无法伪造该头。
    @app.middleware("http")
    async def _csrf_guard(request: Request, call_next):  # noqa: ANN001, ANN202
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            path = request.url.path
            if path.startswith(API_PREFIX) and request.headers.get(CSRF_HEADER) != CSRF_HEADER_VALUE:
                # Bearer 令牌调用（脚本/移动端）不需要该头
                if not request.headers.get("authorization"):
                    return JSONResponse(
                        status_code=403,
                        content={
                            "code": "CSRF_HEADER_MISSING",
                            "message": "请求缺少必要的安全标头",
                            "details": {"header": CSRF_HEADER},
                        },
                    )
        return await call_next(request)

    @app.middleware("http")
    async def _security_headers(request: Request, call_next):  # noqa: ANN001, ANN202
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        return response

    register_exception_handlers(app)
    # 智能服务失败时的统一降级文案：核心功能永不受影响
    register_ai_error_handler(app, message=AI_UNAVAILABLE_MESSAGE)

    app.include_router(api_router, prefix="/api/v1")

    _mount_frontend(app)
    return app


def _detect_asset_prefix(index_file_path) -> str:  # noqa: ANN001
    """从构建产物中推断静态资源前缀。

    前端可以构建在根路径（``/assets/...``）或父站点的子路径下
    （``/wendai/assets/...``）。这里读取 ``index.html`` 中第一个 ``/assets/``
    引用，得到实际前缀，避免靠人工配置出错。

    返回形如 ``/assets`` 或 ``/wendai/assets`` 的前缀。
    """
    try:
        html = index_file_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return "/assets"
    match = re.search(r'["\'](/?[\w\-./]*?/assets)/', html)
    if not match:
        return "/assets"
    prefix = match.group(1)
    if not prefix.startswith("/"):
        prefix = "/" + prefix
    return prefix.rstrip("/")


def _mount_frontend(app: FastAPI) -> None:
    """挂载前端 build 产物并配置 SPA 回退。

    同时支持两种部署形态：

    * 根路径部署：``/``、``/assets/*``
    * 父站点子路径部署：``/``、``/wendai/``、``/wendai/assets/*``

    ``/api/*`` 未命中时始终返回 JSON 404，绝不返回 ``index.html``；
    这一判断同时覆盖子路径形态（``/wendai/api/*``）。
    """
    dist = settings.frontend_dist
    assets_dir = dist / "assets"
    index_file = dist / "index.html"

    if not dist.exists() or not index_file.exists():
        logger.info("未发现前端构建产物（%s），仅提供 API 服务", dist)
        return

    asset_prefix = _detect_asset_prefix(index_file)
    base_prefix = asset_prefix[: -len("/assets")] if asset_prefix.endswith("/assets") else ""
    logger.info("前端静态资源前缀：%s（子路径：%s）", asset_prefix, base_prefix or "/")

    if assets_dir.exists():
        app.mount(asset_prefix, StaticFiles(directory=assets_dir), name="assets")

    # 前端 public 目录下的静态资源（同时注册根路径与子路径）
    for name in ("favicon.svg", "favicon.ico", "logo.svg", "robots.txt"):
        candidate = dist / name
        if candidate.exists():

            def _make(path=candidate, filename=name):  # noqa: ANN001, ANN202
                async def _serve() -> FileResponse:
                    return FileResponse(path)

                _serve.__name__ = f"static_{filename.replace('.', '_')}"
                return _serve

            app.get(f"/{name}", include_in_schema=False)(_make())
            if base_prefix:
                app.get(f"{base_prefix}/{name}", include_in_schema=False)(_make())

    @app.get("/", include_in_schema=False)
    async def _index() -> FileResponse:
        return FileResponse(index_file)

    @app.get("/{full_path:path}", include_in_schema=False, response_model=None)
    async def _spa_fallback(full_path: str):  # noqa: ANN202
        # /api/* 未命中时返回 API 404，绝不返回 index.html
        # 子路径部署时前缀为 /wendai/api/*
        normalised = full_path.lstrip("/")
        api_marker = API_PREFIX.lstrip("/")  # "api"
        if normalised == api_marker or normalised.startswith(f"{api_marker}/"):
            return JSONResponse(
                status_code=404,
                content={"code": "NOT_FOUND", "message": "接口不存在", "details": {}},
            )
        if base_prefix:
            sub = base_prefix.lstrip("/")
            if normalised == sub or normalised.startswith(f"{sub}/"):
                if f"{api_marker}/" in normalised or normalised.endswith(f"/{api_marker}"):
                    return JSONResponse(
                        status_code=404,
                        content={"code": "NOT_FOUND", "message": "接口不存在", "details": {}},
                    )
                # 子路径下的文件请求优先，其余交给 SPA 路由
                relative = normalised[len(sub) :].lstrip("/")
                if relative:
                    candidate = (dist / relative).resolve()
                    try:
                        candidate.relative_to(dist.resolve())
                    except ValueError:
                        candidate = None  # type: ignore[assignment]
                    if candidate is not None and candidate.is_file():
                        return FileResponse(candidate)
                return FileResponse(index_file)

        # 其余静态文件优先
        candidate = (dist / normalised).resolve()
        try:
            candidate.relative_to(dist.resolve())
        except ValueError:
            return JSONResponse(
                status_code=404,
                content={"code": "NOT_FOUND", "message": "资源不存在", "details": {}},
            )
        if candidate.is_file():
            return FileResponse(candidate)
        # SPA 路由回退
        return FileResponse(index_file)


app = create_app()
