"""固定算例（regression fixture）。

这些数值是产品验收的硬性口径，任何改动都必须先通过本文件，再修改期望值。

核心规则（engine 2.0.0）
-----------------------
**期初时点参与最大可提用金额的竞争。**

用户是在 ``snapshot_at`` 这一刻就把钱从经营资金里拿走，因此拿走 ``x`` 之后必须
立刻满足 ``opening - x >= buffer``。约束时点集合为：

    期初时点 ∪ 窗口内每个事件之后的时点 ∪ 窗口结束时点

    headroom(t) = C(t) - buffer
    max_withdrawable = max(0, min headroom(t))

由此可得两条不可违背的性质：

* 未来才到账的收入 **不能** 提前变成今天可以拿走的钱。
* 把收款往后挪（延迟到账）只会让余额更低，绝不会提高上限。

主回归算例：期初 3600 元、经营留底 600 元
----------------------------------------
时间基准 ``2025-10-01 00:00Z``（北京时间 10-01 08:00），窗口至 ``10-08 00:00Z``。

未来现金事件：

=========== ================== ========== ==========================
时点         事项                金额        类型
=========== ================== ========== ==========================
D1 08:00    进货款              -1400.00   ``supplier_payment``
D2 09:00    结算款              +2000.00   ``settlement``
D2 18:00    房租                -1800.00   ``rent``
D3 10:00    已确认退款           -600.00    ``refund``
=========== ================== ========== ==========================

口径一：按当前计划（结算款 D2 09:00 按时到账）
................................................

    3600 -> 2200 -> 4200 -> 2400 -> 1800

* 最小余额 1800.00 元（也是期末余额）
* ``max_withdrawable = 1800.00 - 600.00 = 1200.00`` 元，状态 ``FEASIBLE``
* 未提用时期末余额 1800.00 元；提用 1200.00 元后期末余额 600.00 元

口径二：结算延迟到 D4
......................

    3600 -> 2200 -> 400 -> -200 -> 1800

* 最小余额 -200.00 元，状态 ``PAYMENT_GAP``，``max_withdrawable = 0``
* 付款缺口 ``200.00`` 元，留底缺口 ``800.00`` 元
* 留底缺口 800 元 **已经包含** 余额从 -200 到 +600 所需差额，
  两者绝不相加（不是 1000 元）

口径三：共同约束 = 口径一 + 口径二
...................................

状态 ``PAYMENT_GAP``、可提用 ``0``。**不得**描述为「0 元满足所有情景」：
0 只表示「没有任何金额是安全的」，不代表资金安排可行。

其余固定算例
------------
* 期初约束算例：期初 600 元、留底 600 元，之后只有未来收入 ->
  ``FEASIBLE`` 且 ``max_withdrawable = 0``（未来收入不能提前成为今天可拿的钱）。
* 期初不足算例：期初 500 元、留底 600 元，之后只有未来收入 ->
  ``BELOW_BUFFER`` 且 ``max_withdrawable = 0``。
* 无未来事件算例：期初 1000 元、留底 600 元、窗口内无事项 ->
  ``FEASIBLE`` 且 ``max_withdrawable = 400``（期初点本身就是合法约束点）。
* 特定时点缺口算例：期初 0 元、第 3 天付款 200.00 元 -> 余额 -200.00 元，
  付款缺口 200.00 元、留底缺口 800.00 元。
* 日内顺序算例：同一时刻没有 ``sequence_index`` 时先支出后收入；有 ``sequence_index``
  时按序号处理。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.services.cash_engine import (
    STATE_INCLUDED_IN_OPENING,
    STATE_SCHEDULED,
    CashEventInput,
    EngineInput,
)

# ---------------------------------------------------------------------------
# 时间锚点（固定，保证回归结果稳定）
# ---------------------------------------------------------------------------
SNAPSHOT_AT = datetime(2025, 10, 1, 0, 0, tzinfo=UTC)

# ---------------------------------------------------------------------------
# 主回归算例：期初 3600 元、留底 600 元
# ---------------------------------------------------------------------------
MAIN_OPENING_BALANCE_CENTS = 3600_00
MAIN_BUFFER_CENTS = 600_00

PURCHASE_ID = "fixture-purchase-1400"
SETTLEMENT_INFLOW_ID = "fixture-settlement-inflow"
RENT_ID = "fixture-rent-1800"
REFUND_ID = "fixture-refund-600"

PURCHASE_CENTS = 1400_00
SETTLEMENT_INFLOW_CENTS = 2000_00
RENT_CENTS = 1800_00
REFUND_CENTS = 600_00

PURCHASE_AT = datetime(2025, 10, 2, 8, 0, tzinfo=UTC)  # D1 08:00
SETTLEMENT_INFLOW_AT = datetime(2025, 10, 3, 9, 0, tzinfo=UTC)  # D2 09:00
RENT_AT = datetime(2025, 10, 3, 18, 0, tzinfo=UTC)  # D2 18:00
REFUND_AT = datetime(2025, 10, 4, 10, 0, tzinfo=UTC)  # D3 10:00

#: 结算款推迟 2 天 -> D4 09:00，仍在 7 天窗口内
SETTLEMENT_DELAY_DAYS = 2
SETTLEMENT_DELAYED_AT = SETTLEMENT_INFLOW_AT + timedelta(days=SETTLEMENT_DELAY_DAYS)


def day(offset: int, hour: int = 2) -> datetime:
    """第 ``offset`` 天的 ``hour`` 时（UTC）。``day(0)`` 即快照当天 02:00Z。"""
    return SNAPSHOT_AT + timedelta(days=offset, hours=hour)


def _event(
    *,
    event_id: str,
    cash_key: str,
    title: str,
    amount_cents: int,
    direction: str,
    scheduled_at: datetime,
    event_type: str,
    source_label: str,
    state: str = STATE_SCHEDULED,
) -> CashEventInput:
    return CashEventInput(
        id=event_id,
        cash_key=cash_key,
        title=title,
        amount_cents=amount_cents,
        direction=direction,
        scheduled_at=scheduled_at,
        state=state,
        event_type=event_type,
        source_label=source_label,
    )


def main_events(*, settlement_at: datetime | None = None) -> list[CashEventInput]:
    """按时/延迟两个口径共用的事件集合。"""
    settlement_time = SETTLEMENT_INFLOW_AT if settlement_at is None else settlement_at
    return [
        _event(
            event_id=PURCHASE_ID,
            cash_key="FIX-BUY-0001",
            title="进货款",
            amount_cents=PURCHASE_CENTS,
            direction="outflow",
            scheduled_at=PURCHASE_AT,
            event_type="supplier_payment",
            source_label="采购合同 HT-2026-011",
        ),
        _event(
            event_id=SETTLEMENT_INFLOW_ID,
            cash_key="FIX-SETTLE-0001",
            title="结算款",
            amount_cents=SETTLEMENT_INFLOW_CENTS,
            direction="inflow",
            scheduled_at=settlement_time,
            event_type="settlement",
            source_label="结算通知 8821",
        ),
        _event(
            event_id=RENT_ID,
            cash_key="FIX-RENT-0001",
            title="房租",
            amount_cents=RENT_CENTS,
            direction="outflow",
            scheduled_at=RENT_AT,
            event_type="rent",
            source_label="租赁合同 ZL-2026-03",
        ),
        _event(
            event_id=REFUND_ID,
            cash_key="FIX-REFUND-0001",
            title="已确认退款",
            amount_cents=REFUND_CENTS,
            direction="outflow",
            scheduled_at=REFUND_AT,
            event_type="refund",
            source_label="退款单 TK-2026-007",
        ),
    ]


# ---------------------------------------------------------------------------
# 三个口径
# ---------------------------------------------------------------------------
def on_time_input() -> EngineInput:
    return EngineInput(
        opening_balance_cents=MAIN_OPENING_BALANCE_CENTS,
        buffer_cents=MAIN_BUFFER_CENTS,
        snapshot_at=SNAPSHOT_AT,
        events=main_events(),
        label="按当前计划",
    )


def delayed_input() -> EngineInput:
    return EngineInput(
        opening_balance_cents=MAIN_OPENING_BALANCE_CENTS,
        buffer_cents=MAIN_BUFFER_CENTS,
        snapshot_at=SNAPSHOT_AT,
        events=main_events(settlement_at=SETTLEMENT_DELAYED_AT),
        label="结算延迟",
    )


def joint_inputs() -> list[EngineInput]:
    return [on_time_input(), delayed_input()]


# ---------------------------------------------------------------------------
# 期望值
# ---------------------------------------------------------------------------
EXPECTED_ON_TIME = {
    "max_withdrawable_cents": 1200_00,
    "status": "FEASIBLE",
    "limiting_timestamp": REFUND_AT,
    "limiting_event_id": REFUND_ID,
    "limiting_balance_cents": 1800_00,
    "payment_gap_cents": 0,
    "buffer_gap_cents": 0,
    "end_balance_cents": 1800_00,
    "window_inflow_cents": 2000_00,
    "window_outflow_cents": 3800_00,
    # 待结算口径 = 只有 settlement 类型的计划中收入
    "pending_settlement_cents": 2000_00,
}

EXPECTED_DELAYED = {
    "max_withdrawable_cents": 0,
    "status": "PAYMENT_GAP",
    "limiting_timestamp": REFUND_AT,
    "limiting_event_id": REFUND_ID,
    "limiting_balance_cents": -200_00,
    "payment_gap_cents": 200_00,
    "buffer_gap_cents": 800_00,
    "end_balance_cents": 1800_00,
    "window_inflow_cents": 2000_00,
    "window_outflow_cents": 3800_00,
    "pending_settlement_cents": 2000_00,
}

EXPECTED_JOINT = {
    "max_withdrawable_cents": 0,
    "status": "PAYMENT_GAP",
    "binding_label": "结算延迟",
}

# 按时到账口径下提用 1200.00 元之后
EXPECTED_WITHDRAW_1200_LIMITING_BALANCE = 600_00
EXPECTED_WITHDRAW_1200_END_BALANCE = 600_00

# 两个口径在没有提用家庭资金时的期末余额
EXPECTED_ON_TIME_END_BALANCE = 1800_00
EXPECTED_DELAYED_END_BALANCE = 1800_00

# 提高留底到 800 元后，按时口径上限从 1200 降到 1000
RAISED_BUFFER_CENTS = 800_00
EXPECTED_RAISED_BUFFER_WITHDRAWABLE = 1000_00
EXPECTED_RAISED_BUFFER_DELAYED_PAYMENT_GAP = 200_00
EXPECTED_RAISED_BUFFER_DELAYED_BUFFER_GAP = 1000_00


def on_time_input_with_buffer(buffer_cents: int) -> EngineInput:
    return EngineInput(
        opening_balance_cents=MAIN_OPENING_BALANCE_CENTS,
        buffer_cents=buffer_cents,
        snapshot_at=SNAPSHOT_AT,
        events=main_events(),
        label="按当前计划",
    )


# ---------------------------------------------------------------------------
# 期初约束算例：未来收入不能提前变成今天的可提用金额
# ---------------------------------------------------------------------------
FUTURE_INCOME_ONLY_ID = "fixture-future-income-only"
FUTURE_INCOME_ONLY_CENTS = 5000_00
FUTURE_INCOME_ONLY_AT = day(5, 9)

#: 期初 600 / 留底 600 + 之后只有收入 -> FEASIBLE 且 0
OPENING_EXACT_BUFFER_CENTS = 600_00
EXPECTED_OPENING_EXACT_BUFFER_WITHDRAWABLE = 0
EXPECTED_OPENING_EXACT_BUFFER_STATUS = "FEASIBLE"

#: 期初 500 / 留底 600 + 之后只有收入 -> BELOW_BUFFER 且 0
OPENING_BELOW_BUFFER_CENTS = 500_00
EXPECTED_OPENING_BELOW_BUFFER_WITHDRAWABLE = 0
EXPECTED_OPENING_BELOW_BUFFER_STATUS = "BELOW_BUFFER"
EXPECTED_OPENING_BELOW_BUFFER_BUFFER_GAP = 100_00


def future_income_only_input(
    *, opening_balance_cents: int, buffer_cents: int = MAIN_BUFFER_CENTS,
    income_at: datetime | None = None,
) -> EngineInput:
    return EngineInput(
        opening_balance_cents=opening_balance_cents,
        buffer_cents=buffer_cents,
        snapshot_at=SNAPSHOT_AT,
        events=[
            _event(
                event_id=FUTURE_INCOME_ONLY_ID,
                cash_key="FIX-SETTLE-0009",
                title="结算款",
                amount_cents=FUTURE_INCOME_ONLY_CENTS,
                direction="inflow",
                scheduled_at=income_at or FUTURE_INCOME_ONLY_AT,
                event_type="settlement",
                source_label="结算通知 9009",
            )
        ],
        label="只有未来收入",
    )


# ---------------------------------------------------------------------------
# 无未来事件算例：期初点本身就是唯一约束
# ---------------------------------------------------------------------------
NO_EVENT_OPENING_CENTS = 1000_00
NO_EVENT_BUFFER_CENTS = 600_00
EXPECTED_NO_EVENT_WITHDRAWABLE = 400_00
EXPECTED_NO_EVENT_STATUS = "FEASIBLE"


def no_future_events_input() -> EngineInput:
    return EngineInput(
        opening_balance_cents=NO_EVENT_OPENING_CENTS,
        buffer_cents=NO_EVENT_BUFFER_CENTS,
        snapshot_at=SNAPSHOT_AT,
        events=[],
        label="无未来事件",
    )


# ---------------------------------------------------------------------------
# 特定时点缺口算例：余额 -200 元、留底 600 元
# ---------------------------------------------------------------------------
GAP_CASE_BALANCE_CENTS = -200_00
GAP_CASE_BUFFER_CENTS = 600_00
EXPECTED_GAP_CASE_PAYMENT_GAP = 200_00
EXPECTED_GAP_CASE_BUFFER_GAP = 800_00


def gap_case_input() -> EngineInput:
    """期初 0 元、单一付款 200 元：余额 -200 元，付款缺口 200 元、留底缺口 800 元。"""
    return EngineInput(
        opening_balance_cents=0,
        buffer_cents=GAP_CASE_BUFFER_CENTS,
        snapshot_at=SNAPSHOT_AT,
        events=[
            _event(
                event_id="gap-case-payment",
                cash_key="FIX-GAP-0001",
                title="门店租金",
                amount_cents=200_00,
                direction="outflow",
                scheduled_at=day(2),
                event_type="rent",
                source_label="租赁合同",
            )
        ],
        label="缺口算例",
    )


def included_in_opening_events() -> list[CashEventInput]:
    """已计入期初的事件：不可再次参与未来现金变化。"""
    return [
        _event(
            event_id="already-in-opening",
            cash_key="FIX-OPENING-0001",
            title="已包含在期初的转账",
            amount_cents=500_00,
            direction="inflow",
            scheduled_at=day(1),
            event_type="transfer_in",
            source_label="银行流水",
            state=STATE_INCLUDED_IN_OPENING,
        )
    ]
