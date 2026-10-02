"""现金流读取服务：为首页卡片与引擎提供统一的事件读取入口。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.cash import CashEvent
from app.models.merchant import MerchantProfile
from app.services.cash_engine import (
    DIRECTION_INFLOW,
    DIRECTION_OUTFLOW,
    STATE_CANCELLED,
    CashEventInput,
)
from app.utils.timeutil import to_utc, utcnow


class CashflowService:
    def __init__(self, db: Session) -> None:
        self.db = db

    # ------------------------------------------------------------------
    def list_events(
        self,
        merchant_id: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        include_cancelled: bool = True,
    ) -> list[CashEvent]:
        statement = select(CashEvent).where(CashEvent.merchant_id == merchant_id)
        if not include_cancelled:
            statement = statement.where(CashEvent.state != STATE_CANCELLED)
        if start is not None:
            statement = statement.where(CashEvent.scheduled_at >= to_utc(start).replace(tzinfo=None))
        if end is not None:
            statement = statement.where(CashEvent.scheduled_at <= to_utc(end).replace(tzinfo=None))
        statement = statement.order_by(CashEvent.scheduled_at.asc(), CashEvent.cash_key.asc())
        return list(self.db.scalars(statement).all())

    # ------------------------------------------------------------------
    @staticmethod
    def to_engine_input(event: CashEvent) -> CashEventInput:
        return CashEventInput(
            id=event.id,
            cash_key=event.cash_key,
            title=event.title,
            amount_cents=event.amount_cents,
            direction=event.direction,
            scheduled_at=event.scheduled_at,
            state=event.state,
            event_type=event.event_type,
            sequence_index=event.sequence_index_optional,
            source_label=event.source_label,
            current_version=event.current_version,
            confirmed=event.confirmed,
        )

    def engine_events(
        self, merchant_id: str, *, start: datetime | None = None, end: datetime | None = None
    ) -> list[CashEventInput]:
        return [
            self.to_engine_input(event)
            for event in self.list_events(merchant_id, start=start, end=end)
        ]

    # ------------------------------------------------------------------
    def window_totals(
        self, merchant_id: str, *, start: datetime, end: datetime
    ) -> dict[str, int]:
        """统计窗口内计划中的收支合计（不含已取消、不含已计入期初）。"""
        start_naive = to_utc(start).replace(tzinfo=None)
        end_naive = to_utc(end).replace(tzinfo=None)

        rows = self.db.execute(
            select(CashEvent.direction, func.coalesce(func.sum(CashEvent.amount_cents), 0))
            .where(
                CashEvent.merchant_id == merchant_id,
                CashEvent.state == "scheduled",
                CashEvent.scheduled_at >= start_naive,
                CashEvent.scheduled_at <= end_naive,
            )
            .group_by(CashEvent.direction)
        ).all()

        totals = {"inflow_cents": 0, "outflow_cents": 0}
        for direction, total in rows:
            if direction == DIRECTION_INFLOW:
                totals["inflow_cents"] = int(total or 0)
            elif direction == DIRECTION_OUTFLOW:
                totals["outflow_cents"] = int(total or 0)
        return totals

    # ------------------------------------------------------------------
    def pending_settlement_cents(self, merchant_id: str, *, at: datetime | None = None) -> int:
        """待结算资金：窗口内尚未到账的计划收入合计。"""
        reference = to_utc(at) if at else utcnow()
        from datetime import timedelta

        totals = self.window_totals(
            merchant_id, start=reference, end=reference + timedelta(days=7)
        )
        return totals["inflow_cents"]

    # ------------------------------------------------------------------
    def count_events(self, merchant_id: str) -> int:
        return int(
            self.db.scalar(
                select(func.count(CashEvent.id)).where(CashEvent.merchant_id == merchant_id)
            )
            or 0
        )

    def latest_event_time(self, merchant_id: str) -> datetime | None:
        return self.db.scalar(
            select(func.max(CashEvent.updated_at)).where(CashEvent.merchant_id == merchant_id)
        )


def merchant_of(profile: MerchantProfile) -> str:
    return profile.id
