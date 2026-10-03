"""管理员接口（第一版只做基础管理能力）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import client_ip, require_admin
from app.core.config import settings
from app.core.database import check_database, get_db
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.core.security import hash_password
from app.models.cash import AnalysisResult, CashEvent
from app.models.consultation import AuditLog, ConsultationCase
from app.models.household import Household
from app.models.merchant import MerchantProfile
from app.models.user import ALL_ROLES, ROLE_ADMIN, ROLE_MERCHANT, User
from app.repositories.user_repo import AuditService, UserRepository
from app.schemas.common import Page, PageMeta
from app.schemas.user import USERNAME_PATTERN, UserPublic
from app.services.merchant_service import MerchantService
from app.utils.timeutil import utcnow

router = APIRouter(prefix="/admin", tags=["系统管理"])


class OverviewOut(BaseModel):
    users: int
    merchants: int
    cash_events: int
    consultations: int
    households: int
    analysis_results: int
    ai_enabled: bool
    app_env: str
    version: str


class AdminUserCreateIn(BaseModel):
    """管理员创建账户。咨询人员只能通过这里创建。"""

    username: str = Field(min_length=4, max_length=32, pattern=USERNAME_PATTERN)
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(min_length=1, max_length=64)
    roles: list[str] = Field(min_length=1)
    phone: str | None = Field(default=None, max_length=32)
    email: str | None = Field(default=None, max_length=255)
    business_name: str | None = Field(default=None, max_length=128)
    business_type: str | None = Field(default=None, max_length=64)


class RuntimeOut(BaseModel):
    database_ok: bool
    database_size_bytes: int
    wal_enabled: bool
    uptime_seconds: int
    event_count: int
    stale_results: int
    recent_errors: list[str] = Field(default_factory=list)


class SetStatusIn(BaseModel):
    status: str

    @classmethod
    def validate(cls, value: str) -> str:  # pragma: no cover - 由端点处理
        if value not in ("active", "disabled"):
            raise ValueError("状态只能是 active 或 disabled")
        return value


_STARTED_AT = utcnow()


@router.get("/overview", response_model=OverviewOut, summary="系统概览")
def overview(admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> OverviewOut:
    _ = admin
    from app.models.user import User as UserModel

    return OverviewOut(
        users=int(db.scalar(select(func.count(UserModel.id))) or 0),
        merchants=int(db.scalar(select(func.count(MerchantProfile.id))) or 0),
        cash_events=int(db.scalar(select(func.count(CashEvent.id))) or 0),
        consultations=int(db.scalar(select(func.count(ConsultationCase.id))) or 0),
        households=int(db.scalar(select(func.count(Household.id))) or 0),
        analysis_results=int(db.scalar(select(func.count(AnalysisResult.id))) or 0),
        ai_enabled=settings.ai_configured,
        app_env=settings.app_env,
        version=settings.app_version,
    )


@router.get("/users", response_model=Page[UserPublic], summary="用户列表")
def list_users(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=15, ge=1, le=100),
    search: str | None = Query(default=None, max_length=64),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Page[UserPublic]:
    _ = admin
    from app.models.user import User as UserModel

    statement = select(UserModel)
    if search:
        keyword = f"%{search.strip()}%"
        statement = statement.where(
            (UserModel.username.like(keyword)) | (UserModel.display_name.like(keyword))
        )
    statement = statement.order_by(UserModel.created_at.desc())

    total = int(db.scalar(select(func.count()).select_from(statement.order_by(None).subquery())) or 0)
    rows = (
        db.execute(statement.limit(page_size).offset((page - 1) * page_size)).scalars().all()
    )
    return Page[UserPublic](
        items=[
            UserPublic(
                id=item.id,
                username=item.username,
                display_name=item.display_name,
                status=item.status,
                roles=item.role_names(),
                created_at=item.created_at,
                last_login_at=item.last_login_at,
            )
            for item in rows
        ],
        meta=PageMeta(
            page=page,
            page_size=page_size,
            total=total,
            total_pages=(total + page_size - 1) // page_size if total else 0,
        ),
    )


@router.post("/users", response_model=UserPublic, status_code=201, summary="创建账户")
def create_user(
    payload: AdminUserCreateIn,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> UserPublic:
    """由管理员创建账户。

    公开注册只允许经营主体与家庭成员，因此**咨询人员与管理员只能在这里创建**。
    管理员不得给自己追加角色，也不能创建第二个管理员以外的越权角色。
    """
    username = payload.username.strip().lower()
    if UserRepository(db).exists(username):
        raise Conflict(
            "该用户名已被使用，请更换后重试",
            code="USERNAME_TAKEN",
            details={"field": "username"},
        )
    if not payload.roles:
        raise ValidationFailed("至少需要指定一个角色", code="ROLE_REQUIRED")
    unknown = [item for item in payload.roles if item not in ALL_ROLES]
    if unknown:
        raise ValidationFailed(
            "不支持的业务角色",
            code="UNKNOWN_ROLE",
            details={"roles": unknown},
        )

    user = UserRepository(db).create(
        username=username,
        password_hash=hash_password(payload.password),
        display_name=payload.display_name,
        roles=list(dict.fromkeys(payload.roles)),
        phone=payload.phone,
        email=payload.email,
    )
    if payload.business_name and ROLE_MERCHANT in payload.roles:
        MerchantService(db).ensure_profile(
            user=user,
            business_name=payload.business_name,
            business_type=payload.business_type or "个体工商户",
            phone=payload.phone,
        )
    AuditService(db).record(
        "admin.user_created",
        actor=admin,
        resource_type="user",
        resource_id=user.id,
        metadata={"username": user.username, "roles": list(payload.roles)},
        ip_address=client_ip(request),
    )
    db.commit()
    db.refresh(user)
    return UserPublic(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        status=user.status,
        roles=user.role_names(),
        created_at=user.created_at,
        last_login_at=user.last_login_at,
    )


@router.post("/users/{user_id}/status", summary="启用 / 停用账户")
def set_user_status(
    user_id: str,
    payload: SetStatusIn,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    if payload.status not in ("active", "disabled"):
        raise ValidationFailed("状态只能是 active 或 disabled", code="INVALID_STATUS")

    from app.models.user import User as UserModel

    target = db.get(UserModel, user_id)
    if target is None:
        raise NotFound("用户不存在")
    if target.id == admin.id:
        raise ValidationFailed("不能停用自己的账户", code="CANNOT_DISABLE_SELF")
    if target.has_role(ROLE_ADMIN) and payload.status == "disabled":
        raise ValidationFailed("不能停用管理员账户", code="CANNOT_DISABLE_ADMIN")

    target.status = payload.status
    AuditService(db).record(
        "admin.user_status_changed",
        actor=admin,
        resource_type="user",
        resource_id=target.id,
        metadata={"status": payload.status},
        ip_address=client_ip(request),
    )
    db.commit()
    return {"message": "账户状态已更新"}


@router.get("/runtime", response_model=RuntimeOut, summary="运行状态")
def runtime(admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> RuntimeOut:
    _ = admin
    db_path = settings.database_path
    size = 0
    if db_path and db_path.exists():
        size = db_path.stat().st_size

    wal_enabled = False
    try:
        from app.core.database import engine

        with engine.connect() as connection:
            mode = connection.exec_driver_sql("PRAGMA journal_mode").scalar()
            wal_enabled = str(mode).lower() == "wal"
    except Exception:  # pragma: no cover - 防御性
        wal_enabled = False

    stale_results = int(
        db.scalar(
            select(func.count(AnalysisResult.id)).where(AnalysisResult.is_stale.is_(True))
        )
        or 0
    )

    errors: list[str] = []
    log_file = settings.log_path / "app.log"
    if log_file.exists():
        try:
            tail = log_file.read_text(encoding="utf-8", errors="ignore").splitlines()[-400:]
            errors = [
                line.strip()[:240]
                for line in tail
                if " ERROR " in line or " CRITICAL " in line
            ][-5:]
        except OSError:
            errors = []

    return RuntimeOut(
        database_ok=check_database(),
        database_size_bytes=size,
        wal_enabled=wal_enabled,
        uptime_seconds=int((utcnow() - _STARTED_AT).total_seconds()),
        event_count=int(db.scalar(select(func.count(CashEvent.id))) or 0),
        stale_results=stale_results,
        recent_errors=errors,
    )


@router.get("/audit-logs", summary="审计日志")
def audit_logs(
    limit: int = Query(default=50, ge=1, le=200),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[dict]:
    _ = admin
    rows = db.scalars(
        select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)
    ).all()
    return [
        {
            "id": row.id,
            "actor_id": row.actor_id,
            "actor_name": row.actor_name,
            "action": row.action,
            "resource_type": row.resource_type,
            "resource_id": row.resource_id,
            "merchant_id": row.merchant_id,
            "metadata": row.metadata_json,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]
