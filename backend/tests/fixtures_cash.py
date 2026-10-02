"""固定算例（regression fixture）。

这些数值是产品验收的硬性口径，任何改动都必须先通过本文件，再修改期望值。

数据口径
--------
* 分析区间：``2025-10-01 09:00Z``（北京时间 17:00）起未来 7 天（到 ``10-08 09:00Z``）
* 期初已结算余额：600.00 元
* 经营留底（buffer）：600.00 元
* 三笔未来事项：
  - 第 2 天 商户结算款 +2200.00 元（收入，``settlement``，本算例中可被延迟）
  - 第 3 天 供应商货款 -1000.00 元（支出，``supplier_payment``）
  - 第 6 天 平台结算款  +700.00 元（收入，``settlement``）

口径一：按当前计划（结算款按时到账）
------------------------------------
=========== ============ ==============
时点         余额变化      余额
期初         —             600.00
第 2 天      +2200.00     2800.00
第 3 天      -1000.00     1800.00  <- 最紧张时点
第 6 天       +700.00     2500.00
=========== ============ ==============

* 最紧张时点余额 1800.00 元；可提用金额 = 1800.00 - 600.00 = **1200.00 元**
* 提用 1200.00 元后，第 3 天时点余额 600.00 元，刚好等于经营留底，满足约束

口径二：到账延迟（第 2 天的商户结算款推迟 8 天，落在第 10 天，已在 7 天区间之外）
----------------------------------------------------------------------------------
=========== ============ ==============
时点         余额变化      余额
期初         —             600.00
第 3 天      -1000.00      -400.00  <- 最紧张时点
第 6 天       +700.00       300.00
=========== ============ ==============

* 最紧张时点余额 -400.00 元；可提用金额 = **0.00 元**
* 付款缺口 = 400.00 元；留底缺口 = 600.00 - (-400.00) = 1000.00 元（两者不可相加）

口径三：共同约束 = 口径一、口径二同时成立，取两者中最保守的上限 -> **0.00 元**

其余固定算例
------------
* 特定时点缺口算例：期初 0 元、第 2 天付款 200.00 元 -> 余额 -200.00 元，
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
SNAPSHOT_AT = datetime(2025, 10, 1, 9, 0, tzinfo=UTC)

OPENING_BALANCE_CENTS = 600_00
BUFFER_CENTS = 600_00

SETTLEMENT_INFLOW_ID = "fixture-settlement-inflow"
SUPPLIER_PAYMENT_ID = "fixture-supplier-payment"
PLATFORM_INFLOW_ID = "fixture-platform-inflow"

SETTLEMENT_INFLOW_CENTS = 2200_00
SUPPLIER_PAYMENT_CENTS = 1000_00
PLATFORM_INFLOW_CENTS = 700_00

SETTLEMENT_INFLOW_DAY = 1  # 第 2 天（offset 1）
SUPPLIER_PAYMENT_DAY = 2  # 第 3 天（offset 2）
PLATFORM_INFLOW_DAY = 5  # 第 6 天（offset 5）

DELAY_DAYS = 8  # 第 2 天 + 8 天 = 第 10 天，落在 7 天区间之外


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
    settlement_time = day(SETTLEMENT_INFLOW_DAY) if settlement_at is None else settlement_at
    return [
        _event(
            event_id=SETTLEMENT_INFLOW_ID,
            cash_key="FIX-SETTLE-0001",
            title="商户结算款",
            amount_cents=SETTLEMENT_INFLOW_CENTS,
            direction="inflow",
            scheduled_at=settlement_time,
            event_type="settlement",
            source_label="结算通知 8821",
        ),
        _event(
            event_id=SUPPLIER_PAYMENT_ID,
            cash_key="FIX-PAY-0001",
            title="供应商货款",
            amount_cents=SUPPLIER_PAYMENT_CENTS,
            direction="outflow",
            scheduled_at=day(SUPPLIER_PAYMENT_DAY),
            event_type="supplier_payment",
            source_label="采购合同 HT-2025-018",
        ),
        _event(
            event_id=PLATFORM_INFLOW_ID,
            cash_key="FIX-SETTLE-0002",
            title="平台结算款",
            amount_cents=PLATFORM_INFLOW_CENTS,
            direction="inflow",
            scheduled_at=day(PLATFORM_INFLOW_DAY),
            event_type="settlement",
            source_label="平台账单 2025-10",
        ),
    ]


# ---------------------------------------------------------------------------
# 三个口径
# ---------------------------------------------------------------------------
def on_time_input() -> EngineInput:
    return EngineInput(
        opening_balance_cents=OPENING_BALANCE_CENTS,
        buffer_cents=BUFFER_CENTS,
        snapshot_at=SNAPSHOT_AT,
        events=main_events(),
        label="按当前计划",
    )


def delayed_input() -> EngineInput:
    return EngineInput(
        opening_balance_cents=OPENING_BALANCE_CENTS,
        buffer_cents=BUFFER_CENTS,
        snapshot_at=SNAPSHOT_AT,
        events=main_events(
            settlement_at=day(SETTLEMENT_INFLOW_DAY) + timedelta(days=DELAY_DAYS)
        ),
        label="到账延迟",
    )


def joint_inputs() -> list[EngineInput]:
    return [on_time_input(), delayed_input()]


# ---------------------------------------------------------------------------
# 期望值
# ---------------------------------------------------------------------------
EXPECTED_ON_TIME = {
    "max_withdrawable_cents": 1200_00,
    "status": "OK",
    "limiting_timestamp": day(SUPPLIER_PAYMENT_DAY),
    "limiting_event_id": SUPPLIER_PAYMENT_ID,
    "limiting_balance_cents": 1800_00,
    "payment_gap_cents": 0,
    "buffer_gap_cents": 0,
    "end_balance_cents": 2500_00,
    "window_inflow_cents": 2900_00,
    "window_outflow_cents": 1000_00,
}

EXPECTED_DELAYED = {
    "max_withdrawable_cents": 0,
    "status": "PAYMENT_GAP",
    "limiting_timestamp": day(SUPPLIER_PAYMENT_DAY),
    "limiting_event_id": SUPPLIER_PAYMENT_ID,
    "limiting_balance_cents": -400_00,
    "payment_gap_cents": 400_00,
    "buffer_gap_cents": 1000_00,
    "end_balance_cents": 300_00,
    "window_inflow_cents": 700_00,
    "window_outflow_cents": 1000_00,
}

EXPECTED_JOINT = {
    "max_withdrawable_cents": 0,
    "status": "PAYMENT_GAP",
    "binding_label": "到账延迟",
}

# 按时到账口径下提用 1200.00 元之后
EXPECTED_WITHDRAW_1200_LIMITING_BALANCE = 600_00
EXPECTED_WITHDRAW_1200_END_BALANCE = 1300_00

# 两个口径在没有提用家庭资金时的期末余额
EXPECTED_ON_TIME_END_BALANCE = 2500_00
EXPECTED_DELAYED_END_BALANCE = 300_00

# 特定时点缺口算例：余额 -200 元、留底 600 元
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
