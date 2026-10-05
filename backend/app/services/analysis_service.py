"""资金分析服务：装配引擎输入、持久化结果、结果失效标记。

计算全部委托给 :mod:`app.services.cash_engine`。本模块只负责：
* 从数据库读取期初余额、留底、现金事件
* 组装 :class:`~app.services.cash_engine.EngineInput`
* 把结果持久化为可追溯的 ``AnalysisResult``
* 在任何影响金额计算的修改之后把旧结果标记为 ``stale``
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import NotFound, ValidationFailed
from app.models.cash import (
    AnalysisResult,
    CashEvent,
    Scenario,
    ScenarioEventOverride,
)
from app.models.merchant import MerchantProfile
from app.schemas.analysis import (
    AnalysisResultOut,
    AnalysisRunRequest,
    ArrivalTerm,
    CategoryTerm,
    DailyTerm,
    ScenarioCurve,
    ScenarioOut,
    WindowSummary,
)
from app.services.cash_engine import (
    ENGINE_VERSION,
    STATUS_LABELS,
    AnalysisStatus,
    CashEventInput,
    EngineInput,
    EngineResult,
    JointResult,
    ResolvedAnalysis,
    events_version_hash,
    participating_events,
    resolve_analysis,
    run_engine,
    run_joint,
)
from app.utils.timeutil import APP_TIMEZONE, WINDOW_DAYS, to_utc, utcnow

MODE_CURRENT_PLAN = "current_plan"
MODE_DELAYED = "delayed"
MODE_JOINT = "joint"

#: 聚合接口（图表窗口汇总）默认的到账延迟天数。
#: 与「今日决策」页默认展示的到账延迟口径保持一致。
DEFAULT_DELAY_DAYS = 2

STALE_REASON_DEFAULT = "收付款事项或资金时点已变更"

#: 事项类型中文标签（与前端 labels 保持一致，后端也需要用于图表分组）
EVENT_TYPE_LABELS = {
    "settlement": "结算款",
    "sale_receipt": "销售收款",
    "supplier_payment": "进货款",
    "rent": "房租",
    "refund": "退款",
    "payroll": "工资",
    "utility": "水电",
    "tax": "税款",
    "loan_repayment": "还款",
    "platform_fee": "平台费用",
    "transfer_in": "转入",
    "transfer_out": "转出",
    "other_inflow": "其他收入",
    "other_outflow": "其他支出",
}


def _scenario_kind_label(kind: str) -> str:
    return {
        MODE_CURRENT_PLAN: "按当前计划",
        MODE_DELAYED: "到账延迟",
        MODE_JOINT: "共同约束",
        "custom": "自定义情景",
    }.get(kind, kind)


class AnalysisService:
    def __init__(self, db: Session) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # 输入装配
    # ------------------------------------------------------------------
    def load_events(self, merchant_id: str) -> list[CashEvent]:
        rows = self.db.scalars(
            select(CashEvent)
            .where(CashEvent.merchant_id == merchant_id)
            .order_by(CashEvent.scheduled_at.asc(), CashEvent.cash_key.asc())
        ).all()
        return list(rows)

    @staticmethod
    def to_input(event: CashEvent) -> CashEventInput:
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

    def _reference_time(self, profile: MerchantProfile, requested: datetime | None) -> datetime:
        if requested is not None:
            return to_utc(requested)
        from app.repositories.merchant_repo import AccountSnapshotRepository

        snapshot = AccountSnapshotRepository(self.db).latest(profile.id)
        return snapshot.snapshot_at if snapshot else utcnow()

    def _opening_balance(self, profile: MerchantProfile) -> int:
        from app.repositories.merchant_repo import AccountSnapshotRepository

        snapshot = AccountSnapshotRepository(self.db).latest(profile.id)
        return snapshot.opening_balance_cents if snapshot else 0

    def build_inputs(
        self,
        profile: MerchantProfile,
        *,
        mode: str,
        snapshot_at: datetime | None = None,
        scenario_ids: list[str] | None = None,
        delay_days: int = 3,
        buffer_cents: int | None = None,
    ) -> list[EngineInput]:
        reference = self._reference_time(profile, snapshot_at)
        opening = self._opening_balance(profile)
        buffer = (
            int(buffer_cents)
            if buffer_cents is not None
            else int(profile.default_buffer_amount_cents)
        )
        base_events = [self.to_input(event) for event in self.load_events(profile.id)]

        if mode == MODE_CURRENT_PLAN:
            return [
                EngineInput(
                    opening_balance_cents=opening,
                    buffer_cents=buffer,
                    snapshot_at=reference,
                    events=base_events,
                    label=_scenario_kind_label(MODE_CURRENT_PLAN),
                )
            ]

        if mode == MODE_DELAYED:
            return [
                EngineInput(
                    opening_balance_cents=opening,
                    buffer_cents=buffer,
                    snapshot_at=reference,
                    events=self._delay_inflows(base_events, delay_days),
                    label=_scenario_kind_label(MODE_DELAYED),
                )
            ]

        if mode == MODE_JOINT:
            return [
                EngineInput(
                    opening_balance_cents=opening,
                    buffer_cents=buffer,
                    snapshot_at=reference,
                    events=base_events,
                    label=_scenario_kind_label(MODE_CURRENT_PLAN),
                ),
                EngineInput(
                    opening_balance_cents=opening,
                    buffer_cents=buffer,
                    snapshot_at=reference,
                    events=self._delay_inflows(base_events, delay_days),
                    label=_scenario_kind_label(MODE_DELAYED),
                ),
            ]

        if mode == "scenarios":
            inputs: list[EngineInput] = []
            for scenario in self._load_scenarios(profile.id, scenario_ids):
                inputs.append(self._scenario_input(scenario, opening, buffer, reference))
            if not inputs:
                raise ValidationFailed("请至少选择一个情景", code="NO_SCENARIO")
            return inputs

        raise ValidationFailed("不支持的分析模式", code="UNSUPPORTED_MODE")

    @staticmethod
    def _delay_inflows(events: list[CashEventInput], delay_days: int) -> list[CashEventInput]:
        shift = timedelta(days=max(0, int(delay_days)))
        shifted: list[CashEventInput] = []
        for event in events:
            if event.direction == "inflow" and event.scheduled_at is not None:
                shifted.append(
                    CashEventInput(
                        id=event.id,
                        cash_key=event.cash_key,
                        title=event.title,
                        amount_cents=event.amount_cents,
                        direction=event.direction,
                        scheduled_at=to_utc(event.scheduled_at) + shift,
                        state=event.state,
                        event_type=event.event_type,
                        sequence_index=event.sequence_index,
                        source_label=event.source_label,
                        current_version=event.current_version,
                        confirmed=event.confirmed,
                    )
                )
            else:
                shifted.append(event)
        return shifted

    def _load_scenarios(self, merchant_id: str, scenario_ids: list[str] | None) -> list[Scenario]:
        statement = select(Scenario).where(Scenario.merchant_id == merchant_id)
        if scenario_ids:
            statement = statement.where(Scenario.id.in_(scenario_ids))
        statement = statement.order_by(Scenario.created_at.asc())
        return list(self.db.scalars(statement).all())

    def _scenario_input(
        self,
        scenario: Scenario,
        opening: int,
        buffer: int,
        reference: datetime,
    ) -> EngineInput:
        base = [self.to_input(event) for event in self.load_events(scenario.merchant_id)]
        by_id = {item.id: item for item in base}
        overrides = self.db.scalars(
            select(ScenarioEventOverride).where(ScenarioEventOverride.scenario_id == scenario.id)
        ).all()
        for override in overrides:
            current = by_id.get(override.cash_event_id)
            if current is None:
                continue
            by_id[override.cash_event_id] = CashEventInput(
                id=current.id,
                cash_key=current.cash_key,
                title=current.title,
                amount_cents=(
                    override.amount_cents_override
                    if override.amount_cents_override is not None
                    else current.amount_cents
                ),
                direction=override.direction_override or current.direction,
                scheduled_at=(
                    to_utc(override.scheduled_at_override)
                    if override.scheduled_at_override is not None
                    else current.scheduled_at
                ),
                state=override.state_override or current.state,
                event_type=current.event_type,
                sequence_index=current.sequence_index,
                source_label=current.source_label,
                current_version=current.current_version,
                confirmed=current.confirmed,
            )
        return EngineInput(
            opening_balance_cents=opening,
            buffer_cents=buffer,
            snapshot_at=reference,
            events=list(by_id.values()),
            label=scenario.name,
        )

    # ------------------------------------------------------------------
    # 执行
    # ------------------------------------------------------------------
    def run(
        self,
        profile: MerchantProfile,
        payload: AnalysisRunRequest,
        *,
        actor_id: str | None = None,
        persist: bool = True,
    ) -> AnalysisResultOut:
        inputs = self.build_inputs(
            profile,
            mode=payload.mode,
            snapshot_at=payload.snapshot_at,
            scenario_ids=payload.scenario_ids,
            delay_days=payload.delay_days,
            buffer_cents=payload.buffer_cents,
        )

        if payload.mode == MODE_JOINT:
            joint: JointResult = run_joint(inputs)
            results = joint.scenario_results
        else:
            joint = None
            results = [run_engine(item) for item in inputs]

        # 唯一权威结果：API 输出、数据库落库与家庭分享全部从它派生。
        resolved = resolve_analysis(mode=payload.mode, engine_results=results, joint=joint)

        payload_dict = {
            "mode": payload.mode,
            "max_withdrawable_cents": resolved.max_withdrawable_cents,
            "binding_label": resolved.binding_label,
            "binding_scenario_index": resolved.binding_scenario_index,
            "limiting_event_title": resolved.limiting_event_title,
            "payment_gap_cents": resolved.payment_gap_cents,
            "buffer_gap_cents": resolved.buffer_gap_cents,
            "scenarios": [item.to_dict() for item in results],
        }

        persisted: AnalysisResult | None = None
        if persist:
            persisted = self._persist(profile, resolved, payload, payload_dict, actor_id)

        return self._to_out(profile, results, payload, resolved, persisted)


    def _persist(
        self,
        profile: MerchantProfile,
        resolved: ResolvedAnalysis,
        payload: AnalysisRunRequest,
        payload_dict: dict,
        actor_id: str | None,
    ) -> AnalysisResult:
        """落库 ``ResolvedAnalysis``。

        共同约束模式下**不能**保存 ``scenarios[0]`` 或 ``primary``：
        那会把「按当前计划」的状态与缺口写进结果，而用户看到的共同结论可能来自
        另一个情景。这里保存的每个字段都来自 :func:`resolve_analysis` 选定的绑定情景。
        """
        from app.repositories.merchant_repo import AccountSnapshotRepository

        self.mark_stale(profile.id, reason="已生成新的分析结果", commit=False)

        snapshot = AccountSnapshotRepository(self.db).latest(profile.id)
        row = AnalysisResult(
            merchant_id=profile.id,
            snapshot_id=snapshot.id if snapshot else None,
            scenario_id=None,
            scenario_ids=list(payload.scenario_ids or []),
            mode=payload.mode,
            status=str(resolved.status),
            max_withdrawable_cents=resolved.max_withdrawable_cents,
            opening_balance_cents=resolved.opening_balance_cents,
            buffer_cents=resolved.buffer_cents,
            snapshot_at=resolved.snapshot_at,
            window_end_at=resolved.window_end_at,
            limiting_timestamp=resolved.limiting_timestamp,
            limiting_balance_cents=resolved.limiting_balance_cents,
            limiting_event_id=resolved.limiting_event_id,
            payment_gap_cents=resolved.payment_gap_cents,
            buffer_gap_cents=resolved.buffer_gap_cents,
            payload=payload_dict,
            events_version_hash=self._version_hash(profile.id),
            is_stale=False,
            engine_version=ENGINE_VERSION,
            created_by=actor_id,
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def _version_hash(self, merchant_id: str) -> str:
        return events_version_hash([self.to_input(event) for event in self.load_events(merchant_id)])

    # ------------------------------------------------------------------
    def window_summary(
        self,
        profile: MerchantProfile,
        *,
        mode: str = MODE_CURRENT_PLAN,
        delay_days: int = DEFAULT_DELAY_DAYS,
        scenario_ids: list[str] | None = None,
        snapshot_at: datetime | None = None,
        buffer_cents: int | None = None,
    ) -> WindowSummary:
        """聚合未来 7 天窗口内的分析数据（供图表使用）。

        口径由 ``mode`` 决定，且**完全复用引擎输入**：

        * ``current_plan``：按已确认的计划事项；
        * ``delayed``：收入按 ``delay_days`` 推后到账；
        * ``joint``：取 :func:`resolve_analysis` 选出的**绑定情景**
          （真正最保守的那个），而不是固定取第一个情景；
        * ``scenarios``：按用户选中的自定义情景（与情景分析页保持一致）。

        本方法是纯读取：不创建 ``AnalysisResult``、不写库、不产生审计。
        """
        inputs = self.build_inputs(
            profile,
            mode=mode,
            snapshot_at=snapshot_at,
            scenario_ids=scenario_ids,
            delay_days=delay_days,
            buffer_cents=buffer_cents,
        )

        if mode == MODE_JOINT:
            joint: JointResult = run_joint(inputs)
            results = joint.scenario_results
            resolved = resolve_analysis(mode=mode, engine_results=results, joint=joint)
            binding_index = resolved.binding_scenario_index or 0
        else:
            results = [run_engine(item) for item in inputs]
            resolved = resolve_analysis(mode=mode, engine_results=results)
            binding_index = resolved.binding_scenario_index or 0

        selected = inputs[binding_index]
        reference = to_utc(selected.snapshot_at)
        window_days = int(selected.window_days)
        window_end = reference + timedelta(days=window_days)
        opening = int(selected.opening_balance_cents)
        buffer = int(selected.buffer_cents)

        # 只聚合引擎真正参与推演、且落在窗口内的事项：与顶部结论同源。
        events = [
            event
            for event in participating_events(selected.events)
            if reference <= to_utc(event.scheduled_at) <= window_end
        ]

        # 逐日聚合：以当地时间为准归日，保证与经营者看到的日期一致
        days: dict[date, dict[str, int]] = {}
        cursor = reference.astimezone(APP_TIMEZONE).date()
        last_day = window_end.astimezone(APP_TIMEZONE).date()
        while cursor <= last_day:
            days[cursor] = {
                "inflow_cents": 0,
                "outflow_cents": 0,
                "net_cents": 0,
                "closing_balance_cents": 0,
                "event_count": 0,
            }
            cursor += timedelta(days=1)

        category_acc: dict[tuple[str, str], dict[str, int]] = {}
        arrival_acc: dict[date, dict[str, object]] = {}

        for event in events:
            when = to_utc(event.scheduled_at)
            local_day = when.astimezone(APP_TIMEZONE).date()
            bucket = days.setdefault(
                local_day,
                {
                    "inflow_cents": 0,
                    "outflow_cents": 0,
                    "net_cents": 0,
                    "closing_balance_cents": 0,
                    "event_count": 0,
                },
            )
            amount = int(event.amount_cents)
            if event.direction == "inflow":
                bucket["inflow_cents"] += amount
                bucket["net_cents"] += amount
                arrival = arrival_acc.setdefault(local_day, {"amount_cents": 0, "titles": []})
                arrival["amount_cents"] = int(arrival["amount_cents"]) + amount
                titles = arrival["titles"]
                if isinstance(titles, list) and len(titles) < 5:
                    titles.append(event.title)
            else:
                bucket["outflow_cents"] += amount
                bucket["net_cents"] -= amount
            bucket["event_count"] += 1

            key = (event.event_type, event.direction)
            acc = category_acc.setdefault(key, {"amount_cents": 0, "event_count": 0})
            acc["amount_cents"] += amount
            acc["event_count"] += 1

        # 期初之后的每日期末余额
        running = opening
        daily_terms: list[DailyTerm] = []
        for day, bucket in sorted(days.items()):
            running += bucket["net_cents"]
            daily_terms.append(
                DailyTerm(
                    day=day,
                    inflow_cents=bucket["inflow_cents"],
                    outflow_cents=bucket["outflow_cents"],
                    net_cents=bucket["net_cents"],
                    closing_balance_cents=running,
                    event_count=bucket["event_count"],
                )
            )

        inflow_total = sum(item.inflow_cents for item in daily_terms)
        outflow_total = sum(item.outflow_cents for item in daily_terms)

        category_terms = [
            CategoryTerm(
                event_type=event_type,
                label=EVENT_TYPE_LABELS.get(event_type, event_type),
                direction=direction,
                amount_cents=values["amount_cents"],
                event_count=values["event_count"],
                share_ratio=round(
                    values["amount_cents"]
                    / (inflow_total if direction == "inflow" else outflow_total)
                    if (inflow_total if direction == "inflow" else outflow_total)
                    else 0.0,
                    4,
                ),
            )
            for (event_type, direction), values in sorted(
                category_acc.items(), key=lambda item: -item[1]["amount_cents"]
            )
        ]

        arrival_terms = [
            ArrivalTerm(
                day=day,
                amount_cents=int(values["amount_cents"]),
                event_count=len(values["titles"]) if isinstance(values["titles"], list) else 0,
                titles=list(values["titles"]) if isinstance(values["titles"], list) else [],
            )
            for day, values in sorted(arrival_acc.items())
        ]

        return WindowSummary(
            mode=mode,
            delay_days=int(delay_days),
            status=str(resolved.status),
            status_label=resolved.status_label,
            binding_scenario_index=resolved.binding_scenario_index,
            binding_label=resolved.binding_label,
            window_start=reference,
            window_end=window_end,
            window_days=window_days,
            opening_balance_cents=opening,
            closing_balance_cents=running,
            buffer_cents=buffer,
            scheduled_inflow_cents=inflow_total,
            scheduled_outflow_cents=outflow_total,
            net_change_cents=inflow_total - outflow_total,
            daily_terms=daily_terms,
            category_terms=category_terms,
            arrival_terms=arrival_terms,
            event_count=len(events),
        )

    # ------------------------------------------------------------------
    def mark_stale(
        self,
        merchant_id: str,
        *,
        reason: str = STALE_REASON_DEFAULT,
        commit: bool = True,
    ) -> int:
        """把该商户所有未失效的分析结果标记为 stale。"""
        result = self.db.execute(
            update(AnalysisResult)
            .where(AnalysisResult.merchant_id == merchant_id, AnalysisResult.is_stale.is_(False))
            .values(is_stale=True, stale_reason=reason, updated_at=utcnow())
        )
        if commit:
            self.db.commit()
        return int(result.rowcount or 0)

    def latest_result(self, merchant_id: str) -> AnalysisResult | None:
        return self.db.scalar(
            select(AnalysisResult)
            .where(AnalysisResult.merchant_id == merchant_id)
            .order_by(AnalysisResult.created_at.desc())
            .limit(1)
        )

    def is_latest_stale(self, merchant_id: str) -> bool:
        latest = self.latest_result(merchant_id)
        if latest is None:
            return False
        if latest.is_stale:
            return True
        return latest.events_version_hash != self._version_hash(merchant_id)

    # ------------------------------------------------------------------
    def _to_out(
        self,
        profile: MerchantProfile,
        results: list[EngineResult],
        payload: AnalysisRunRequest,
        resolved: ResolvedAnalysis,
        persisted: AnalysisResult | None,
    ) -> AnalysisResultOut:
        """把唯一权威结果渲染为 API 输出。

        这里**不再**自行判断状态或重新计算缺口：所有标量字段直接取
        ``resolved``，它已经绑定到同一个情景。此前的实现会在状态不一致时
        用「各情景最大缺口」重新拼装，导致 API 输出与落库结果不是同一份数据。
        """
        curves = [
            ScenarioCurve(
                label=item.label,
                kind=payload.mode,
                status=str(item.status),
                status_label=item.status_label,
                max_withdrawable_cents=item.max_withdrawable_cents,
                limiting_timestamp=item.limiting_timestamp,
                limiting_balance_cents=item.limiting_balance_cents,
                limiting_event_id=item.limiting_event_id,
                limiting_event_title=item.limiting_event_title,
                limiting_reason=item.limiting_reason,
                payment_gap_cents=item.payment_gap_cents,
                buffer_gap_cents=item.buffer_gap_cents,
                end_balance_cents=item.end_balance_cents,
                points=[point.to_dict() for point in item.points],
                pending_inflows_at_limit=[item2.to_dict() for item2 in item.pending_inflows_at_limit],
                window_inflow_cents=item.window_inflow_cents,
                window_outflow_cents=item.window_outflow_cents,
            )
            for item in results
        ]

        return AnalysisResultOut(
            id=persisted.id if persisted else None,
            merchant_id=profile.id,
            mode=payload.mode,
            mode_label=_scenario_kind_label(payload.mode),
            status=str(resolved.status),
            status_label=resolved.status_label,
            max_withdrawable_cents=resolved.max_withdrawable_cents,
            binding_label=resolved.binding_label,
            binding_scenario_index=resolved.binding_scenario_index,
            opening_balance_cents=resolved.opening_balance_cents,
            buffer_cents=resolved.buffer_cents,
            currency=profile.default_currency,
            snapshot_at=resolved.snapshot_at,
            window_end_at=resolved.window_end_at,
            limiting_timestamp=resolved.limiting_timestamp,
            limiting_balance_cents=resolved.limiting_balance_cents,
            limiting_event_id=resolved.limiting_event_id,
            limiting_event_title=resolved.limiting_event_title,
            limiting_reason=resolved.limiting_reason,
            pending_inflows_at_limit=[
                item.to_dict() for item in resolved.pending_inflows_at_limit
            ],
            payment_gap_cents=resolved.payment_gap_cents,
            buffer_gap_cents=resolved.buffer_gap_cents,
            minimum_balance_cents=resolved.minimum_balance_cents,
            balance_floor_cents=resolved.minimum_balance_cents,
            end_balance_cents=resolved.end_balance_cents,
            opening_covers_buffer=resolved.opening_covers_buffer,
            window_inflow_cents=resolved.window_inflow_cents,
            window_outflow_cents=resolved.window_outflow_cents,
            pending_settlement_cents=resolved.pending_settlement_cents,
            scenarios=curves,
            points=[point.to_dict() for point in resolved.points],
            validation_errors=resolved.validation_errors,
            excluded_event_ids=resolved.excluded_event_ids,
            engine_version=ENGINE_VERSION,
            is_stale=False,
            generated_at=utcnow(),
        )

    # ------------------------------------------------------------------
    # 情景
    # ------------------------------------------------------------------
    def list_scenarios(self, merchant_id: str) -> list[ScenarioOut]:
        rows = self._load_scenarios(merchant_id, None)
        return [self._scenario_out(item) for item in rows]

    def create_scenario(
        self,
        profile: MerchantProfile,
        *,
        name: str,
        kind: str,
        description: str | None,
        actor_id: str,
        overrides: list[dict] | None = None,
    ) -> ScenarioOut:
        scenario = Scenario(
            merchant_id=profile.id,
            name=name.strip(),
            kind=kind,
            description=description,
            created_by=actor_id,
        )
        self.db.add(scenario)
        self.db.flush()
        for item in overrides or []:
            self._upsert_override(scenario.id, item)
        self.db.commit()
        self.db.refresh(scenario)
        return self._scenario_out(scenario)

    def update_scenario(
        self,
        scenario: Scenario,
        *,
        name: str | None = None,
        description: str | None = None,
        overrides: list[dict] | None = None,
    ) -> ScenarioOut:
        if name is not None and name.strip():
            scenario.name = name.strip()
        if description is not None:
            scenario.description = description
        if overrides is not None:
            for item in overrides:
                self._upsert_override(scenario.id, item)
        self.db.commit()
        self.db.refresh(scenario)
        return self._scenario_out(scenario)

    def _upsert_override(self, scenario_id: str, data: dict) -> ScenarioEventOverride:
        event_id = data.get("cash_event_id")
        if not event_id:
            raise ValidationFailed("情景覆盖必须指定收付款事项", code="MISSING_EVENT_ID")
        existing = self.db.scalar(
            select(ScenarioEventOverride).where(
                ScenarioEventOverride.scenario_id == scenario_id,
                ScenarioEventOverride.cash_event_id == event_id,
            )
        )
        scheduled_at = data.get("scheduled_at")
        if existing is None:
            existing = ScenarioEventOverride(scenario_id=scenario_id, cash_event_id=event_id)
            self.db.add(existing)
        existing.scheduled_at_override = to_utc(scheduled_at) if scheduled_at else None
        existing.amount_cents_override = data.get("amount_cents")
        existing.direction_override = data.get("direction")
        existing.state_override = data.get("state")
        existing.note = data.get("note")
        self.db.flush()
        return existing

    def require_scenario(self, scenario_id: str, merchant_id: str) -> Scenario:
        row = self.db.get(Scenario, scenario_id)
        if row is None or row.merchant_id != merchant_id:
            raise NotFound("情景不存在")
        return row

    def delete_scenario(self, scenario: Scenario) -> None:
        self.db.delete(scenario)
        self.db.commit()

    def _scenario_out(self, scenario: Scenario) -> ScenarioOut:
        from app.schemas.analysis import ScenarioOverrideOut

        return ScenarioOut(
            id=scenario.id,
            merchant_id=scenario.merchant_id,
            name=scenario.name,
            kind=scenario.kind,
            description=scenario.description,
            is_primary=scenario.is_primary,
            created_at=scenario.created_at,
            updated_at=scenario.updated_at,
            overrides=[
                ScenarioOverrideOut(
                    cash_event_id=item.cash_event_id,
                    scheduled_at=item.scheduled_at_override,
                    amount_cents=item.amount_cents_override,
                    direction=item.direction_override,
                    state=item.state_override,
                    note=item.note,
                )
                for item in scenario.overrides
            ],
        )


def window_end_of(snapshot_at: datetime) -> datetime:
    return to_utc(snapshot_at) + timedelta(days=WINDOW_DAYS)


def ai_configured() -> bool:
    return settings.ai_configured
