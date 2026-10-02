"""七天资金推演与最大可提用金额计算引擎。

设计约束
--------
* 纯业务模块：不依赖 FastAPI ``Request``、不依赖数据库 ``Session``、不依赖
  任何页面代码，只接受结构化输入，返回结构化结果。
* 全部金额为整数分（``int`` cents），绝不使用浮点。
* 全部时间为 aware UTC ``datetime``。
* 所有结果为确定性计算，AI 不得参与、不得覆盖、不得改写。

核心公式
--------
``C(t) = opening_balance + Σ Δk``（按时间顺序逐事件扫描）

* ``inflow``  -> ``+amount``
* ``outflow`` -> ``-amount``

最大可提用金额（用户今日从经营资金中拿给家庭使用的金额 ``x``）::

    x* = min( min_{t in 未来受约束时点} C(t) ) - buffer
    max_withdrawable = max(0, x*)

期初余额不再参与 ``x*`` 的计算：期初资金本身已经“可用”，不构成对未来付款的
约束；同时输出 ``balance_floor_cents`` 给出“即使不提用也达不到留底”的提示。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, Iterable, Sequence

from app.utils.money import format_cny, sum_cents
from app.utils.timeutil import WINDOW_DAYS, to_utc

ENGINE_VERSION = "1.0.0"

DIRECTION_INFLOW = "inflow"
DIRECTION_OUTFLOW = "outflow"

STATE_SCHEDULED = "scheduled"
STATE_INCLUDED_IN_OPENING = "included_in_opening"
STATE_CANCELLED = "cancelled"

VALID_DIRECTIONS = (DIRECTION_INFLOW, DIRECTION_OUTFLOW)
VALID_STATES = (STATE_SCHEDULED, STATE_INCLUDED_IN_OPENING, STATE_CANCELLED)


class AnalysisStatus(StrEnum):
    OK = "OK"
    PAYMENT_GAP = "PAYMENT_GAP"
    BELOW_BUFFER = "BELOW_BUFFER"
    INPUT_INCOMPLETE = "INPUT_INCOMPLETE"


STATUS_LABELS = {
    AnalysisStatus.OK: "资金安排可行",
    AnalysisStatus.PAYMENT_GAP: "存在付款缺口",
    AnalysisStatus.BELOW_BUFFER: "低于经营留底",
    AnalysisStatus.INPUT_INCOMPLETE: "资料不完整",
}

DIRECTION_LABELS = {DIRECTION_INFLOW: "收入", DIRECTION_OUTFLOW: "支出"}

STATE_LABELS = {
    STATE_SCHEDULED: "计划中",
    STATE_INCLUDED_IN_OPENING: "已计入期初",
    STATE_CANCELLED: "已取消",
}


@dataclass(frozen=True, slots=True)
class CashEventInput:
    """已经规范化的现金移动（收入或支出）。"""

    id: str
    cash_key: str
    title: str
    amount_cents: int | None
    direction: str
    scheduled_at: datetime | None
    state: str = STATE_SCHEDULED
    event_type: str = "other_inflow"
    sequence_index: int | None = None
    source_label: str | None = None
    current_version: int = 1
    confirmed: bool = True

    def fingerprint(self) -> str:
        when = self.scheduled_at.isoformat() if self.scheduled_at else "none"
        return (
            f"{self.cash_key}|{self.direction}|{self.amount_cents}|{when}|"
            f"{self.state}|{self.sequence_index}"
        )


@dataclass(frozen=True, slots=True)
class EngineInput:
    """Engine input: opening balance, buffer, snapshot time and events."""

    opening_balance_cents: int
    buffer_cents: int
    snapshot_at: datetime
    events: Sequence[CashEventInput]
    window_days: int = WINDOW_DAYS
    label: str = "按当前计划"

    def window_end(self) -> datetime:
        return to_utc(self.snapshot_at) + timedelta(days=self.window_days)


@dataclass(frozen=True, slots=True)
class BalancePoint:
    """余额曲线上的一个时点。"""

    timestamp: datetime
    balance_cents: int
    event_id: str | None
    event_title: str
    delta_cents: int
    direction: str | None
    state: str | None
    sequence_index: int | None
    is_opening: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "balance_cents": self.balance_cents,
            "balance_text": format_cny(self.balance_cents),
            "event_id": self.event_id,
            "event_title": self.event_title,
            "delta_cents": self.delta_cents,
            "delta_text": format_cny(self.delta_cents),
            "direction": self.direction,
            "state": self.state,
            "sequence_index": self.sequence_index,
            "is_opening": self.is_opening,
        }


@dataclass(frozen=True, slots=True)
class PendingInflow:
    event_id: str
    title: str
    amount_cents: int
    scheduled_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "title": self.title,
            "amount_cents": self.amount_cents,
            "amount_text": format_cny(self.amount_cents),
            "scheduled_at": self.scheduled_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class EngineResult:
    """结构化分析结果。"""

    status: AnalysisStatus
    label: str
    opening_balance_cents: int
    buffer_cents: int
    snapshot_at: datetime
    window_end_at: datetime
    max_withdrawable_cents: int | None
    limiting_timestamp: datetime | None
    limiting_balance_cents: int | None
    limiting_event_id: str | None
    limiting_event_title: str | None
    limiting_reason: str
    pending_inflows_at_limit: list[PendingInflow]
    payment_gap_cents: int
    buffer_gap_cents: int
    minimum_balance_cents: int | None
    minimum_future_balance_cents: int | None
    balance_floor_cents: int | None
    end_balance_cents: int | None
    opening_covers_buffer: bool
    window_inflow_cents: int
    window_outflow_cents: int
    pending_settlement_cents: int
    points: list[BalancePoint]
    validation_errors: list[dict[str, Any]] = field(default_factory=list)
    excluded_event_ids: list[str] = field(default_factory=list)
    engine_version: str = ENGINE_VERSION

    # ------------------------------------------------------------------
    @property
    def is_ok(self) -> bool:
        return self.status is AnalysisStatus.OK

    @property
    def status_label(self) -> str:
        return STATUS_LABELS[self.status]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": str(self.status),
            "status_label": self.status_label,
            "label": self.label,
            "opening_balance_cents": self.opening_balance_cents,
            "buffer_cents": self.buffer_cents,
            "snapshot_at": self.snapshot_at.isoformat(),
            "window_end_at": self.window_end_at.isoformat(),
            "max_withdrawable_cents": self.max_withdrawable_cents,
            "limiting_timestamp": (
                self.limiting_timestamp.isoformat() if self.limiting_timestamp else None
            ),
            "limiting_balance_cents": self.limiting_balance_cents,
            "limiting_event_id": self.limiting_event_id,
            "limiting_event_title": self.limiting_event_title,
            "limiting_reason": self.limiting_reason,
            "pending_inflows_at_limit": [item.to_dict() for item in self.pending_inflows_at_limit],
            "payment_gap_cents": self.payment_gap_cents,
            "buffer_gap_cents": self.buffer_gap_cents,
            "minimum_balance_cents": self.minimum_balance_cents,
            "minimum_future_balance_cents": self.minimum_future_balance_cents,
            "balance_floor_cents": self.balance_floor_cents,
            "end_balance_cents": self.end_balance_cents,
            "opening_covers_buffer": self.opening_covers_buffer,
            "window_inflow_cents": self.window_inflow_cents,
            "window_outflow_cents": self.window_outflow_cents,
            "pending_settlement_cents": self.pending_settlement_cents,
            "points": [point.to_dict() for point in self.points],
            "validation_errors": self.validation_errors,
            "excluded_event_ids": self.excluded_event_ids,
            "engine_version": self.engine_version,
        }


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------
def validate_events(events: Iterable[CashEventInput]) -> tuple[list[dict[str, Any]], list[str]]:
    """Return ``(errors, duplicate_cash_keys)`` for a set of candidate events."""
    errors: list[dict[str, Any]] = []
    seen: dict[str, str] = {}
    duplicates: list[str] = []

    for event in events:
        if not event.cash_key or not event.cash_key.strip():
            errors.append({"event_id": event.id, "field": "cash_key", "reason": "缺少事项编号"})
        elif event.cash_key in seen:
            if event.cash_key not in duplicates:
                duplicates.append(event.cash_key)
            errors.append(
                {
                    "event_id": event.id,
                    "field": "cash_key",
                    "reason": f"事项编号 {event.cash_key} 重复",
                }
            )
        else:
            seen[event.cash_key] = event.id

        if event.amount_cents is None:
            errors.append({"event_id": event.id, "field": "amount_cents", "reason": "金额缺失"})
        elif int(event.amount_cents) < 0:
            errors.append({"event_id": event.id, "field": "amount_cents", "reason": "金额为负"})

        if event.direction not in VALID_DIRECTIONS:
            errors.append(
                {"event_id": event.id, "field": "direction", "reason": "收支方向不合法"}
            )
        if event.state not in VALID_STATES:
            errors.append({"event_id": event.id, "field": "state", "reason": "状态不合法"})
        if event.scheduled_at is None:
            errors.append({"event_id": event.id, "field": "scheduled_at", "reason": "时间缺失"})
        if not event.confirmed:
            errors.append({"event_id": event.id, "field": "confirmed", "reason": "事项尚未确认"})

    return errors, duplicates


def _prepare(
    events: Sequence[CashEventInput],
) -> tuple[list[CashEventInput], list[dict[str, Any]], list[str]]:
    """Filter to events that participate in the future-cash scan.

    ``cancelled`` 与 ``included_in_opening`` 不参与未来现金变化。
    """
    validation_errors, _duplicates = validate_events(events)
    blocking_ids = {item["event_id"] for item in validation_errors}

    participating: list[CashEventInput] = []
    excluded: list[str] = []
    for event in events:
        if event.id in blocking_ids:
            excluded.append(event.id)
            continue
        if event.state != STATE_SCHEDULED:
            excluded.append(event.id)
            continue
        participating.append(event)

    return participating, validation_errors, excluded


def _sort_key(event: CashEventInput):
    """同一时刻没有明确 ``sequence_index`` 时：先支出，后收入。

    这样做的目的是暴露资金在中途可能出现的缺口，而不是用当天的入账去“抹平”
    同一时刻的付款压力。
    """
    direction_rank = 0 if event.direction == DIRECTION_OUTFLOW else 1
    sequence = event.sequence_index if event.sequence_index is not None else 10**9
    return (to_utc(event.scheduled_at), sequence, direction_rank, event.cash_key)


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------
def run_engine(data: EngineInput) -> EngineResult:
    """执行一次七天资金推演。"""
    snapshot_at = to_utc(data.snapshot_at)
    window_end_at = snapshot_at + timedelta(days=data.window_days)
    opening = int(data.opening_balance_cents)
    buffer_cents = int(data.buffer_cents)

    participating, validation_errors, excluded_ids = _prepare(data.events)
    incomplete = bool(validation_errors)

    # 未来现金变化只扫描窗口内的事件（含起点，不含终点之后）
    in_window = [event for event in participating if snapshot_at <= to_utc(event.scheduled_at) <= window_end_at]
    in_window.sort(key=_sort_key)

    window_inflow = sum_cents(
        event.amount_cents or 0 for event in in_window if event.direction == DIRECTION_INFLOW
    )
    window_outflow = sum_cents(
        event.amount_cents or 0 for event in in_window if event.direction == DIRECTION_OUTFLOW
    )
    pending_settlement = window_inflow

    points: list[BalancePoint] = [
        BalancePoint(
            timestamp=snapshot_at,
            balance_cents=opening,
            event_id=None,
            event_title="期初余额",
            delta_cents=0,
            direction=None,
            state=None,
            sequence_index=None,
            is_opening=True,
        )
    ]

    balance = opening
    for event in in_window:
        amount = int(event.amount_cents or 0)
        delta = amount if event.direction == DIRECTION_INFLOW else -amount
        balance += delta
        points.append(
            BalancePoint(
                timestamp=to_utc(event.scheduled_at),
                balance_cents=balance,
                event_id=event.id,
                event_title=event.title,
                delta_cents=delta,
                direction=event.direction,
                state=event.state,
                sequence_index=event.sequence_index,
            )
        )

    future_points = [point for point in points if not point.is_opening]
    all_balances = [point.balance_cents for point in points]
    minimum_balance = min(all_balances) if all_balances else opening
    minimum_future_balance = (
        min(point.balance_cents for point in future_points) if future_points else None
    )
    end_balance = points[-1].balance_cents if points else opening

    # ------------------------------------------------------------------
    # 最大可提用金额
    # ------------------------------------------------------------------
    if incomplete:
        max_withdrawable: int | None = None
        limiting_point: BalancePoint | None = next(
            (point for point in future_points if point.balance_cents == minimum_future_balance),
            None,
        )
        limiting_reason = "部分收付款事项的资料不完整，暂时无法给出可提用金额。"
    elif minimum_future_balance is None:
        max_withdrawable = None
        limiting_point = None
        limiting_reason = "未来 7 天内没有已确认的收付款事项，暂时无法判断提用后的资金安全。"
    else:
        candidate = minimum_future_balance - buffer_cents
        max_withdrawable = max(0, candidate)
        limiting_point = next(
            (point for point in future_points if point.balance_cents == minimum_future_balance),
            None,
        )
        if candidate < 0:
            limiting_reason = (
                "未来 7 天内最紧张时点的余额低于经营留底，当前不建议提用家庭资金。"
            )
        elif candidate == 0:
            limiting_reason = "未来 7 天内最紧张时点的余额刚好等于经营留底，没有可提用空间。"
        else:
            limiting_reason = "未来 7 天内最紧张时点的余额扣除经营留底后，即为今日可提用金额。"

    # ------------------------------------------------------------------
    # 缺口：付款缺口与留底缺口分别计算，绝不相加
    # ------------------------------------------------------------------
    payment_gap = max(0, -minimum_balance)
    buffer_gap = max(0, buffer_cents - minimum_balance)

    # ------------------------------------------------------------------
    # 状态：优先级 INPUT_INCOMPLETE > PAYMENT_GAP > BELOW_BUFFER > OK
    # ------------------------------------------------------------------
    if incomplete:
        status = AnalysisStatus.INPUT_INCOMPLETE
    elif minimum_balance < 0:
        status = AnalysisStatus.PAYMENT_GAP
    elif minimum_balance < buffer_cents:
        status = AnalysisStatus.BELOW_BUFFER
    else:
        status = AnalysisStatus.OK

    # ------------------------------------------------------------------
    # 限制时点当时的待结算收入
    # ------------------------------------------------------------------
    pending: list[PendingInflow] = []
    if limiting_point is not None:
        limit_ts = limiting_point.timestamp
        later_inflows = [
            event
            for event in in_window
            if event.direction == DIRECTION_INFLOW and to_utc(event.scheduled_at) > limit_ts
        ]
        later_inflows.sort(key=lambda item: to_utc(item.scheduled_at))
        pending = [
            PendingInflow(
                event_id=event.id,
                title=event.title,
                amount_cents=int(event.amount_cents or 0),
                scheduled_at=to_utc(event.scheduled_at),
            )
            for event in later_inflows[:3]
        ]

    return EngineResult(
        status=status,
        label=data.label,
        opening_balance_cents=opening,
        buffer_cents=buffer_cents,
        snapshot_at=snapshot_at,
        window_end_at=window_end_at,
        max_withdrawable_cents=max_withdrawable,
        limiting_timestamp=limiting_point.timestamp if limiting_point else None,
        limiting_balance_cents=limiting_point.balance_cents if limiting_point else None,
        limiting_event_id=limiting_point.event_id if limiting_point else None,
        limiting_event_title=limiting_point.event_title if limiting_point else None,
        limiting_reason=limiting_reason,
        pending_inflows_at_limit=pending,
        payment_gap_cents=payment_gap,
        buffer_gap_cents=buffer_gap,
        minimum_balance_cents=minimum_balance,
        minimum_future_balance_cents=minimum_future_balance,
        balance_floor_cents=minimum_balance,
        end_balance_cents=end_balance,
        opening_covers_buffer=opening >= buffer_cents,
        window_inflow_cents=window_inflow,
        window_outflow_cents=window_outflow,
        pending_settlement_cents=pending_settlement,
        points=points,
        validation_errors=validation_errors,
        excluded_event_ids=excluded_ids,
    )


@dataclass(frozen=True, slots=True)
class JointResult:
    """多情景共同约束结果：取所有情景中最保守的上限。"""

    scenario_results: list[EngineResult]
    max_withdrawable_cents: int | None
    binding_label: str | None
    status: AnalysisStatus

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_withdrawable_cents": self.max_withdrawable_cents,
            "binding_label": self.binding_label,
            "status": str(self.status),
            "scenarios": [item.to_dict() for item in self.scenario_results],
        }


def run_joint(scenarios: Sequence[EngineInput]) -> JointResult:
    """按“共同约束”模式计算：`x` 必须同时满足所有情景。"""
    results = [run_engine(item) for item in scenarios]

    if not results:
        return JointResult(
            scenario_results=[],
            max_withdrawable_cents=None,
            binding_label=None,
            status=AnalysisStatus.INPUT_INCOMPLETE,
        )

    values: list[tuple[int, str]] = []
    statuses: list[AnalysisStatus] = []
    for result in results:
        statuses.append(result.status)
        if result.max_withdrawable_cents is None:
            continue
        values.append((result.max_withdrawable_cents, result.label))

    if not values or AnalysisStatus.INPUT_INCOMPLETE in statuses:
        max_withdrawable: int | None = None
        binding_label: str | None = None
    else:
        max_withdrawable, binding_label = min(values, key=lambda item: item[0])

    if AnalysisStatus.INPUT_INCOMPLETE in statuses:
        status = AnalysisStatus.INPUT_INCOMPLETE
    elif any(item is AnalysisStatus.PAYMENT_GAP for item in statuses):
        status = AnalysisStatus.PAYMENT_GAP
    elif any(item is AnalysisStatus.BELOW_BUFFER for item in statuses):
        status = AnalysisStatus.BELOW_BUFFER
    else:
        status = AnalysisStatus.OK

    return JointResult(
        scenario_results=list(results),
        max_withdrawable_cents=max_withdrawable,
        binding_label=binding_label,
        status=status,
    )


def events_version_hash(events: Sequence[CashEventInput]) -> str:
    """Deterministic fingerprint of the event set (for staleness detection)."""
    import hashlib

    payload = "||".join(sorted(event.fingerprint() for event in events))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


__all__ = [
    "AnalysisStatus",
    "BalancePoint",
    "CashEventInput",
    "DIRECTION_INFLOW",
    "DIRECTION_OUTFLOW",
    "ENGINE_VERSION",
    "EngineInput",
    "EngineResult",
    "JointResult",
    "PendingInflow",
    "STATE_CANCELLED",
    "STATE_INCLUDED_IN_OPENING",
    "STATE_SCHEDULED",
    "STATUS_LABELS",
    "events_version_hash",
    "run_engine",
    "run_joint",
    "validate_events",
]
