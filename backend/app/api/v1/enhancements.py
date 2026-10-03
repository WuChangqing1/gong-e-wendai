"""资金增强接口：历史经营数据、结算记录、增强总览与留底确认。

权限
----
以下资源**只允许经营主体（merchant）**访问：历史数据、增强结果、留底建议。
家庭成员与咨询人员一律 403，其他商户不可能通过 ID 访问到本商户数据
（所有查询都以当前登录用户自己的 ``merchant_id`` 为根）。

边界
----
* 增强结果里的历史参考（预测）永远不参与确定性金额计算，
  ``forecast_affects_withdrawable`` 恒为 ``False``。
* 留底确认必须由用户显式点击触发（POST），后端重新校验依据版本，
  任一变化返回 409 ``STALE_RESERVE_ADVICE``。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import client_ip, get_current_user, get_merchant_profile
from app.core.database import get_db
from app.core.errors import ValidationFailed
from app.models.merchant import MerchantProfile
from app.models.user import User
from app.repositories.user_repo import AuditService
from app.schemas.enhancement import (
    DailyHistoryListOut,
    DailyHistoryOut,
    EnhancementOverviewOut,
    HistoryImportConfirmOut,
    HistoryImportConfirmRequest,
    HistoryImportIssue,
    HistoryImportPreviewOut,
    HistoryImportPreviewRequest,
    HistoryImportRow,
    ReserveConfirmOut,
    ReserveConfirmRequest,
    SettlementImportConfirmOut,
    SettlementImportConfirmRequest,
    SettlementImportPreviewRequest,
    SettlementRecordListOut,
    SettlementRecordOut,
)
from app.services.enhancement_service import EnhancementService
from app.utils.timeutil import utcnow

router = APIRouter(tags=["资金增强"])


def _preview_token(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:32]


# ---------------------------------------------------------------------------
# 历史经营数据
# ---------------------------------------------------------------------------
@router.get(
    "/history/daily",
    response_model=DailyHistoryListOut,
    summary="历史经营数据（已确认完整的自然日）",
)
def list_daily_history(
    limit: int = Query(default=120, ge=1, le=400),
    profile: MerchantProfile = Depends(get_merchant_profile),
    db: Session = Depends(get_db),
) -> DailyHistoryListOut:
    service = EnhancementService(db)
    rows = service.list_history(profile.id, limit=limit)
    summary = service.history_summary(profile.id)
    return DailyHistoryListOut(
        items=[
            DailyHistoryOut(
                id=row.id,
                day=row.day,
                complete=row.complete,
                inflow_cents=row.inflow_cents,
                outflow_cents=row.outflow_cents,
                net_cents=row.inflow_cents - row.outflow_cents,
                source_refs=list(row.source_refs or []),
                completeness_confirmed=row.completeness_confirmed,
                note=row.note,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
            for row in rows
        ],
        complete_days=summary["complete_days"],
        first_day=summary["first_day"],
        last_day=summary["last_day"],
        total_inflow_cents=summary["total_inflow_cents"],
        total_outflow_cents=summary["total_outflow_cents"],
        missing_days=summary["missing_days"],
        continuity_warning=summary["continuity_warning"],
    )


def _preview_history(payload: HistoryImportPreviewRequest) -> HistoryImportPreviewOut:
    issues: list[HistoryImportIssue] = []
    seen: dict[str, int] = {}
    valid = 0
    duplicates = 0

    for index, row in enumerate(payload.rows, start=1):
        key = row.day.isoformat()
        if key in seen:
            duplicates += 1
            issues.append(
                HistoryImportIssue(
                    row=index,
                    field="day",
                    reason=f"日期 {key} 重复（首次出现在第 {seen[key]} 行）",
                )
            )
            continue
        seen[key] = index
        if row.inflow_cents == 0 and row.outflow_cents == 0 and not row.source_label:
            issues.append(
                HistoryImportIssue(
                    row=index,
                    field="amount",
                    reason="当天没有任何收付且没有来源说明，请确认该日数据确实完整",
                    level="warning",
                )
            )
        valid += 1

    days = sorted(seen)
    range_start = payload.range_start
    range_end = payload.range_end
    if days:
        from datetime import date as _date

        if range_start is None:
            range_start = _date.fromisoformat(days[0])
        if range_end is None:
            range_end = _date.fromisoformat(days[-1])
    missing = 0
    if range_start and range_end and range_end >= range_start:
        missing = (range_end - range_start).days + 1 - len(days)

    requires_confirmation = missing > 0
    can_confirm = valid > 0 and (payload.completeness_confirmed or not requires_confirmation)

    token = _preview_token(
        {
            "merchant": True,
            "rows": [
                {"day": row.day.isoformat(), "in": row.inflow_cents, "out": row.outflow_cents}
                for row in payload.rows
            ],
        }
    )

    return HistoryImportPreviewOut(
        total_rows=len(payload.rows),
        valid_rows=valid,
        invalid_rows=len(payload.rows) - valid,
        duplicate_rows=duplicates,
        missing_row_count=max(0, missing),
        range_start=range_start,
        range_end=range_end,
        completeness_confirmed=payload.completeness_confirmed,
        can_confirm=can_confirm,
        requires_completeness_confirmation=requires_confirmation,
        issues=issues,
        sample=payload.rows[:50],
        preview_token=token,
    )


@router.post(
    "/history/import/preview",
    response_model=HistoryImportPreviewOut,
    summary="历史经营数据导入预览",
)
def preview_history_import(
    payload: HistoryImportPreviewRequest,
    profile: MerchantProfile = Depends(get_merchant_profile),
) -> HistoryImportPreviewOut:
    del profile
    return _preview_history(payload)


@router.post(
    "/history/import/confirm",
    response_model=HistoryImportConfirmOut,
    summary="确认导入历史经营数据",
)
def confirm_history_import(
    payload: HistoryImportConfirmRequest,
    request: Request,
    profile: MerchantProfile = Depends(get_merchant_profile),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> HistoryImportConfirmOut:
    preview = _preview_history(
        HistoryImportPreviewRequest(
            rows=payload.rows,
            completeness_confirmed=payload.completeness_confirmed,
            fill_missing_days=payload.fill_missing_days,
        )
    )
    # token 只由数据行决定：确认时的「完整性确认」开关不参与，否则同一份数据
    # 在预览与确认两步之间会因为勾选状态不同而被误判为已过期。
    if preview.preview_token != payload.preview_token:
        raise ValidationFailed(
            "预览数据已经变化，请重新预览后再确认", code="PREVIEW_STALE"
        )
    if preview.requires_completeness_confirmation and not payload.completeness_confirmed:
        raise ValidationFailed(
            "该日期范围内存在没有记录的日期。请先确认「该日期范围内数据完整；"
            "没有记录的日期代表当天确实没有对应收付」，再继续导入。",
            code="COMPLETENESS_NOT_CONFIRMED",
            details={"missing_days": preview.missing_row_count},
        )
    if not preview.can_confirm:
        raise ValidationFailed("没有可导入的有效数据", code="NO_VALID_ROWS")

    service = EnhancementService(db)
    created, updated, filled = service.upsert_history_rows(
        profile.id,
        payload.rows,
        completeness_confirmed=payload.completeness_confirmed,
        fill_missing_days=payload.fill_missing_days,
        actor_id=user.id,
    )
    state = service.get_state(profile.id)
    AuditService(db).record(
        "enhancement.history_imported",
        actor=user,
        resource_type="daily_cash_history",
        resource_id=profile.id,
        merchant_id=profile.id,
        metadata={
            "created": created,
            "updated": updated,
            "filled_zero_days": filled,
            "completeness_confirmed": payload.completeness_confirmed,
        },
        ip_address=client_ip(request),
    )
    db.commit()
    return HistoryImportConfirmOut(
        created=created,
        updated=updated,
        skipped=len(payload.rows) - created - updated,
        filled_zero_days=filled,
        history_revision=state.history_revision,
        message=(
            f"已导入 {created + updated} 天历史经营数据"
            + (f"，其中 {filled} 天按完整性确认记为无收付" if filled else "")
        ),
    )


# ---------------------------------------------------------------------------
# 结算记录
# ---------------------------------------------------------------------------
@router.get(
    "/settlement-records",
    response_model=SettlementRecordListOut,
    summary="结算记录（预计 / 实际到账配对）",
)
def list_settlement_records(
    profile: MerchantProfile = Depends(get_merchant_profile),
    db: Session = Depends(get_db),
) -> SettlementRecordListOut:
    rows = EnhancementService(db).list_settlement_records(profile.id)
    channels = sorted({row.channel for row in rows})
    items: list[SettlementRecordOut] = []
    for row in rows:
        delay_days = None
        if row.status == "completed" and row.actual_at is not None:
            delta = row.actual_at - row.scheduled_at
            delay_days = max(0, -(-int(delta.total_seconds()) // 86_400))
        items.append(
            SettlementRecordOut(
                id=row.id,
                external_key=row.external_key,
                channel=row.channel,
                scheduled_at=row.scheduled_at,
                actual_at=row.actual_at,
                known_at=row.known_at,
                status=row.status,
                source_ref=row.source_ref,
                note=row.note,
                delay_days=delay_days,
                created_at=row.created_at,
            )
        )
    return SettlementRecordListOut(
        items=items,
        open_count=sum(1 for row in rows if row.status == "open"),
        completed_count=sum(1 for row in rows if row.status == "completed"),
        cancelled_count=sum(1 for row in rows if row.status == "cancelled"),
        channels=channels,
    )


class _SettlementPreviewOut(SettlementImportConfirmOut):
    pass


@router.post(
    "/settlement-records/import/preview",
    summary="结算记录导入预览",
)
def preview_settlement_import(
    payload: SettlementImportPreviewRequest,
    profile: MerchantProfile = Depends(get_merchant_profile),
) -> dict:
    del profile
    issues = []
    seen: set[str] = set()
    valid = 0
    for index, row in enumerate(payload.rows, start=1):
        if row.external_key in seen:
            issues.append(
                {"row": index, "field": "external_key", "reason": "外部编号重复"}
            )
            continue
        seen.add(row.external_key)
        if row.status == "completed" and row.actual_at is None:
            issues.append(
                {
                    "row": index,
                    "field": "actual_at",
                    "reason": "已完成的结算记录必须填写实际到账时间",
                }
            )
            continue
        if row.status == "completed" and row.actual_at and row.actual_at < row.scheduled_at:
            issues.append(
                {
                    "row": index,
                    "field": "actual_at",
                    "reason": "实际到账时间早于预计时间，将按 0 天延迟统计",
                    "level": "warning",
                }
            )
        valid += 1
    return {
        "total_rows": len(payload.rows),
        "valid_rows": valid,
        "invalid_rows": len(payload.rows) - valid,
        "issues": issues,
        "can_confirm": valid > 0,
        "preview_token": _preview_token(
            [row.model_dump(mode="json") for row in payload.rows]
        ),
        "open_count": sum(1 for row in payload.rows if row.status == "open"),
        "completed_count": sum(1 for row in payload.rows if row.status == "completed"),
    }


@router.post(
    "/settlement-records/import/confirm",
    response_model=SettlementImportConfirmOut,
    summary="确认导入结算记录",
)
def confirm_settlement_import(
    payload: SettlementImportConfirmRequest,
    request: Request,
    profile: MerchantProfile = Depends(get_merchant_profile),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SettlementImportConfirmOut:
    expected = _preview_token([row.model_dump(mode="json") for row in payload.rows])
    if expected != payload.preview_token:
        raise ValidationFailed(
            "预览数据已经变化，请重新预览后再确认", code="PREVIEW_STALE"
        )
    service = EnhancementService(db)
    created, updated, skipped = service.upsert_settlement_records(
        profile.id, payload.rows, actor_id=user.id
    )
    state = service.get_state(profile.id)
    AuditService(db).record(
        "enhancement.settlement_imported",
        actor=user,
        resource_type="settlement_record",
        resource_id=profile.id,
        merchant_id=profile.id,
        metadata={"created": created, "updated": updated},
        ip_address=client_ip(request),
    )
    db.commit()
    return SettlementImportConfirmOut(
        created=created,
        updated=updated,
        skipped=skipped,
        history_revision=state.history_revision,
        message=f"已导入 {created + updated} 条结算记录",
    )


# ---------------------------------------------------------------------------
# 增强总览
# ---------------------------------------------------------------------------
@router.get(
    "/enhancements/overview",
    response_model=EnhancementOverviewOut,
    summary="资金增强总览（结算延期压力 / 日常收付参考 / 留底建议）",
)
def enhancement_overview(
    request: Request,
    reference_at: datetime | None = Query(default=None, description="指定期初时点"),
    delay_days: int = Query(default=2, ge=0, le=30),
    delay_quantile: float = Query(default=0.9, gt=0, le=1),
    reserve_quantile: float = Query(default=0.9, gt=0, le=1),
    settlement_channel: str | None = Query(
        default=None,
        max_length=64,
        description="用于筛选历史结算记录样本的渠道；缺省取第一笔结算款的来源标注",
    ),
    profile: MerchantProfile = Depends(get_merchant_profile),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> EnhancementOverviewOut:
    service = EnhancementService(db)
    result = service.overview(
        profile,
        reference_at=reference_at,
        delay_days=delay_days,
        delay_quantile=delay_quantile,
        reserve_quantile=reserve_quantile,
        settlement_channel=settlement_channel,
        actor_id=user.id,
    )
    AuditService(db).record(
        "enhancement.overview",
        actor=user,
        resource_type="enhancement_run",
        resource_id=result.run_id or profile.id,
        merchant_id=profile.id,
        metadata={
            "delay_days": delay_days,
            "needs_review": result.needs_review,
            "forecast_available": result.forecast.available,
        },
        ip_address=client_ip(request),
    )
    db.commit()
    return result


@router.post(
    "/enhancements/reserve/confirm",
    response_model=ReserveConfirmOut,
    summary="确认采用建议留底",
)
def confirm_reserve(
    payload: ReserveConfirmRequest,
    request: Request,
    profile: MerchantProfile = Depends(get_merchant_profile),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReserveConfirmOut:
    service = EnhancementService(db)
    result = service.confirm_reserve(
        profile,
        suggested_reserve_cents=payload.suggested_reserve_cents,
        basis_hash=payload.basis_hash,
        ledger_revision=payload.ledger_revision,
        history_revision=payload.history_revision,
        actor_id=user.id,
        run_id=payload.run_id,
    )
    AuditService(db).record(
        "enhancement.reserve_confirmed",
        actor=user,
        resource_type="reserve_advice_confirmation",
        resource_id=result.analysis.get("id") or profile.id,
        merchant_id=profile.id,
        metadata={
            "previous_reserve_cents": result.previous_reserve_cents,
            "confirmed_reserve_cents": result.confirmed_reserve_cents,
        },
        ip_address=client_ip(request),
    )
    db.commit()
    return result


__all__ = ["router"]
