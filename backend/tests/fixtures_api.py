"""测试用固定算例辅助：把固定日期算例钉在“现在”。

产品口径中，分析窗口 = 期初资金时点 -> 期初资金时点 + 7 天。
为了让同一个固定算例既能用于纯函数测试（固定日期），又能用于 API 测试
（需要落在当前时间之后才不会被窗口过滤），这里提供一套“以现在为锚点”的
辅助函数。

主算例与 ``fixtures_cash`` 保持一致（期初 3600 元、留底 600 元）：

* 按当前计划：最小余额 1800 元，可提用 ``1800 - 600 = 1200`` 元，``FEASIBLE``
* 结算延迟 2 天：最小余额 -200 元，付款缺口 200 元、留底缺口 800 元，
  ``PAYMENT_GAP`` 且可提用 0
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.services.cash_engine import STATE_SCHEDULED, CashEventInput, EngineInput

MAIN_OPENING_BALANCE_CENTS = 3600_00
MAIN_BUFFER_CENTS = 600_00

# 兼容旧引用名
OPENING_BALANCE_CENTS = MAIN_OPENING_BALANCE_CENTS
BUFFER_CENTS = MAIN_BUFFER_CENTS

PURCHASE_ID = "fixture-purchase-1400"
SETTLEMENT_INFLOW_ID = "fixture-settlement-inflow"
RENT_ID = "fixture-rent-1800"
REFUND_ID = "fixture-refund-600"

PURCHASE_CENTS = 1400_00
SETTLEMENT_INFLOW_CENTS = 2000_00
RENT_CENTS = 1800_00
REFUND_CENTS = 600_00

PURCHASE_DAY = 1
SETTLEMENT_INFLOW_DAY = 2
RENT_DAY = 2
REFUND_DAY = 3

#: 结算款延迟 2 个自然日（仍在 7 天窗口内）
DELAY_DAYS = 2


def anchor() -> datetime:
    """当前时刻对齐到分钟，作为期初资金时点。"""
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    return now


def day(offset: int, hour: int = 2, *, base: datetime | None = None) -> datetime:
    return (base or anchor()) + timedelta(days=offset, hours=hour)


def event_payloads(*, base: datetime | None = None) -> list[dict]:
    """四笔未来事项的 API 请求体（时间锚定在现在之后）。"""
    return [
        {
            "cash_key": "API-BUY-0001",
            "title": "进货款",
            "direction": "outflow",
            "amount_cents": PURCHASE_CENTS,
            "scheduled_at": day(PURCHASE_DAY, 8, base=base).isoformat(),
            "event_type": "supplier_payment",
            "source_label": "采购合同 HT-2026-011",
        },
        {
            "cash_key": "API-SETTLE-0001",
            "title": "结算款",
            "direction": "inflow",
            "amount_cents": SETTLEMENT_INFLOW_CENTS,
            "scheduled_at": day(SETTLEMENT_INFLOW_DAY, 9, base=base).isoformat(),
            "event_type": "settlement",
            "source_label": "结算通知 8821",
        },
        {
            "cash_key": "API-RENT-0001",
            "title": "房租",
            "direction": "outflow",
            "amount_cents": RENT_CENTS,
            "scheduled_at": day(RENT_DAY, 18, base=base).isoformat(),
            "event_type": "rent",
            "source_label": "租赁合同 ZL-2026-03",
        },
        {
            "cash_key": "API-REFUND-0001",
            "title": "已确认退款",
            "direction": "outflow",
            "amount_cents": REFUND_CENTS,
            "scheduled_at": day(REFUND_DAY, 10, base=base).isoformat(),
            "event_type": "refund",
            "source_label": "退款单 TK-2026-007",
        },
    ]


def engine_events(*, base: datetime | None = None, settlement_at: datetime | None = None):
    settlement_time = (
        day(SETTLEMENT_INFLOW_DAY, 9, base=base) if settlement_at is None else settlement_at
    )
    return [
        CashEventInput(
            id=PURCHASE_ID,
            cash_key="API-BUY-0001",
            title="进货款",
            amount_cents=PURCHASE_CENTS,
            direction="outflow",
            scheduled_at=day(PURCHASE_DAY, 8, base=base),
            state=STATE_SCHEDULED,
            event_type="supplier_payment",
        ),
        CashEventInput(
            id=SETTLEMENT_INFLOW_ID,
            cash_key="API-SETTLE-0001",
            title="结算款",
            amount_cents=SETTLEMENT_INFLOW_CENTS,
            direction="inflow",
            scheduled_at=settlement_time,
            state=STATE_SCHEDULED,
            event_type="settlement",
        ),
        CashEventInput(
            id=RENT_ID,
            cash_key="API-RENT-0001",
            title="房租",
            amount_cents=RENT_CENTS,
            direction="outflow",
            scheduled_at=day(RENT_DAY, 18, base=base),
            state=STATE_SCHEDULED,
            event_type="rent",
        ),
        CashEventInput(
            id=REFUND_ID,
            cash_key="API-REFUND-0001",
            title="已确认退款",
            amount_cents=REFUND_CENTS,
            direction="outflow",
            scheduled_at=day(REFUND_DAY, 10, base=base),
            state=STATE_SCHEDULED,
            event_type="refund",
        ),
    ]


def engine_input(*, base: datetime | None = None, delayed: bool = False) -> EngineInput:
    settlement = None
    if delayed:
        settlement = day(SETTLEMENT_INFLOW_DAY, 9, base=base) + timedelta(days=DELAY_DAYS)
    return EngineInput(
        opening_balance_cents=MAIN_OPENING_BALANCE_CENTS,
        buffer_cents=MAIN_BUFFER_CENTS,
        snapshot_at=base or anchor(),
        events=engine_events(base=base, settlement_at=settlement),
        label="结算延迟" if delayed else "按当前计划",
    )


def setup_merchant(
    client,
    *,
    opening: int = MAIN_OPENING_BALANCE_CENTS,
    buffer: int = MAIN_BUFFER_CENTS,
    username: str = "fixture_merchant",
    write_events: bool = True,
):
    """注册商户、登记期初资金、写入未来事项，返回 ``base`` 时间锚点。

    若账户已存在（同一个测试内重复调用），直接登录并复用。
    """
    response = client.post(
        "/api/v1/auth/register",
        json={
            "username": username,
            "password": "Wendai2025",
            "display_name": "固定算例商户",
            "roles": ["merchant"],
            "business_name": "固定算例商铺",
        },
    )
    if response.status_code == 409:
        login = client.post(
            "/api/v1/auth/login", json={"username": username, "password": "Wendai2025"}
        )
        assert login.status_code == 200, login.text
    else:
        assert response.status_code == 201, response.text

    base = anchor()
    snapshot = client.post(
        "/api/v1/account/snapshots",
        json={
            "opening_balance_cents": opening,
            "pending_settlement_cents": 0,
            "snapshot_at": base.isoformat(),
        },
    )
    assert snapshot.status_code == 201, snapshot.text

    if buffer:
        client.patch("/api/v1/merchant/profile", json={"default_buffer_amount_cents": buffer})

    event_ids: dict[str, str] = {}
    if not write_events:
        return base, event_ids

    for payload in event_payloads(base=base):
        created = client.post("/api/v1/cash-events", json=payload)
        assert created.status_code == 201, created.text
        event_ids[payload["cash_key"]] = created.json()["id"]

    return base, event_ids
