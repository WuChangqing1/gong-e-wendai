"""测试用固定算例辅助：把固定日期算例钉在“现在”。

产品口径中，分析窗口 = 期初资金时点 -> 期初资金时点 + 7 天。
为了让同一个固定算例既能用于纯函数测试（固定日期），又能用于 API 测试
（需要落在当前时间之后才不会被窗口过滤），这里提供一套“以现在为锚点”的
辅助函数。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.services.cash_engine import STATE_SCHEDULED, CashEventInput, EngineInput

OPENING_BALANCE_CENTS = 600_00
BUFFER_CENTS = 600_00

SETTLEMENT_INFLOW_ID = "fixture-settlement-inflow"
SUPPLIER_PAYMENT_ID = "fixture-supplier-payment"
PLATFORM_INFLOW_ID = "fixture-platform-inflow"

SETTLEMENT_INFLOW_CENTS = 2200_00
SUPPLIER_PAYMENT_CENTS = 1000_00
PLATFORM_INFLOW_CENTS = 700_00

SETTLEMENT_INFLOW_DAY = 1
SUPPLIER_PAYMENT_DAY = 2
PLATFORM_INFLOW_DAY = 5
DELAY_DAYS = 8


def anchor() -> datetime:
    """当前时刻对齐到分钟，作为期初资金时点。"""
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    return now


def day(offset: int, hour: int = 2, *, base: datetime | None = None) -> datetime:
    return (base or anchor()) + timedelta(days=offset, hours=hour)


def event_payloads(*, base: datetime | None = None) -> list[dict]:
    """三笔未来事项的 API 请求体（时间锚定在现在之后）。"""
    return [
        {
            "cash_key": "API-SETTLE-0001",
            "title": "商户结算款",
            "direction": "inflow",
            "amount_cents": SETTLEMENT_INFLOW_CENTS,
            "scheduled_at": day(SETTLEMENT_INFLOW_DAY, base=base).isoformat(),
            "event_type": "settlement",
            "source_label": "结算通知 8821",
        },
        {
            "cash_key": "API-PAY-0001",
            "title": "供应商货款",
            "direction": "outflow",
            "amount_cents": SUPPLIER_PAYMENT_CENTS,
            "scheduled_at": day(SUPPLIER_PAYMENT_DAY, base=base).isoformat(),
            "event_type": "supplier_payment",
            "source_label": "采购合同 HT-2025-018",
        },
        {
            "cash_key": "API-SETTLE-0002",
            "title": "平台结算款",
            "direction": "inflow",
            "amount_cents": PLATFORM_INFLOW_CENTS,
            "scheduled_at": day(PLATFORM_INFLOW_DAY, base=base).isoformat(),
            "event_type": "settlement",
            "source_label": "平台账单",
        },
    ]


def engine_events(*, base: datetime | None = None, settlement_at: datetime | None = None):
    settlement_time = day(SETTLEMENT_INFLOW_DAY, base=base)
    if settlement_at is not None:
        settlement_time = settlement_at
    return [
        CashEventInput(
            id=SETTLEMENT_INFLOW_ID,
            cash_key="API-SETTLE-0001",
            title="商户结算款",
            amount_cents=SETTLEMENT_INFLOW_CENTS,
            direction="inflow",
            scheduled_at=settlement_time,
            state=STATE_SCHEDULED,
            event_type="settlement",
        ),
        CashEventInput(
            id=SUPPLIER_PAYMENT_ID,
            cash_key="API-PAY-0001",
            title="供应商货款",
            amount_cents=SUPPLIER_PAYMENT_CENTS,
            direction="outflow",
            scheduled_at=day(SUPPLIER_PAYMENT_DAY, base=base),
            state=STATE_SCHEDULED,
            event_type="supplier_payment",
        ),
        CashEventInput(
            id=PLATFORM_INFLOW_ID,
            cash_key="API-SETTLE-0002",
            title="平台结算款",
            amount_cents=PLATFORM_INFLOW_CENTS,
            direction="inflow",
            scheduled_at=day(PLATFORM_INFLOW_DAY, base=base),
            state=STATE_SCHEDULED,
            event_type="settlement",
        ),
    ]


def engine_input(*, base: datetime | None = None, delayed: bool = False) -> EngineInput:
    settlement = None
    if delayed:
        settlement = day(SETTLEMENT_INFLOW_DAY, base=base) + timedelta(days=DELAY_DAYS)
    return EngineInput(
        opening_balance_cents=OPENING_BALANCE_CENTS,
        buffer_cents=BUFFER_CENTS,
        snapshot_at=base or anchor(),
        events=engine_events(base=base, settlement_at=settlement),
        label="到账延迟" if delayed else "按当前计划",
    )


def setup_merchant(client, *, opening: int = OPENING_BALANCE_CENTS, buffer: int = BUFFER_CENTS):
    """注册商户、登记期初资金、写入三笔未来事项，返回 ``base`` 时间锚点。"""
    response = client.post(
        "/api/v1/auth/register",
        json={
            "username": "fixture_merchant",
            "password": "Wendai2025",
            "display_name": "固定算例商户",
            "roles": ["merchant"],
            "business_name": "固定算例商铺",
        },
    )
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
    for payload in event_payloads(base=base):
        created = client.post("/api/v1/cash-events", json=payload)
        assert created.status_code == 201, created.text
        event_ids[payload["cash_key"]] = created.json()["id"]

    return base, event_ids
