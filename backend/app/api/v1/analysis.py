"""资金分析与情景接口。"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import client_ip, get_current_user, get_merchant_profile
from app.core.database import get_db
from app.core.errors import ValidationFailed
from app.models.merchant import MerchantProfile
from app.models.user import User
from app.repositories.user_repo import AuditService
from app.schemas.analysis import (
    MODE_CURRENT_PLAN,
    MODE_DELAYED,
    MODE_JOINT,
    MODE_SCENARIOS,
    AnalysisResultOut,
    AnalysisRunRequest,
    ScenarioCreate,
    ScenarioOut,
    ScenarioUpdate,
    StaleStatus,
    WindowSummary,
)
from app.schemas.cash_event import CancelRequest
from app.services.analysis_service import DEFAULT_DELAY_DAYS, AnalysisService

router = APIRouter(tags=["资金分析"])


@router.get(
    "/analysis/window-summary",
    response_model=WindowSummary,
    summary="未来 7 天窗口聚合（供图表使用）",
)
def window_summary(
    mode: str = Query(
        default=MODE_CURRENT_PLAN,
        description="口径：current_plan / delayed / joint / scenarios，缺省按当前计划",
    ),
    delay_days: int = Query(default=DEFAULT_DELAY_DAYS, ge=0, le=30),
    scenario_ids: list[str] | None = Query(
        default=None, description="mode=scenarios 时要聚合的自定义情景"
    ),
    buffer_cents: int | None = Query(default=None, ge=0),
    reference_at: datetime | None = Query(
        default=None, description="指定期初时点，默认取最近一次资金时点"
    ),
    profile: MerchantProfile = Depends(get_merchant_profile),
    db: Session = Depends(get_db),
) -> WindowSummary:
    """按日聚合收付款、按事项类型聚合收支结构、给出待结算到账时间分布。

    全部由确定性引擎的事件扫描结果聚合，不重新定义任何金额规则；
    ``mode`` 决定统计哪些事项，共同约束模式取**真正绑定**的那个情景。
    """
    if mode not in (MODE_CURRENT_PLAN, MODE_DELAYED, MODE_JOINT, MODE_SCENARIOS):
        raise ValidationFailed("不支持的分析模式", code="UNSUPPORTED_MODE")
    return AnalysisService(db).window_summary(
        profile,
        mode=mode,
        delay_days=delay_days,
        scenario_ids=scenario_ids,
        snapshot_at=reference_at,
        buffer_cents=buffer_cents,
    )


@router.post("/analysis/run", response_model=AnalysisResultOut, summary="运行资金分析")
def run_analysis(
    payload: AnalysisRunRequest,
    request: Request,
    profile: MerchantProfile = Depends(get_merchant_profile),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AnalysisResultOut:
    """执行未来 7 天推演。

    金额、时间、余额、可提用金额与缺口全部由确定性引擎计算，AI 不参与。
    """
    service = AnalysisService(db)
    result = service.run(profile, payload, actor_id=user.id)
    AuditService(db).record(
        "analysis.run",
        actor=user,
        resource_type="analysis_result",
        resource_id=result.id,
        merchant_id=profile.id,
        metadata={
            "mode": payload.mode,
            "status": result.status,
            "max_withdrawable_cents": result.max_withdrawable_cents,
        },
        ip_address=client_ip(request),
    )
    db.commit()
    return result


@router.get(
    "/analysis/today",
    response_model=AnalysisResultOut,
    summary="今日决策（按当前计划）",
)
def today_analysis(
    persist: bool = Query(default=False, description="是否落库为一次分析结果"),
    profile: MerchantProfile = Depends(get_merchant_profile),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AnalysisResultOut:
    service = AnalysisService(db)
    return service.run(
        profile, AnalysisRunRequest(), actor_id=user.id, persist=persist
    )


@router.get("/analysis/stale", response_model=StaleStatus, summary="结果是否已失效")
def stale_status(
    profile: MerchantProfile = Depends(get_merchant_profile),
    db: Session = Depends(get_db),
) -> StaleStatus:
    service = AnalysisService(db)
    latest = service.latest_result(profile.id)
    return StaleStatus(
        is_stale=service.is_latest_stale(profile.id),
        last_generated_at=latest.created_at if latest else None,
        stale_reason=latest.stale_reason if latest else None,
        max_withdrawable_cents=latest.max_withdrawable_cents if latest else None,
    )


@router.get("/analysis/history", summary="历史分析记录")
def analysis_history(
    limit: int = Query(default=20, ge=1, le=100),
    profile: MerchantProfile = Depends(get_merchant_profile),
    db: Session = Depends(get_db),
) -> list[dict]:
    from sqlalchemy import select

    from app.models.cash import AnalysisResult
    from app.services.cash_engine import AnalysisStatus, is_engine_version_current

    rows = db.scalars(
        select(AnalysisResult)
        .where(AnalysisResult.merchant_id == profile.id)
        .order_by(AnalysisResult.created_at.desc())
        .limit(limit)
    ).all()
    return [
        {
            "id": row.id,
            "mode": row.mode,
            # 历史结果按当前状态枚举归一：旧版本的 OK 统一读作 FEASIBLE
            "status": str(
                AnalysisStatus.coerce(row.status) or AnalysisStatus.INPUT_INCOMPLETE
            ),
            "max_withdrawable_cents": row.max_withdrawable_cents,
            "snapshot_at": row.snapshot_at.isoformat(),
            "limiting_timestamp": row.limiting_timestamp.isoformat()
            if row.limiting_timestamp
            else None,
            "payment_gap_cents": row.payment_gap_cents,
            "buffer_gap_cents": row.buffer_gap_cents,
            "is_stale": row.is_stale,
            "stale_reason": row.stale_reason,
            "engine_version": row.engine_version,
            "engine_version_current": is_engine_version_current(row.engine_version),
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]


# ---------------------------------------------------------------------------
# 情景
# ---------------------------------------------------------------------------
@router.get("/scenarios", response_model=list[ScenarioOut], summary="情景列表")
def list_scenarios(
    profile: MerchantProfile = Depends(get_merchant_profile),
    db: Session = Depends(get_db),
) -> list[ScenarioOut]:
    return AnalysisService(db).list_scenarios(profile.id)


@router.post(
    "/scenarios", response_model=ScenarioOut, status_code=201, summary="创建情景"
)
def create_scenario(
    payload: ScenarioCreate,
    request: Request,
    profile: MerchantProfile = Depends(get_merchant_profile),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ScenarioOut:
    service = AnalysisService(db)
    result = service.create_scenario(
        profile,
        name=payload.name,
        kind=payload.kind,
        description=payload.description,
        actor_id=user.id,
        overrides=[item.model_dump() for item in payload.overrides],
    )
    AuditService(db).record(
        "scenario.created",
        actor=user,
        resource_type="scenario",
        resource_id=result.id,
        merchant_id=profile.id,
        metadata={"name": result.name, "kind": result.kind},
        ip_address=client_ip(request),
    )
    db.commit()
    return result


@router.patch("/scenarios/{scenario_id}", response_model=ScenarioOut, summary="修改情景")
def update_scenario(
    scenario_id: str,
    payload: ScenarioUpdate,
    profile: MerchantProfile = Depends(get_merchant_profile),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ScenarioOut:
    service = AnalysisService(db)
    scenario = service.require_scenario(scenario_id, profile.id)
    result = service.update_scenario(
        scenario,
        name=payload.name,
        description=payload.description,
        overrides=[item.model_dump() for item in payload.overrides]
        if payload.overrides is not None
        else None,
    )
    AuditService(db).record(
        "scenario.updated",
        actor=user,
        resource_type="scenario",
        resource_id=scenario.id,
        merchant_id=profile.id,
        metadata={"name": scenario.name},
    )
    db.commit()
    return result


@router.delete("/scenarios/{scenario_id}", summary="删除情景")
def delete_scenario(
    scenario_id: str,
    profile: MerchantProfile = Depends(get_merchant_profile),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    service = AnalysisService(db)
    scenario = service.require_scenario(scenario_id, profile.id)
    service.delete_scenario(scenario)
    AuditService(db).record(
        "scenario.deleted",
        actor=user,
        resource_type="scenario",
        resource_id=scenario_id,
        merchant_id=profile.id,
        metadata={},
    )
    db.commit()
    return {"message": "情景已删除"}


@router.post(
    "/scenarios/compare",
    response_model=AnalysisResultOut,
    summary="多情景共同约束比较",
)
def compare_scenarios(
    scenario_ids: list[str],
    delay_days: int = Query(default=3, ge=1, le=30),
    profile: MerchantProfile = Depends(get_merchant_profile),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AnalysisResultOut:
    payload = AnalysisRunRequest(
        mode="scenarios" if scenario_ids else "joint",
        scenario_ids=scenario_ids or None,
        delay_days=delay_days,
    )
    return AnalysisService(db).run(profile, payload, actor_id=user.id)


__all__ = ["CancelRequest", "router"]
