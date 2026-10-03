"""增强服务：把三个资金增强模块装配到现有确定性账本之上。

强制边界
--------
* 金额计算唯一来源仍是 :mod:`app.services.cash_engine`。本模块只负责装配输入、
  汇总结果与失效判定。
* **预测永远不进入确定性 Cash Engine**：``forecast_affects_withdrawable`` 恒为
  ``False``。预测收入放大任意倍数都不会改变 ``max_withdrawable``。
* 留底建议不自动生效：只有用户显式点击确认才会写入 ``MerchantProfile``。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Sequence

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.errors import Conflict, ValidationFailed
from app.models.cash import AnalysisResult, CashEvent
from app.models.enhancement import (
    SETTLEMENT_COMPLETED,
    SETTLEMENT_OPEN,
    DailyCashHistory,
    EnhancementRun,
    MerchantAnalysisState,
    ReserveAdviceConfirmation,
    SettlementRecord,
)
from app.models.merchant import MerchantProfile
from app.schemas.analysis import AnalysisRunRequest
from app.schemas.enhancement import (
    EnhancementOverviewOut,
    ForecastOut,
    ReserveAdviceOut,
    ReserveConfirmOut,
    SettlementPressureOut,
    SettlementPressureScenarioOut,
    SettlementPressureStatsOut,
)
from app.services.analysis_service import AnalysisService, EVENT_TYPE_LABELS
from app.services.cash_engine import (
    DIRECTION_INFLOW,
    STATE_SCHEDULED,
    CashEventInput,
    EngineInput,
    run_engine,
)
from app.services.forecast_engine import (
    WARMUP_MIN_DAYS,
    DailyCash,
    ForecastError,
    ForecastResult,
    evaluate_forecasts,
    rolling_backtest,
    rows_from_records,
    unavailable_from_error,
)
from app.services.reserve_advisor import (
    DEFAULT_RESERVE_QUANTILE,
    DEFAULT_ROUNDING_CENTS,
    ErrorBlock,
    buffer_blocks,
    recommend_reserve,
)
from app.services.settlement_pressure import (
    DEFAULT_DELAY_QUANTILE,
    DEFAULT_MANUAL_DELAY_DAYS,
    MAX_MANUAL_DELAY_DAYS,
    DelayScenario,
    SettlementPair,
    SettlementStats,
    build_delay_scenarios,
    settlement_stats,
)
from app.utils.timeutil import to_utc, utcnow

STALE_RESERVE_ADVICE = "STALE_RESERVE_ADVICE"
STALE_RESERVE_MESSAGE = "相关数据已经更新，请重新查看留底建议后再确认。"

#: 增强计算参数（全部会在页面上可见，且不称概率）
DEFAULT_PARAMETERS: dict[str, Any] = {
    "delay_days": DEFAULT_MANUAL_DELAY_DAYS,
    "delay_quantile": DEFAULT_DELAY_QUANTILE,
    "reserve_quantile": DEFAULT_RESERVE_QUANTILE,
    "reserve_rounding_cents": DEFAULT_ROUNDING_CENTS,
}


@dataclass(frozen=True, slots=True)
class EnhancementBundle:
    """一次增强计算的完整依据。"""

    baseline_engine_input: EngineInput
    settlement_targets: tuple[CashEventInput, ...]
    settlement_pairs: tuple[SettlementPair, ...]
    history_rows: tuple[DailyCash, ...]
    history_incomplete_reason: str | None
    current_reserve_cents: int
    ledger_revision: int
    history_revision: int
    basis_hash: str


def _hash_payload(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _yuan(cents: int | None) -> str:
    if cents is None:
        return "--"
    sign = "-" if cents < 0 else ""
    value = abs(int(cents))
    return f"{sign}¥{value // 100}.{value % 100:02d}"


class EnhancementService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.analysis = AnalysisService(db)

    # ------------------------------------------------------------------
    # 版本状态
    # ------------------------------------------------------------------
    def get_state(self, merchant_id: str) -> MerchantAnalysisState:
        state = self.db.get(MerchantAnalysisState, merchant_id)
        if state is None:
            state = MerchantAnalysisState(
                merchant_id=merchant_id, ledger_revision=1, history_revision=1
            )
            self.db.add(state)
            self.db.flush()
        return state

    def bump_ledger_revision(self, merchant_id: str, *, commit: bool = True) -> int:
        state = self.get_state(merchant_id)
        state.ledger_revision += 1
        if commit:
            self.db.commit()
        return state.ledger_revision

    def bump_history_revision(self, merchant_id: str, *, commit: bool = True) -> int:
        state = self.get_state(merchant_id)
        state.history_revision += 1
        if commit:
            self.db.commit()
        return state.history_revision

    def mark_runs_stale(self, merchant_id: str, reason: str) -> int:
        result = self.db.execute(
            update(EnhancementRun)
            .where(
                EnhancementRun.merchant_id == merchant_id,
                EnhancementRun.is_stale.is_(False),
            )
            .values(is_stale=True, stale_reason=reason, updated_at=utcnow())
        )
        return int(result.rowcount or 0)

    # ------------------------------------------------------------------
    # 历史经营数据
    # ------------------------------------------------------------------
    def list_history(self, merchant_id: str, *, limit: int = 400) -> list[DailyCashHistory]:
        return list(
            self.db.scalars(
                select(DailyCashHistory)
                .where(DailyCashHistory.merchant_id == merchant_id)
                .order_by(DailyCashHistory.day.desc())
                .limit(limit)
            ).all()
        )

    def history_summary(self, merchant_id: str) -> dict[str, Any]:
        rows = list(
            self.db.scalars(
                select(DailyCashHistory)
                .where(DailyCashHistory.merchant_id == merchant_id)
                .order_by(DailyCashHistory.day.asc())
            ).all()
        )
        if not rows:
            return {
                "complete_days": 0,
                "first_day": None,
                "last_day": None,
                "total_inflow_cents": 0,
                "total_outflow_cents": 0,
                "missing_days": [],
                "continuity_warning": "还没有导入历史经营数据。",
                "required_days": WARMUP_MIN_DAYS,
            }

        days = [date.fromisoformat(row.day) for row in rows]
        missing: list[date] = []
        for previous, current in zip(days, days[1:], strict=False):
            cursor = previous + timedelta(days=1)
            while cursor < current:
                missing.append(cursor)
                if len(missing) >= 60:
                    break
                cursor += timedelta(days=1)

        warning = None
        if missing:
            warning = (
                f"历史日期不连续，缺少 {len(missing)} 天记录。"
                "缺失的日期不能当作没有收付，请补齐或明确确认。"
            )
        # 注意：本摘要会写进 JSON 列，日期必须提前序列化为 ISO 字符串
        return {
            "complete_days": sum(1 for row in rows if row.complete),
            "first_day": days[0].isoformat(),
            "last_day": days[-1].isoformat(),
            "total_inflow_cents": sum(int(row.inflow_cents) for row in rows),
            "total_outflow_cents": sum(int(row.outflow_cents) for row in rows),
            "missing_days": [item.isoformat() for item in missing],
            "continuity_warning": warning,
            "required_days": WARMUP_MIN_DAYS,
        }

    def upsert_history_rows(
        self,
        merchant_id: str,
        rows: Sequence[Any],
        *,
        completeness_confirmed: bool,
        fill_missing_days: bool,
        actor_id: str | None,
        import_batch_id: str | None = None,
    ) -> tuple[int, int, int]:
        """写入历史日数据。

        「缺失日当作 0」**只在** ``completeness_confirmed`` 为真时才允许，
        否则缺失的日期代表「不清楚」，绝不静默当作 0。
        """
        created = updated = filled = 0
        incoming = {row.day: row for row in rows}

        if completeness_confirmed and fill_missing_days and len(incoming) >= 2:
            ordered = sorted(incoming)
            for previous, current in zip(ordered, ordered[1:], strict=False):
                cursor = previous + timedelta(days=1)
                while cursor < current:
                    incoming.setdefault(
                        cursor,
                        type("_Filled", (), {
                            "day": cursor,
                            "inflow_cents": 0,
                            "outflow_cents": 0,
                            "source_label": "完整性确认：当日无收付",
                        })(),
                    )
                    filled += 1
                    cursor += timedelta(days=1)

        for day, row in incoming.items():
            existing = self.db.scalar(
                select(DailyCashHistory).where(
                    DailyCashHistory.merchant_id == merchant_id,
                    DailyCashHistory.day == day.isoformat(),
                )
            )
            refs = [getattr(row, "source_label", "") or "manual"]
            if existing is None:
                self.db.add(
                    DailyCashHistory(
                        merchant_id=merchant_id,
                        day=day.isoformat(),
                        complete=bool(getattr(row, "complete", True)),
                        inflow_cents=int(getattr(row, "inflow_cents", 0)),
                        outflow_cents=int(getattr(row, "outflow_cents", 0)),
                        source_refs=refs,
                        import_batch_id=import_batch_id,
                        completeness_confirmed=completeness_confirmed,
                        created_by=actor_id,
                    )
                )
                created += 1
            else:
                existing.inflow_cents = int(getattr(row, "inflow_cents", 0))
                existing.outflow_cents = int(getattr(row, "outflow_cents", 0))
                existing.complete = bool(getattr(row, "complete", True))
                existing.completeness_confirmed = completeness_confirmed
                existing.source_refs = list(dict.fromkeys([*(existing.source_refs or []), *refs]))
                updated += 1

        self.bump_history_revision(merchant_id, commit=False)
        self.mark_runs_stale(merchant_id, "历史经营数据已更新")
        return created, updated, filled

    # ------------------------------------------------------------------
    # 结算记录
    # ------------------------------------------------------------------
    def list_settlement_records(self, merchant_id: str) -> list[SettlementRecord]:
        return list(
            self.db.scalars(
                select(SettlementRecord)
                .where(SettlementRecord.merchant_id == merchant_id)
                .order_by(SettlementRecord.scheduled_at.desc())
            ).all()
        )

    def upsert_settlement_records(
        self,
        merchant_id: str,
        rows: Sequence[Any],
        *,
        actor_id: str | None,
    ) -> tuple[int, int, int]:
        created = updated = skipped = 0
        for row in rows:
            status = row.status
            if status == SETTLEMENT_COMPLETED and row.actual_at is None:
                raise ValidationFailed(
                    "已完成的结算记录必须填写实际到账时间",
                    code="SETTLEMENT_ACTUAL_REQUIRED",
                    details={"外部编号": row.external_key},
                )
            existing = self.db.scalar(
                select(SettlementRecord).where(
                    SettlementRecord.merchant_id == merchant_id,
                    SettlementRecord.external_key == row.external_key,
                )
            )
            known = row.known_at or utcnow()
            if existing is None:
                self.db.add(
                    SettlementRecord(
                        merchant_id=merchant_id,
                        external_key=row.external_key,
                        channel=row.channel,
                        scheduled_at=to_utc(row.scheduled_at),
                        actual_at=to_utc(row.actual_at) if row.actual_at else None,
                        known_at=to_utc(known),
                        status=status,
                        source_ref=row.source_ref or "manual",
                        note=row.note,
                        created_by=actor_id,
                    )
                )
                created += 1
            else:
                existing.channel = row.channel
                existing.scheduled_at = to_utc(row.scheduled_at)
                existing.actual_at = to_utc(row.actual_at) if row.actual_at else None
                existing.known_at = to_utc(known)
                existing.status = status
                existing.source_ref = row.source_ref or existing.source_ref
                existing.note = row.note
                updated += 1

        if created or updated:
            self.bump_history_revision(merchant_id, commit=False)
            self.mark_runs_stale(merchant_id, "结算记录已更新")
        return created, updated, skipped

    # ------------------------------------------------------------------
    # 依据装配
    # ------------------------------------------------------------------
    def _settlement_targets(self, merchant_id: str) -> list[CashEventInput]:
        rows = self.db.scalars(
            select(CashEvent).where(
                CashEvent.merchant_id == merchant_id,
                CashEvent.direction == DIRECTION_INFLOW,
                CashEvent.state == STATE_SCHEDULED,
                CashEvent.event_type == "settlement",
            )
        ).all()
        return [self.analysis.to_input(row) for row in rows]

    @staticmethod
    def _channel_of(event: CashEventInput) -> str:
        """结算渠道。当前以来源标注作为渠道标识；缺失时归入「未标注渠道」。"""
        return (event.source_label or "未标注渠道").strip() or "未标注渠道"

    def build_bundle(
        self,
        profile: MerchantProfile,
        *,
        reference_at: datetime | None = None,
        delay_days: int = DEFAULT_MANUAL_DELAY_DAYS,
    ) -> EnhancementBundle:
        reference = self.analysis._reference_time(profile, reference_at)
        opening = self.analysis._opening_balance(profile)
        buffer = int(profile.default_buffer_amount_cents)
        base_events = [self.analysis.to_input(event) for event in self.analysis.load_events(profile.id)]

        baseline = EngineInput(
            opening_balance_cents=opening,
            buffer_cents=buffer,
            snapshot_at=reference,
            events=base_events,
            label="按当前计划",
        )

        targets = self._settlement_targets(profile.id)
        pairs = [
            SettlementPair(
                id=row.id,
                channel=row.channel,
                scheduled_at=row.scheduled_at,
                known_at=row.known_at,
                status=row.status,
                source_ref=row.source_ref,
                actual_at=row.actual_at,
            )
            for row in self.list_settlement_records(profile.id)
        ]

        history_reason: str | None = None
        try:
            history_rows = rows_from_records(self.list_history(profile.id, limit=400))
        except (ForecastError, ValueError) as error:  # pragma: no cover - 数据异常兜底
            history_rows = []
            history_reason = str(error)

        state = self.get_state(profile.id)
        basis = {
            "ledger_revision": state.ledger_revision,
            "history_revision": state.history_revision,
            "opening_balance_cents": opening,
            "buffer_cents": buffer,
            "snapshot_at": reference,
            "delay_days": delay_days,
            "events": [item.fingerprint() for item in base_events],
            "settlement_records": [
                f"{row.id}|{row.status}|{row.scheduled_at}|{row.actual_at}|{row.channel}"
                for row in self.list_settlement_records(profile.id)
            ],
            "history": [f"{row.day}|{row.inflow_cents}|{row.outflow_cents}" for row in history_rows],
        }
        return EnhancementBundle(
            baseline_engine_input=baseline,
            settlement_targets=tuple(targets),
            settlement_pairs=tuple(pairs),
            history_rows=tuple(history_rows),
            history_incomplete_reason=history_reason,
            current_reserve_cents=buffer,
            ledger_revision=state.ledger_revision,
            history_revision=state.history_revision,
            basis_hash=_hash_payload(basis),
        )

    # ------------------------------------------------------------------
    # 计算
    # ------------------------------------------------------------------
    def _forecast_out(
        self, rows: Sequence[DailyCash], history_days: int
    ) -> tuple[ForecastOut, list[ErrorBlock], str | None]:
        """运行预测与留底误差窗口。预测结果绝不进入确定性账本。"""
        if not rows:
            return (
                ForecastOut(
                    available=False,
                    reason_code="HISTORY_REQUIRED",
                    message=(
                        "还没有历史经营数据。今日可提用金额、现金事件、情景分析、"
                        "家庭协同与经营咨询都照常可用。"
                    ),
                    history_days=0,
                    required_days=WARMUP_MIN_DAYS,
                    forecast_affects_withdrawable=False,
                ),
                [],
                None,
            )
        try:
            result: ForecastResult = evaluate_forecasts(list(rows))
        except ForecastError as error:
            unavailable = unavailable_from_error(error, len(rows))
            return (
                ForecastOut(
                    available=False,
                    reason_code=unavailable.reason_code,
                    message=unavailable.message,
                    history_days=unavailable.history_days,
                    required_days=unavailable.required_days,
                    forecast_affects_withdrawable=False,
                ),
                [],
                None,
            )

        blocks: list[ErrorBlock] = []
        blocks_error: str | None = None
        try:
            income_bt = _backtest_for(result, rows, "inflow")
            expense_bt = _backtest_for(result, rows, "outflow")
            blocks = buffer_blocks(income_bt, expense_bt)
        except ForecastError as error:
            blocks_error = error.message

        payload = result.to_dict()
        return (
            ForecastOut(
                available=True,
                history_days=result.history_days,
                required_days=WARMUP_MIN_DAYS,
                training_end=result.training_end,
                needs_review=result.needs_review,
                needs_review_reason=result.needs_review_reason,
                method_inflow=result.method_inflow,
                method_inflow_label=payload["method_inflow_label"],
                method_outflow=result.method_outflow,
                method_outflow_label=payload["method_outflow_label"],
                daily=payload["daily"],
                summary=result.summary,
                diagnostics={**result.diagnostics, "blocks_error": blocks_error},
                source_refs=list(result.source_refs),
                disclosure=result.disclosure,
                forecast_affects_withdrawable=False,
            ),
            blocks,
            blocks_error,
        )

    def _settlement_pressure_out(
        self,
        profile: MerchantProfile,
        bundle: EnhancementBundle,
        *,
        delay_days: int,
        delay_quantile: float,
        channel: str | None = None,
    ) -> tuple[SettlementPressureOut, list[dict[str, Any]]]:
        targets = bundle.settlement_targets
        if not targets:
            return (
                SettlementPressureOut(
                    available=False,
                    message=(
                        "当前没有计划中的结算款，暂时不需要做延期压力分析。"
                        "补录结算款后这里会自动出现。"
                    ),
                    manual_delay_days=delay_days,
                    disclosure=(
                        "延期压力分析只针对已确认、计划中的结算款；"
                        "历史参考不是未来到账概率。"
                    ),
                ),
                [],
            )

        reference = bundle.baseline_engine_input.snapshot_at
        available_channels: list[str] = []
        for target in targets:
            item = self._channel_of(target)
            if item not in available_channels:
                available_channels.append(item)
        # 历史结算记录与现金事件是两套数据源，渠道名称可能不一致：
        # 必须允许显式指定渠道，缺省时才回退到第一笔结算款的来源标注。
        selected_channel = (
            channel.strip() if channel and channel.strip() else available_channels[0]
        )

        stats: SettlementStats | None = None
        if bundle.settlement_pairs:
            try:
                stats = settlement_stats(
                    bundle.settlement_pairs,
                    channel=selected_channel,
                    as_of=reference,
                    quantile=delay_quantile,
                )
            except ForecastError:
                stats = None

        # 延期目标 = 全部已确认的计划中结算款。
        # 渠道只用于筛选「历史结算记录」样本，不用来决定哪些款可以被延后：
        # 否则渠道名称仅因来源标注不同就会让压力分析整块消失。
        selected = list(targets)
        try:
            scenarios: list[DelayScenario] = build_delay_scenarios(
                selected, manual_delay_days=delay_days, stats=stats
            )
        except ForecastError as error:
            return (
                SettlementPressureOut(
                    available=False,
                    message=error.message,
                    manual_delay_days=delay_days,
                    disclosure="延期压力分析只针对已确认、计划中的结算款。",
                ),
                [],
            )

        base_events = list(bundle.baseline_engine_input.events)
        target_ids = {item.id for item in selected}
        scenario_outputs: list[SettlementPressureScenarioOut] = []
        scenario_payloads: list[dict[str, Any]] = []
        for scenario in scenarios:
            overrides = scenario.time_overrides or {}
            events = [
                item
                if item.id not in target_ids
                else CashEventInput(
                    id=item.id,
                    cash_key=item.cash_key,
                    title=item.title,
                    amount_cents=item.amount_cents,
                    direction=item.direction,
                    scheduled_at=overrides.get(item.id, item.scheduled_at),
                    state=item.state,
                    event_type=item.event_type,
                    sequence_index=item.sequence_index,
                    source_label=item.source_label,
                    current_version=item.current_version,
                    confirmed=item.confirmed,
                )
                for item in base_events
            ]
            result = run_engine(
                EngineInput(
                    opening_balance_cents=bundle.baseline_engine_input.opening_balance_cents,
                    buffer_cents=bundle.baseline_engine_input.buffer_cents,
                    snapshot_at=reference,
                    events=events,
                    label=scenario.label,
                )
            )
            scenario_outputs.append(
                SettlementPressureScenarioOut(
                    id=scenario.id,
                    label=scenario.label,
                    delay_days=scenario.delay_days,
                    basis=scenario.basis,
                    status=str(result.status),
                    status_label=result.status_label,
                    max_withdrawable_cents=result.max_withdrawable_cents,
                    payment_gap_cents=result.payment_gap_cents,
                    buffer_gap_cents=result.buffer_gap_cents,
                    minimum_balance_cents=result.minimum_balance_cents,
                    end_balance_cents=result.end_balance_cents,
                    limiting_timestamp=result.limiting_timestamp,
                    limiting_event_title=result.limiting_event_title,
                    points=[point.to_dict() for point in result.points],
                    source_refs=list(scenario.source_refs),
                )
            )
            scenario_payloads.append(
                {
                    "id": scenario.id,
                    "label": scenario.label,
                    "delay_days": scenario.delay_days,
                    "status": str(result.status),
                    "max_withdrawable_cents": result.max_withdrawable_cents,
                    "payment_gap_cents": result.payment_gap_cents,
                    "buffer_gap_cents": result.buffer_gap_cents,
                }
            )

        stats_out = (
            SettlementPressureStatsOut(
                channel=stats.channel,
                completed_count=stats.completed_count,
                open_count=stats.open_count,
                overdue_open_count=stats.overdue_open_count,
                status=stats.status,
                quantile=stats.quantile,
                min_samples=stats.min_samples,
                empirical_delay_days=stats.empirical_delay_days,
                has_sample=stats.has_sample,
                headline=stats.headline,
                source_refs=list(stats.source_refs),
                open_source_refs=list(stats.open_source_refs),
                disclosure=stats.disclosure,
            )
            if stats is not None
            else None
        )

        message = None
        if stats is not None and not stats.has_sample:
            message = stats.headline

        targets_out = [
            {
                "id": item.id,
                "title": item.title,
                "amount_cents": item.amount_cents,
                "amount_text": _yuan(item.amount_cents),
                "scheduled_at": item.scheduled_at.isoformat() if item.scheduled_at else None,
                "channel": self._channel_of(item),
                "source_label": item.source_label,
                "event_type_label": EVENT_TYPE_LABELS.get(item.event_type, item.event_type),
            }
            for item in selected
        ]

        return (
            SettlementPressureOut(
                available=True,
                message=message,
                manual_delay_days=delay_days,
                targets=targets_out,
                scenarios=scenario_outputs,
                stats=stats_out,
                disclosure=(
                    "历史已完成结算记录中的经验延迟参考，只统计 completed 样本；"
                    "未完成项单独计数，不能解释为未来到账概率，也不推断银行 T+1 或节假日规则。"
                ),
            ),
            scenario_payloads,
        )

    def overview(
        self,
        profile: MerchantProfile,
        *,
        reference_at: datetime | None = None,
        delay_days: int = DEFAULT_MANUAL_DELAY_DAYS,
        delay_quantile: float = DEFAULT_DELAY_QUANTILE,
        reserve_quantile: float = DEFAULT_RESERVE_QUANTILE,
        reserve_rounding_cents: int = DEFAULT_ROUNDING_CENTS,
        settlement_channel: str | None = None,
        actor_id: str | None = None,
        persist: bool = True,
    ) -> EnhancementOverviewOut:
        if delay_days < 0 or delay_days > MAX_MANUAL_DELAY_DAYS:
            raise ValidationFailed(
                f"延期天数需在 0 到 {MAX_MANUAL_DELAY_DAYS} 个自然日之间",
                code="DELAY_LIMIT",
            )

        bundle = self.build_bundle(profile, reference_at=reference_at, delay_days=delay_days)

        baseline_result = run_engine(bundle.baseline_engine_input)
        assessment = run_engine(
            EngineInput(
                opening_balance_cents=bundle.baseline_engine_input.opening_balance_cents,
                buffer_cents=bundle.baseline_engine_input.buffer_cents,
                snapshot_at=bundle.baseline_engine_input.snapshot_at,
                events=bundle.baseline_engine_input.events,
                label="按当前计划",
            )
        )

        forecast_out, blocks, blocks_error = self._forecast_out(
            bundle.history_rows, len(bundle.history_rows)
        )
        reserve_advice = recommend_reserve(
            blocks,
            current_reserve_cents=bundle.current_reserve_cents,
            quantile=reserve_quantile,
            rounding_cents=reserve_rounding_cents,
            basis_hash=bundle.basis_hash,
        )
        pressure_out, scenario_payloads = self._settlement_pressure_out(
            profile,
            bundle,
            delay_days=delay_days,
            delay_quantile=delay_quantile,
            channel=settlement_channel,
        )
        history_payload = self.history_summary(profile.id)

        parameters = {
            **DEFAULT_PARAMETERS,
            "delay_days": delay_days,
            "delay_quantile": delay_quantile,
            "reserve_quantile": reserve_quantile,
            "reserve_rounding_cents": reserve_rounding_cents,
        }
        result_payload = {
            "baseline": {
                "status": str(baseline_result.status),
                "max_withdrawable_cents": baseline_result.max_withdrawable_cents,
                "payment_gap_cents": baseline_result.payment_gap_cents,
                "buffer_gap_cents": baseline_result.buffer_gap_cents,
            },
            "settlement_pressure": {
                "available": pressure_out.available,
                "scenarios": scenario_payloads,
                "stats": pressure_out.stats.model_dump() if pressure_out.stats else None,
            },
            "reserve_advice": reserve_advice.to_dict(),
            "history": history_payload,
            "assessment": {
                "status": str(assessment.status),
                "max_withdrawable_cents": assessment.max_withdrawable_cents,
                "minimum_balance_cents": assessment.minimum_balance_cents,
            },
            "blocks_error": blocks_error,
        }

        run_id: str | None = None
        if persist:
            self.mark_runs_stale(profile.id, "已生成新的增强结果")
            run = EnhancementRun(
                merchant_id=profile.id,
                ledger_revision=bundle.ledger_revision,
                history_revision=bundle.history_revision,
                basis_hash=bundle.basis_hash,
                parameters_json=parameters,
                result_json=result_payload,
                is_stale=False,
                created_by=actor_id,
            )
            self.db.add(run)
            self.db.commit()
            self.db.refresh(run)
            run_id = run.id

        return EnhancementOverviewOut(
            merchant_id=profile.id,
            ledger_revision=bundle.ledger_revision,
            history_revision=bundle.history_revision,
            basis_hash=bundle.basis_hash,
            generated_at=utcnow(),
            needs_review=forecast_out.needs_review,
            baseline={
                "status": str(baseline_result.status),
                "status_label": baseline_result.status_label,
                "max_withdrawable_cents": baseline_result.max_withdrawable_cents,
                "payment_gap_cents": baseline_result.payment_gap_cents,
                "buffer_gap_cents": baseline_result.buffer_gap_cents,
                "minimum_balance_cents": baseline_result.minimum_balance_cents,
                "end_balance_cents": baseline_result.end_balance_cents,
                "limiting_timestamp": baseline_result.limiting_timestamp,
                "limiting_event_title": baseline_result.limiting_event_title,
                "buffer_cents": baseline_result.buffer_cents,
                "opening_balance_cents": baseline_result.opening_balance_cents,
            },
            settlement_pressure=pressure_out,
            forecast=forecast_out,
            reserve_advice=ReserveAdviceOut(
                **{
                    **reserve_advice.to_dict(),
                    "basis": reserve_advice.basis_dict(),
                }
            ),
            history=history_payload,
            reserve_changed_since_advice=(
                reserve_advice.suggested_reserve_cents != bundle.current_reserve_cents
                and reserve_advice.has_suggestion
                and reserve_advice.suggests_increase
            ),
            run_id=run_id,
            forecast_affects_withdrawable=False,
        )

    # ------------------------------------------------------------------
    # 留底确认
    # ------------------------------------------------------------------
    def confirm_reserve(
        self,
        profile: MerchantProfile,
        *,
        suggested_reserve_cents: int,
        basis_hash: str,
        ledger_revision: int,
        history_revision: int,
        actor_id: str,
    ) -> ReserveConfirmOut:
        """确认采用建议留底。

        后端必须重新校验版本与依据；任一变化返回 409 ``STALE_RESERVE_ADVICE``。
        绝不自动降低当前留底。
        """
        current = self.overview(profile, persist=False)
        # 先判断依据是否过期：版本或 basis 变化时，连「建议值是多少」都已经不可信，
        # 必须要求用户重新查看，而不是拿旧建议和新依据比较。
        if (
            current.basis_hash != basis_hash
            or current.ledger_revision != ledger_revision
            or current.history_revision != history_revision
        ):
            raise Conflict(STALE_RESERVE_MESSAGE, code=STALE_RESERVE_ADVICE)
        if current.reserve_advice.suggested_reserve_cents != suggested_reserve_cents:
            raise Conflict(STALE_RESERVE_MESSAGE, code=STALE_RESERVE_ADVICE)

        previous = int(profile.default_buffer_amount_cents)
        if suggested_reserve_cents < previous:
            raise ValidationFailed(
                "不能通过确认建议来降低经营留底",
                code="NO_AUTO_LOWERING",
                details={"current": previous, "suggested": suggested_reserve_cents},
            )

        profile.default_buffer_amount_cents = int(suggested_reserve_cents)
        confirmation = ReserveAdviceConfirmation(
            merchant_id=profile.id,
            previous_reserve_cents=previous,
            suggested_reserve_cents=int(suggested_reserve_cents),
            confirmed_reserve_cents=int(suggested_reserve_cents),
            basis_hash=basis_hash,
            ledger_revision=ledger_revision,
            history_revision=history_revision,
            basis_json=current.reserve_advice.basis,
            confirmed_by=actor_id,
            confirmed_at=utcnow(),
        )
        self.db.add(confirmation)

        # 提高留底属于账本口径变化：所有旧结果失效并重新计算
        state = self.get_state(profile.id)
        state.history_revision += 1
        stale_count = self.analysis.mark_stale(
            profile.id, reason="经营留底已更新，需按新留底重新计算", commit=False
        )
        self.mark_runs_stale(profile.id, "经营留底已更新")
        self.db.commit()

        rerun = self.analysis.run(
            profile, AnalysisRunRequest(mode="current_plan"), actor_id=actor_id, persist=True
        )
        confirmation.analysis_result_id = rerun.id
        self.db.commit()

        return ReserveConfirmOut(
            confirmed_reserve_cents=int(suggested_reserve_cents),
            previous_reserve_cents=previous,
            history_revision=state.history_revision,
            ledger_revision=state.ledger_revision,
            stale_analysis_results=stale_count,
            message=(
                f"经营留底已更新为 {_yuan(suggested_reserve_cents)}，"
                "相关资金结果已经重新计算。"
            ),
            analysis=rerun.model_dump(mode="json"),
        )


def _backtest_for(result: ForecastResult, rows: Sequence[DailyCash], field_name: str):
    """按已选方法对指定字段重新做一次滚动检验（用于留底误差窗口）。"""
    method = result.method_inflow if field_name == "inflow" else result.method_outflow
    return rolling_backtest(rows, method=method, field_name=field_name)  # type: ignore[arg-type]


__all__ = [
    "DEFAULT_PARAMETERS",
    "STALE_RESERVE_ADVICE",
    "STALE_RESERVE_MESSAGE",
    "EnhancementBundle",
    "EnhancementService",
]
