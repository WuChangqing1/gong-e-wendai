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

受约束时点集合 ``T`` = { 期初时点 ``snapshot_at`` } ∪ { 窗口内每个事件之后的时点 }
∪ { 窗口结束时点 }。

最大可提用金额（用户今日从经营资金中拿给家庭使用的金额 ``x``）::

    headroom(t) = C(t) - buffer
    x* = min_{t in T} headroom(t)
    max_withdrawable = max(0, x*)

**期初时点必须参与竞争**：用户是在 ``snapshot_at`` 这一刻就把钱拿走，因此拿走 ``x``
之后立刻要满足 ``opening - x >= buffer``。若期初点不参与，未来才到账的收入会反过来
把今天可以提前拿走的额度抬高，这是错误的。

因此：

* 未来没有事件时，期初点本身就是唯一约束，``max_withdrawable = opening - buffer``；
  只有资料确实不完整时才返回 ``None``。
* 任意时点的余额都不会因为把收款往后挪而变高，所以「延迟收入」不可能提高上限。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, Iterable, Sequence

from app.utils.money import format_cny, sum_cents
from app.utils.timeutil import WINDOW_DAYS, to_utc

ENGINE_VERSION = "2.0.0"

DIRECTION_INFLOW = "inflow"
DIRECTION_OUTFLOW = "outflow"

EVENT_TYPE_SETTLEMENT = "settlement"

STATE_SCHEDULED = "scheduled"
STATE_INCLUDED_IN_OPENING = "included_in_opening"
STATE_CANCELLED = "cancelled"

VALID_DIRECTIONS = (DIRECTION_INFLOW, DIRECTION_OUTFLOW)
VALID_STATES = (STATE_SCHEDULED, STATE_INCLUDED_IN_OPENING, STATE_CANCELLED)

#: 影响金额计算的字段。任何对它们的修改都必须保存旧版本并触发重算。
MATERIAL_FIELDS = ("amount_cents", "scheduled_at", "direction", "state")


class AnalysisStatus(StrEnum):
    """统一后的四种分析状态。

    ``OK`` 为 2.0.0 之前的旧值，仅用于读取历史数据时做兼容映射，
    新计算一律产出 ``FEASIBLE``。
    """

    FEASIBLE = "FEASIBLE"
    PAYMENT_GAP = "PAYMENT_GAP"
    BELOW_BUFFER = "BELOW_BUFFER"
    INPUT_INCOMPLETE = "INPUT_INCOMPLETE"

    @classmethod
    def coerce(cls, value: object) -> AnalysisStatus | None:
        """把历史数据中的状态值映射到当前枚举；无法识别时返回 ``None``。"""
        if isinstance(value, cls):
            return value
        raw = str(value or "").strip().upper()
        if raw == "OK":
            return cls.FEASIBLE
        for member in cls:
            if member.value == raw:
                return member
        return None


STATUS_LABELS = {
    AnalysisStatus.FEASIBLE: "资金安排可行",
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
    #: 窗口内「计划中的结算款」收入合计。
    #: 与 :attr:`window_inflow_cents`（未来 7 天全部计划收入）不是同一个口径，
    #: 不得互相代替。
    pending_settlement_cents: int
    points: list[BalancePoint]
    validation_errors: list[dict[str, Any]] = field(default_factory=list)
    excluded_event_ids: list[str] = field(default_factory=list)
    engine_version: str = ENGINE_VERSION

    # ------------------------------------------------------------------
    @property
    def is_feasible(self) -> bool:
        return self.status is AnalysisStatus.FEASIBLE

    @property
    def is_ok(self) -> bool:
        """向后兼容别名，等价于 :attr:`is_feasible`。"""
        return self.is_feasible

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


def participating_events(events: Sequence[CashEventInput]) -> list[CashEventInput]:
    """引擎实际参与未来推演的事项。

    聚合类接口（图表窗口汇总等）必须复用本函数，而不是自己再写一套
    「哪些事项算数」的过滤规则 —— 否则图表与顶部结论会分叉。
    """
    return _prepare(events)[0]


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
    # 待结算资金只统计“结算款”这一类计划中收入。
    # 销售收款、转入、其他收入都不属于待结算口径，不能被自动归类为结算款。
    window_settlement = sum_cents(
        event.amount_cents or 0
        for event in in_window
        if event.direction == DIRECTION_INFLOW
        and event.state == STATE_SCHEDULED
        and event.event_type == EVENT_TYPE_SETTLEMENT
    )

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
    #
    # 约束时点集合包含期初点：用户此刻就把钱拿走，所以 opening - x >= buffer
    # 必须立刻成立。不含期初点时，未来到账的收入会错误地抬高今天的可提用金额。
    # ------------------------------------------------------------------
    headroom_minimum = minimum_balance - buffer_cents
    limiting_point: BalancePoint | None = next(
        (point for point in points if point.balance_cents == minimum_balance), None
    )

    if incomplete:
        max_withdrawable: int | None = None
        limiting_reason = "部分收付款事项的资料不完整，暂时无法给出可提用金额。"
    else:
        max_withdrawable = max(0, headroom_minimum)
        if limiting_point is not None and limiting_point.is_opening:
            if headroom_minimum > 0:
                limiting_reason = (
                    "当前可用经营资金扣除经营留底后即为今日可提用金额；"
                    "未来 7 天的收支不会再抬高这个上限。"
                )
            elif headroom_minimum == 0:
                limiting_reason = (
                    "当前可用经营资金刚好等于经营留底，今天没有可提用空间。"
                )
            else:
                limiting_reason = "当前可用经营资金已经低于经营留底，今天不建议提用家庭资金。"
        elif headroom_minimum < 0:
            limiting_reason = (
                "未来 7 天内最紧张时点的余额低于经营留底，当前不建议提用家庭资金。"
            )
        elif headroom_minimum == 0:
            limiting_reason = "未来 7 天内最紧张时点的余额刚好等于经营留底，没有可提用空间。"
        else:
            limiting_reason = "未来 7 天内最紧张时点的余额扣除经营留底后，即为今日可提用金额。"

    # ------------------------------------------------------------------
    # 缺口：付款缺口与留底缺口分别计算，绝不相加
    # ------------------------------------------------------------------
    payment_gap = max(0, -minimum_balance)
    buffer_gap = max(0, buffer_cents - minimum_balance)

    # ------------------------------------------------------------------
    # 状态：优先级 INPUT_INCOMPLETE > PAYMENT_GAP > BELOW_BUFFER > FEASIBLE
    # ------------------------------------------------------------------
    if incomplete:
        status = AnalysisStatus.INPUT_INCOMPLETE
    elif minimum_balance < 0:
        status = AnalysisStatus.PAYMENT_GAP
    elif minimum_balance < buffer_cents:
        status = AnalysisStatus.BELOW_BUFFER
    else:
        status = AnalysisStatus.FEASIBLE

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
        pending_settlement_cents=window_settlement,
        points=points,
        validation_errors=validation_errors,
        excluded_event_ids=excluded_ids,
    )


@dataclass(frozen=True, slots=True)
class JointResult:
    """多情景共同约束结果：取所有情景中**真正最坏**的那个作为绑定情景。

    「最坏」不能只比 ``max_withdrawable_cents``：多个不可行情景的上限都会被截断为
    0，用最小值比较等于随机取第一个。因此按严重度排序，同级再比缺口与余额。

    绑定情景一旦确定，顶层结果的所有字段都必须从它取值 —— 不允许
    「A 情景的状态 + B 情景的缺口 + C 情景的限制时点」这种拼装。
    """

    scenario_results: list[EngineResult]
    max_withdrawable_cents: int | None
    binding_label: str | None
    status: AnalysisStatus
    #: 绑定情景在 ``scenario_results`` 中的下标；无情景时为 ``None``
    binding_scenario_index: int | None = None

    @property
    def binding(self) -> EngineResult | None:
        """绑定情景本身（顶层字段的唯一来源）。"""
        index = self.binding_scenario_index
        if index is None or index < 0 or index >= len(self.scenario_results):
            return None
        return self.scenario_results[index]

    @property
    def payment_gap_cents_effective(self) -> int:
        """绑定情景的付款缺口（共同约束下顶层缺口就是它）。"""
        binding = self.binding
        return int(binding.payment_gap_cents) if binding is not None else 0

    @property
    def buffer_gap_cents_effective(self) -> int:
        """绑定情景的留底缺口。"""
        binding = self.binding
        return int(binding.buffer_gap_cents) if binding is not None else 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_withdrawable_cents": self.max_withdrawable_cents,
            "binding_label": self.binding_label,
            "binding_scenario_index": self.binding_scenario_index,
            "status": str(self.status),
            "scenarios": [item.to_dict() for item in self.scenario_results],
        }


#: 状态严重度：数值越大越严重。INPUT_INCOMPLETE 最严重（无法给出结论）。
STATUS_SEVERITY: dict[str, int] = {
    str(AnalysisStatus.INPUT_INCOMPLETE): 3,
    str(AnalysisStatus.PAYMENT_GAP): 2,
    str(AnalysisStatus.BELOW_BUFFER): 1,
    str(AnalysisStatus.FEASIBLE): 0,
}


def scenario_severity_key(result: EngineResult) -> tuple[int, int, int, int]:
    """把一个情景映射为「越小越坏」的排序键，用于挑选绑定情景。

    按产品口径的优先级逐级比较：

    1. 状态严重度（``INPUT_INCOMPLETE`` > ``PAYMENT_GAP`` > ``BELOW_BUFFER`` > ``FEASIBLE``）
    2. ``PAYMENT_GAP``：付款缺口更大者更坏，其次留底缺口更大者，再次最低余额更低者
    3. ``BELOW_BUFFER``：留底缺口更大者更坏，其次最低余额更低者
    4. ``FEASIBLE``：可提用更小者更坏，其次最低余额更低者

    为让四种状态共用同一个键，这里统一使用「取负」把「更大更坏」转成「更小更坏」；
    对不适用的字段填 0，因此不会影响同状态内的比较。
    """
    severity = STATUS_SEVERITY.get(str(result.status), 0)
    return (
        -severity,
        -int(result.payment_gap_cents or 0),
        -int(result.buffer_gap_cents or 0),
        int(result.minimum_balance_cents if result.minimum_balance_cents is not None else 0),
        0 if result.max_withdrawable_cents is None else int(result.max_withdrawable_cents),
    )


def run_joint(scenarios: Sequence[EngineInput]) -> JointResult:
    """按“共同约束”模式计算：`x` 必须同时满足所有情景。

    绑定情景按 :func:`scenario_severity_key` 选出；结果相同时保持输入顺序（稳定）。
    """
    results = [run_engine(item) for item in scenarios]

    if not results:
        return JointResult(
            scenario_results=[],
            max_withdrawable_cents=None,
            binding_label=None,
            status=AnalysisStatus.INPUT_INCOMPLETE,
            binding_scenario_index=None,
        )

    # 稳定选择：min 取第一个最小值，键完全相同时保留输入顺序。
    binding_index = min(range(len(results)), key=lambda index: scenario_severity_key(results[index]))
    binding = results[binding_index]

    return JointResult(
        scenario_results=list(results),
        max_withdrawable_cents=binding.max_withdrawable_cents,
        binding_label=binding.label,
        status=binding.status,
        binding_scenario_index=binding_index,
    )


# ---------------------------------------------------------------------------
# 唯一解析结果
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class ResolvedAnalysis:
    """一次分析的**唯一权威结果**。

    ``AnalysisService`` 的 API 输出、数据库落库与家庭分享全部从它派生，
    禁止任何一处再自行判断 ``results[0]`` 或重新计算缺口。

    共同约束模式下，各字段都已绑定到同一个情景（:attr:`binding_scenario_index`），
    因此不会出现「状态来自 A、缺口来自 B、限制时点来自 C」的拼装结果。
    """

    mode: str
    status: AnalysisStatus
    status_label: str
    max_withdrawable_cents: int | None

    #: 绑定情景下标与标签（单情景模式下为 0 / 该情景标签）
    binding_scenario_index: int | None
    binding_label: str | None

    opening_balance_cents: int
    buffer_cents: int
    snapshot_at: datetime
    window_end_at: datetime

    limiting_timestamp: datetime | None
    limiting_balance_cents: int | None
    limiting_event_id: str | None
    limiting_event_title: str | None
    limiting_reason: str

    payment_gap_cents: int
    buffer_gap_cents: int
    minimum_balance_cents: int | None
    end_balance_cents: int | None

    window_inflow_cents: int
    window_outflow_cents: int
    pending_settlement_cents: int

    pending_inflows_at_limit: list[PendingInflow]
    points: list[BalancePoint]

    opening_covers_buffer: bool
    validation_errors: list[dict[str, Any]]
    excluded_event_ids: list[str]
    engine_version: str = ENGINE_VERSION

    @property
    def is_joint(self) -> bool:
        return self.mode == "joint"

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "status": str(self.status),
            "status_label": self.status_label,
            "max_withdrawable_cents": self.max_withdrawable_cents,
            "binding_scenario_index": self.binding_scenario_index,
            "binding_label": self.binding_label,
            "limiting_event_title": self.limiting_event_title,
            "payment_gap_cents": self.payment_gap_cents,
            "buffer_gap_cents": self.buffer_gap_cents,
        }


#: 共同约束模式下，顶层文案由绑定情景的状态决定，因此需要与单情景不同的措辞。
_JOINT_REASON_BY_STATUS: dict[AnalysisStatus, str] = {
    AnalysisStatus.PAYMENT_GAP: (
        "同时考虑这些情况后，至少一个情景即使不提用家庭资金，仍然存在付款缺口；"
        "可提用金额为 0 只表示没有安全金额，不代表资金安排可行。"
    ),
    AnalysisStatus.BELOW_BUFFER: (
        "同时考虑这些情况后，至少一个情景会低于你设置的经营留底，当前不建议提用家庭资金。"
    ),
    AnalysisStatus.INPUT_INCOMPLETE: "部分情景的收付款资料尚未确认，暂时无法给出共同结果。",
}


def resolve_analysis(
    *,
    mode: str,
    engine_results: Sequence[EngineResult],
    joint: JointResult | None = None,
) -> ResolvedAnalysis:
    """把一次分析的引擎结果解析为唯一权威结果。

    单情景模式：直接取该情景。
    共同约束模式：取 :func:`run_joint` 选出的绑定情景，顶层字段全部来自它。
    """
    if not engine_results:
        raise ValueError("resolve_analysis 需要至少一个情景结果")

    if joint is not None:
        index = joint.binding_scenario_index
        if index is None:
            index = 0
        binding = engine_results[index]
        status = joint.status
        binding_label = joint.binding_label
    else:
        index = 0
        binding = engine_results[0]
        status = binding.status
        binding_label = binding.label

    status_label = STATUS_LABELS[status]
    limiting_reason = binding.limiting_reason
    if joint is not None:
        # 共同约束的文案必须解释「共同」的含义，不能沿用单情景措辞。
        limiting_reason = _JOINT_REASON_BY_STATUS.get(status, binding.limiting_reason)

    return ResolvedAnalysis(
        mode=mode,
        status=status,
        status_label=status_label,
        max_withdrawable_cents=(
            joint.max_withdrawable_cents if joint is not None else binding.max_withdrawable_cents
        ),
        binding_scenario_index=index,
        binding_label=binding_label,
        opening_balance_cents=binding.opening_balance_cents,
        buffer_cents=binding.buffer_cents,
        snapshot_at=binding.snapshot_at,
        window_end_at=binding.window_end_at,
        limiting_timestamp=binding.limiting_timestamp,
        limiting_balance_cents=binding.limiting_balance_cents,
        limiting_event_id=binding.limiting_event_id,
        limiting_event_title=binding.limiting_event_title,
        limiting_reason=limiting_reason,
        payment_gap_cents=binding.payment_gap_cents,
        buffer_gap_cents=binding.buffer_gap_cents,
        minimum_balance_cents=binding.minimum_balance_cents,
        end_balance_cents=binding.end_balance_cents,
        window_inflow_cents=binding.window_inflow_cents,
        window_outflow_cents=binding.window_outflow_cents,
        pending_settlement_cents=binding.pending_settlement_cents,
        pending_inflows_at_limit=list(binding.pending_inflows_at_limit),
        points=list(binding.points),
        opening_covers_buffer=binding.opening_covers_buffer,
        validation_errors=list(binding.validation_errors),
        excluded_event_ids=list(binding.excluded_event_ids),
    )



def events_version_hash(events: Sequence[CashEventInput]) -> str:
    """Deterministic fingerprint of the event set (for staleness detection)."""
    import hashlib

    payload = "||".join(sorted(event.fingerprint() for event in events))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def is_engine_version_current(engine_version: str | None) -> bool:
    """旧引擎版本产生的分析结果不再作为当前决策依据。"""
    return str(engine_version or "") == ENGINE_VERSION


__all__ = [
    "AnalysisStatus",
    "BalancePoint",
    "CashEventInput",
    "DIRECTION_INFLOW",
    "DIRECTION_OUTFLOW",
    "ENGINE_VERSION",
    "EVENT_TYPE_SETTLEMENT",
    "EngineInput",
    "EngineResult",
    "JointResult",
    "MATERIAL_FIELDS",
    "PendingInflow",
    "ResolvedAnalysis",
    "STATE_CANCELLED",
    "STATE_INCLUDED_IN_OPENING",
    "STATE_SCHEDULED",
    "STATUS_LABELS",
    "STATUS_SEVERITY",
    "events_version_hash",
    "is_engine_version_current",
    "resolve_analysis",
    "run_engine",
    "run_joint",
    "scenario_severity_key",
    "validate_events",
]
