#!/usr/bin/env python
"""为正式业务账号建设完整、相互关联的产品数据（幂等）。

设计约束
--------
* **只做增量**：不删除任何用户、事项、历史、家庭、咨询或来源数据。
  需要退出当前计算的旧初始化事项通过 ``EventService.cancel_event`` 取消
  （保留版本与来源），不做物理删除。
* **不创建账号、不修改密码、不创建角色、不触碰 GLM Secret**。
  三个正式账号（``wangzhanggui`` / ``wangtaitai`` / ``zixunxiaoli``）任一缺失即停止。
* **数据全部通过 Service Layer 写入**，不直接 INSERT 业务表，
  因此版本历史、审计日志、来源记录都由业务规则自然产生。
* **日期相对 ``reference_at`` 计算**，不硬编码绝对日期，避免几天后全部过期。
* **固定随机种子**：同一次 ``reference_at`` 反复运行得到完全一致的历史数据。
* 数据来源内部可识别：``cash_key``、``source_ref`` 与来源 ``raw_content``
  都带统一前缀；这些内部标识不出现在普通产品页面上。

用法::

    python scripts/populate_product_data.py --dry-run      # 只报告将做什么
    python scripts/populate_product_data.py --apply        # 实际写入
    python scripts/populate_product_data.py --apply --reference-at 2026-10-04T09:00:00+08:00
"""

from __future__ import annotations

import argparse
import random
import sys
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND_DIR))

#: 正式业务账号（缺失即停止，绝不新建）
MERCHANT_USERNAME = "wangzhanggui"
FAMILY_USERNAME = "wangtaitai"
CONSULTANT_USERNAME = "zixunxiaoli"

#: 内部来源前缀：便于审计与清理，不在普通页面展示
SOURCE_PREFIX = "product-data"
#: 事项编号前缀，避免出现 DEMO / TEST / DEV / SIM 等字样
KEY_PREFIXES = {
    "settlement": "SET",
    "sale_receipt": "SLS",
    "supplier_payment": "PAY",
    "rent": "RNT",
    "refund": "RFD",
    "payroll": "SAL",
    "utility": "UTL",
}

#: 需要退场的旧演示初始化事项（按现金编号识别，且必须确认来自旧 seed）
LEGACY_CASH_KEYS = (
    "DEMO-SETTLE-0001",
    "DEMO-SETTLE-0002",
    "DEMO-PAY-0001",
)

#: 经营者可见的编号里不允许出现的字样
DEMO_KEY_TOKENS = ("DEMO", "TEST", "DEV", "SIM")

#: 核心算例常量（与 backend/tests/fixtures_cash.py 完全一致）
OPENING_BALANCE_CENTS = 3600_00
BUFFER_CENTS = 600_00
CORE_SETTLEMENT_CENTS = 2000_00
CORE_DELAY_DAYS = 2

#: 历史经营数据天数（>= 84 才能形成预测与留底建议）
HISTORY_DAYS = 84
HISTORY_SEED = 20261003

#: 结算记录：17 completed + 3 open，与需求给定的延期序列一致
SETTLEMENT_DELAY_SEQUENCE = (0, 0, 0, 1, 0, 1, 0, 2, 1, 0, 0, 1, 2, 0, 1, 0, 2)


@dataclass
class Action:
    """一条将被执行（或 dry-run 下仅被统计）的动作。"""

    kind: str
    detail: str
    count: int = 1


@dataclass
class Plan:
    actions: list[Action] = field(default_factory=list)

    def add(self, kind: str, detail: str, count: int = 1) -> None:
        self.actions.append(Action(kind=kind, detail=detail, count=count))

    def summary(self) -> dict[str, int]:
        totals: dict[str, int] = {}
        for item in self.actions:
            totals[item.kind] = totals.get(item.kind, 0) + item.count
        return totals


# ---------------------------------------------------------------------------
# 时间工具：统一走 UTC 存库 + Asia/Shanghai 语义
# ---------------------------------------------------------------------------
def resolve_reference(reference_at: str | None) -> datetime:
    """解析期初时点；缺省为 Asia/Shanghai 的当前时间，取整到分钟。"""
    from app.utils.timeutil import APP_TIMEZONE, to_utc  # noqa: PLC0415

    if reference_at:
        parsed = datetime.fromisoformat(reference_at)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=APP_TIMEZONE)
        return to_utc(parsed).replace(second=0, microsecond=0)

    local = datetime.now(APP_TIMEZONE).replace(second=0, microsecond=0)
    return to_utc(local)


def at_local_day(reference: datetime, offset_days: int, hour: int, minute: int = 0) -> datetime:
    """reference 当地日期 + offset_days 的 hour:minute（Asia/Shanghai），返回 UTC。"""
    from app.utils.timeutil import APP_TIMEZONE, to_utc  # noqa: PLC0415

    local_ref = reference.astimezone(APP_TIMEZONE)
    target = datetime.combine(
        local_ref.date() + timedelta(days=offset_days), time(hour, minute)
    ).replace(tzinfo=APP_TIMEZONE)
    return to_utc(target)


def local_day(reference: datetime, offset_days: int) -> date:
    """reference 当地日期 + offset_days（Asia/Shanghai）。"""
    from app.utils.timeutil import APP_TIMEZONE  # noqa: PLC0415

    return reference.astimezone(APP_TIMEZONE).date() + timedelta(days=offset_days)


def cash_key_for(kind: str, day: date, index: int) -> str:
    prefix = KEY_PREFIXES.get(kind, "EVT")
    return f"{prefix}-{day.strftime('%Y%m%d')}-{index:03d}"


def is_demo_key(cash_key: str | None) -> bool:
    """编号里是否含 DEMO / TEST / DEV / SIM 之类字样（经营者可见的编号不允许）。"""
    upper = (cash_key or "").upper()
    return any(token in upper for token in DEMO_KEY_TOKENS)


def legacy_key_for(row, taken: set[str]) -> str:  # noqa: ANN001
    """给退场事项生成中性编号：沿用业务前缀 + 该事项自己的预计日期。

    与未来事项共用同一套编号规则（`KEY_PREFIXES`），因此列表里看起来是
    一笔普通的历史记录，而不是初始化数据。
    """
    from app.utils.timeutil import APP_TIMEZONE  # noqa: PLC0415

    prefix = KEY_PREFIXES.get(row.event_type or "", "EVT")
    scheduled = row.scheduled_at
    day = scheduled.astimezone(APP_TIMEZONE).strftime("%Y%m%d") if scheduled else "00000000"
    for index in range(1, 1000):
        candidate = f"{prefix}-{day}-{index:03d}"
        if candidate not in taken:
            return candidate
    raise RuntimeError(f"无法为退场事项分配中性编号：{row.cash_key}")


# ---------------------------------------------------------------------------
# 未来 7 天事项定义
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class EventSpec:
    kind: str
    title: str
    direction: str
    amount_cents: int
    offset_days: int
    hour: int
    minute: int
    source_label: str
    raw_lines: tuple[str, ...]
    note: str | None = None
    scale: str = "core"  # core | extra


#: 核心四笔：主回归算例，金额与时间不可改动
CORE_EVENTS: tuple[EventSpec, ...] = (
    EventSpec(
        kind="supplier_payment",
        title="鲜食原料采购",
        direction="outflow",
        amount_cents=1400_00,
        offset_days=1,
        hour=8,
        minute=0,
        source_label="10 月采购计划",
        raw_lines=(
            "供应商：城东蔬菜配送中心",
            "结算方式：对公转账",
            "付款时间：次日 08:00 前",
            "金额：1,400.00 元",
            "备注：含生鲜与干货，按周结算",
        ),
        note="按周采购计划，款项次日早间支付",
    ),
    EventSpec(
        kind="settlement",
        title="平台结算款",
        direction="inflow",
        amount_cents=CORE_SETTLEMENT_CENTS,
        offset_days=2,
        hour=9,
        minute=0,
        source_label="平台结算单",
        raw_lines=(
            "结算渠道：外卖平台",
            "结算周期：上一自然周",
            "预计到账：次日 09:00",
            "金额：2,000.00 元",
            "备注：已扣除平台服务费",
        ),
        note="上一结算周期的平台结算款",
    ),
    EventSpec(
        kind="rent",
        title="门店租金",
        direction="outflow",
        amount_cents=1800_00,
        offset_days=2,
        hour=18,
        minute=0,
        source_label="门店租赁计划",
        raw_lines=(
            "出租方：临街商铺业主",
            "结算方式：银行转账",
            "付款时间：每月固定日 18:00",
            "金额：1,800.00 元",
            "备注：含物业费",
        ),
        note="按租赁合同约定的月度租金",
    ),
    EventSpec(
        kind="refund",
        title="顾客退款",
        direction="outflow",
        amount_cents=600_00,
        offset_days=3,
        hour=10,
        minute=0,
        source_label="退款记录",
        raw_lines=(
            "退款单号：RF-当日-001",
            "原因：顾客取消订单",
            "退款方式：原路退回",
            "金额：600.00 元",
            "备注：已与顾客确认",
        ),
        note="顾客取消订单后的原路退款",
    ),
)

#: 额外六笔：用于让事项页、分类图与结算压力有更完整的业务形态。
#:
#: 金额经过**反解校准**，保证核心算例不被破坏：
#:
#: * 额外净流入固定为 +2,200 元
#: * 当前计划：最低余额 1,800（正是 D1 付款后），可提用 1,200
#: * 结算延迟 2 天：最低余额 −200（D2 18:00 付房租后），付款缺口 200、留底缺口 800
#:
#: 推导：延迟后余额在 D2 18:00 触底（3,600−1,400+2,000−1,800 = 2,400；
#: 结算 2,000 移到 D4 后为 400，再付房租 1,800 → −200）。
#: 此后必须先靠额外流入把余额抬回正数，额外支出才不会再击穿 0、
#: 从而把付款缺口推高到 200 以上。
EXTRA_EVENTS: tuple[EventSpec, ...] = (
    EventSpec(
        kind="settlement",
        title="外卖平台结算",
        direction="inflow",
        amount_cents=900_00,
        offset_days=4,
        hour=10,
        minute=0,
        source_label="外卖平台结算单",
        raw_lines=(
            "结算渠道：外卖平台",
            "结算批次：本周第二次",
            "预计到账：当日 10:00",
            "金额：900.00 元",
        ),
        scale="extra",
    ),
    EventSpec(
        kind="supplier_payment",
        title="包装耗材采购",
        direction="outflow",
        amount_cents=500_00,
        offset_days=4,
        hour=16,
        minute=0,
        source_label="耗材采购计划",
        raw_lines=(
            "供应商：包装耗材供应商",
            "结算方式：月结",
            "付款时间：当日 16:00",
            "金额：500.00 元",
        ),
        scale="extra",
    ),
    EventSpec(
        kind="sale_receipt",
        title="门店销售收款",
        direction="inflow",
        amount_cents=1200_00,
        offset_days=5,
        hour=10,
        minute=30,
        source_label="门店收银汇总",
        raw_lines=(
            "收款渠道：门店收银",
            "统计区间：前一日营业款",
            "到账时间：当日 10:30",
            "金额：1,200.00 元",
        ),
        scale="extra",
    ),
    EventSpec(
        kind="payroll",
        title="员工工资",
        direction="outflow",
        amount_cents=400_00,
        offset_days=5,
        hour=17,
        minute=0,
        source_label="工资计划",
        raw_lines=(
            "发放对象：门店员工 2 人",
            "结算方式：银行代发",
            "发放时间：当日 17:00",
            "金额：400.00 元",
        ),
        scale="extra",
    ),
    EventSpec(
        kind="utility",
        title="门店水电费",
        direction="outflow",
        amount_cents=300_00,
        offset_days=6,
        hour=9,
        minute=0,
        source_label="门店账单",
        raw_lines=(
            "费用类型：水费与电费",
            "结算方式：线上缴费",
            "缴费时间：当日 09:00",
            "金额：300.00 元",
        ),
        scale="extra",
    ),
    EventSpec(
        kind="settlement",
        title="团购平台结算",
        direction="inflow",
        amount_cents=600_00,
        offset_days=7,
        hour=12,
        minute=0,
        source_label="团购平台结算单",
        raw_lines=(
            "结算渠道：团购平台",
            "结算批次：月末批次",
            "预计到账：当日 12:00",
            "金额：600.00 元",
        ),
        scale="extra",
    ),
)


#: 事项版本历史：先改到历史值，再改回标准当前数据。
#: 通过 EventService.update_event 产生真实 CashEventRevision，不伪造 JSON。
#:
#: 按**事项标题**索引而不是现金编号前缀：``PAY-`` 前缀同时被「鲜食原料采购」
#: 与「包装耗材采购」使用，按前缀匹配会把后者也改成 1400（实测踩到过）。
REVISION_PLAN: dict[str, tuple[dict, dict]] = {
    "鲜食原料采购": (
        {"amount_cents": 1300_00},
        {"amount_cents": 1400_00},
    ),
    "门店租金": (
        {"note": "按租赁合同约定"},
        {"note": "按租赁合同约定的月度租金"},
    ),
    "顾客退款": (
        {"offset_days": 4},
        {"offset_days": 3},
    ),
    "门店水电费": (
        {"amount_cents": 450_00},
        {"amount_cents": 500_00},
    ),
}


def build_plan(profile, *, reference_at: datetime, dry_run: bool) -> Plan:
    """根据当前库内状态推导出需要执行的动作（幂等：只补缺失的部分）。"""

    from sqlalchemy import select

    from app.models.cash import CashEvent
    from app.models.consultation import ConsultationCase
    from app.models.enhancement import (
        DailyCashHistory,
        ReserveAdviceConfirmation,
        SettlementRecord,
    )
    from app.models.household import Household, HouseholdCard
    from app.models.merchant import BusinessAccountSnapshot

    plan = Plan()
    db = profile._sa_instance_state.session  # type: ignore[attr-defined]

    # --- 经营留底（只在首次建设时校准） ---
    reserve_confirmed = db.scalar(
        select(ReserveAdviceConfirmation.id)
        .where(ReserveAdviceConfirmation.merchant_id == profile.id)
        .limit(1)
    )
    if (
        int(profile.default_buffer_amount_cents) != BUFFER_CENTS
        and reserve_confirmed is None
    ):
        plan.add(
            "buffer",
            f"经营留底校准为 {BUFFER_CENTS // 100} 元"
            f"（当前 {profile.default_buffer_amount_cents // 100} 元）",
        )

    # --- 资金时点 ---
    latest = db.scalar(
        select(BusinessAccountSnapshot)
        .where(BusinessAccountSnapshot.merchant_id == profile.id)
        .order_by(BusinessAccountSnapshot.snapshot_at.desc())
        .limit(1)
    )
    if latest is None or int(latest.opening_balance_cents) != OPENING_BALANCE_CENTS:
        plan.add(
            "snapshot",
            f"登记新的资金时点 期初 {OPENING_BALANCE_CENTS // 100} 元"
            f"（当前 {0 if latest is None else latest.opening_balance_cents // 100} 元，历史时点保留）",
        )

    # --- 旧初始化事项 ---
    existing_keys = {
        row.cash_key: row
        for row in db.scalars(
            select(CashEvent).where(CashEvent.merchant_id == profile.id)
        ).all()
    }
    for key in LEGACY_CASH_KEYS:
        row = existing_keys.get(key)
        if row is not None and row.state == "scheduled":
            plan.add("cancel_legacy", f"取消旧初始化事项 {key}（{row.title}）")

    # 已退场的旧演示编号仍是经营者可见的「事项编号」，
    # 因此要改成中性编号（保留记录与版本，不删除）。
    for _row, old_key, new_key in legacy_renames(db, profile, reference_at):
        plan.add("rename_legacy_key", f"旧初始化事项改编号 {old_key} → {new_key}")

    # --- 未来事项 ---
    for spec in (*CORE_EVENTS, *EXTRA_EVENTS):
        day = local_day(reference_at, spec.offset_days)
        key = cash_key_for(spec.kind, day, _index_of(spec))
        row = existing_keys.get(key)
        if row is None:
            plan.add("event", f"新增事项 {key} {spec.title} {spec.amount_cents // 100} 元")
        elif row.state == "cancelled":
            plan.add("event", f"重建被取消的事项 {key}")

    # --- 事项版本历史 ---
    # 只统计**确实有计划**的事项：否则会把 SET-* / SLS-* 这些本就不该有修订记录的
    # 事项也报成「将生成版本历史」，dry-run 与实际执行不一致。
    for spec in (*CORE_EVENTS, *EXTRA_EVENTS):
        if spec.title not in REVISION_PLAN:
            continue
        day = local_day(reference_at, spec.offset_days)
        key = cash_key_for(spec.kind, day, _index_of(spec))
        row = existing_keys.get(key)
        if row is not None and int(row.current_version or 1) < 2:
            plan.add("revision", f"为 {key}（{spec.title}）生成版本历史")

    # --- 历史经营数据 ---
    history_days = db.scalar(
        select(DailyCashHistory.day).where(DailyCashHistory.merchant_id == profile.id)
    )
    _ = history_days
    existing_history = len(
        db.scalars(
            select(DailyCashHistory.id).where(DailyCashHistory.merchant_id == profile.id)
        ).all()
    )
    if existing_history < HISTORY_DAYS:
        plan.add(
            "history",
            f"补齐历史经营数据至 {HISTORY_DAYS} 天（现有 {existing_history} 天）",
            count=HISTORY_DAYS - existing_history,
        )

    # --- 结算记录 ---
    existing_settlements = len(
        db.scalars(
            select(SettlementRecord.id).where(SettlementRecord.merchant_id == profile.id)
        ).all()
    )
    expected_settlements = len(SETTLEMENT_DELAY_SEQUENCE) + 3
    if existing_settlements < expected_settlements:
        plan.add(
            "settlement",
            f"补齐结算记录至 {expected_settlements} 条（现有 {existing_settlements} 条）",
            count=expected_settlements - existing_settlements,
        )

    # --- 家庭与邀请码 ---
    household = db.scalar(
        select(Household).where(Household.merchant_id == profile.id).limit(1)
    )
    if household is None:
        plan.add("household", "创建家庭「王家小院」（邀请码由后端随机生成）")
    elif is_demo_key(household.invite_code):
        plan.add("rotate_invite_code", "旧演示邀请码更新为随机邀请码")

    # --- 家庭协同卡与评论 ---
    existing_cards = len(
        db.scalars(
            select(HouseholdCard.id).where(HouseholdCard.merchant_id == profile.id)
        ).all()
    )
    if existing_cards < 8:
        plan.add("household_card", f"补建家庭协同卡至 8 张（现有 {existing_cards} 张）")

    # --- 经营咨询 ---
    existing_cases = len(
        db.scalars(
            select(ConsultationCase.id).where(ConsultationCase.merchant_id == profile.id)
        ).all()
    )
    if existing_cases < 8:
        plan.add("consultation", f"补建经营咨询至 8 条（现有 {existing_cases} 条）")

    _ = dry_run
    return plan


def _index_of(spec: EventSpec) -> int:
    """同一类型同一天可能有多笔，用固定序号保证 cash_key 稳定。"""
    same = [
        item
        for item in (*CORE_EVENTS, *EXTRA_EVENTS)
        if item.kind == spec.kind and item.offset_days == spec.offset_days
    ]
    return same.index(spec) + 1


def event_key(spec: EventSpec, reference_at: datetime) -> str:
    return cash_key_for(spec.kind, local_day(reference_at, spec.offset_days), _index_of(spec))


def legacy_renames(db, profile, reference_at: datetime) -> list[tuple[object, str, str]]:  # noqa: ANN001
    """需要改编号的退场事项：``[(row, 旧编号, 新编号), ...]``。

    dry-run 与 apply 共用本函数，保证「计划里写什么」与「实际做什么」一致。
    只处理 :data:`LEGACY_CASH_KEYS` 白名单内的记录，绝不改经营者自己录的事项。
    """
    from sqlalchemy import select  # noqa: PLC0415

    from app.models.cash import CashEvent  # noqa: PLC0415

    rows = list(
        db.scalars(select(CashEvent).where(CashEvent.merchant_id == profile.id)).all()
    )
    taken = {row.cash_key for row in rows}
    # 未来事项的编号也要预留，避免退场事项占用后导致事项无法落库
    for spec in (*CORE_EVENTS, *EXTRA_EVENTS):
        taken.add(event_key(spec, reference_at))

    pairs: list[tuple[object, str, str]] = []
    ordered = sorted(rows, key=lambda row: (row.scheduled_at or datetime.min, row.cash_key))
    for row in ordered:
        if row.cash_key in LEGACY_CASH_KEYS and is_demo_key(row.cash_key):
            new_key = legacy_key_for(row, taken)
            taken.add(new_key)
            pairs.append((row, row.cash_key, new_key))
    return pairs


# ---------------------------------------------------------------------------
# 写入：经营者业务链
# ---------------------------------------------------------------------------


def apply_merchant_data(
    db,
    *,
    profile,
    merchant,
    reference_at: datetime,
    plan: Plan,
    verbose: bool = True,
) -> None:
    """写入经营者的完整业务链：资金时点、事项、来源、版本历史。"""
    from sqlalchemy import select

    from app.models.cash import CashEvent
    from app.models.merchant import BusinessAccountSnapshot
    from app.schemas.cash_event import CashEventCreate, CashEventUpdate
    from app.services.event_service import CashEventService
    from app.utils.timeutil import utcnow

    service = CashEventService(db)

    # 0) 经营留底：核心算例要求 600 元，只在**首次建设**时校准。
    #    留底是经营者自己的业务设置：一旦他在页面上确认过留底建议，
    #    再跑本脚本就绝不能把它改回去（否则会静默覆盖用户的真实决策）。
    from app.models.enhancement import ReserveAdviceConfirmation  # noqa: PLC0415

    confirmed = db.scalar(
        select(ReserveAdviceConfirmation.id)
        .where(ReserveAdviceConfirmation.merchant_id == profile.id)
        .limit(1)
    )
    if int(profile.default_buffer_amount_cents) != BUFFER_CENTS:
        if confirmed is not None:
            if verbose:
                print(
                    f"  · 经营留底已是经营者确认过的 "
                    f"{profile.default_buffer_amount_cents // 100} 元，保持不变"
                )
        else:
            profile.default_buffer_amount_cents = BUFFER_CENTS
            db.commit()
            if verbose:
                print(f"  ✓ 经营留底校准为 {BUFFER_CENTS // 100} 元")

    # 1) 新的资金时点（历史时点保留）
    latest = db.scalar(
        select(BusinessAccountSnapshot)
        .where(BusinessAccountSnapshot.merchant_id == profile.id)
        .order_by(BusinessAccountSnapshot.snapshot_at.desc())
        .limit(1)
    )
    if latest is None or int(latest.opening_balance_cents) != OPENING_BALANCE_CENTS:
        db.add(
            BusinessAccountSnapshot(
                merchant_id=profile.id,
                opening_balance_cents=OPENING_BALANCE_CENTS,
                pending_settlement_cents=0,
                snapshot_at=reference_at,
                source_type="manual",
                created_by=merchant.id,
                note=f"{SOURCE_PREFIX}: 当前经营资金时点",
            )
        )
        db.commit()
        if verbose:
            print(f"  ✓ 资金时点：期初 {OPENING_BALANCE_CENTS // 100} 元")

    # 2) 旧演示初始化事项：取消（保留版本与来源），不物理删除
    for key in LEGACY_CASH_KEYS:
        row = db.scalar(
            select(CashEvent).where(
                CashEvent.merchant_id == profile.id, CashEvent.cash_key == key
            )
        )
        if row is not None and row.state == "scheduled":
            service.cancel_event(
                row, actor=merchant, reason=f"{SOURCE_PREFIX}: 旧初始化数据退场", commit=True
            )
            if verbose:
                print(f"  ✓ 取消旧初始化事项 {key}")

    # 2b) 旧演示编号改成中性编号：经营者能看到「事项编号」，
    #     编号里不能留 DEMO / TEST / DEV / SIM 字样；改编号同样写入版本历史。
    for row, old_key, new_key in legacy_renames(db, profile, reference_at):
        service.update_event(
            row,
            CashEventUpdate(
                cash_key=new_key,
                change_reason=f"{SOURCE_PREFIX}: 旧初始化数据退场，编号规范化",
            ),
            actor=merchant,
            commit=True,
        )
        if verbose:
            print(f"  ✓ 旧初始化事项改编号 {old_key} → {new_key}")

    # 3) 未来事项 + 来源记录
    existing = {
        row.cash_key: row
        for row in db.scalars(
            select(CashEvent).where(CashEvent.merchant_id == profile.id)
        ).all()
    }
    for spec in (*CORE_EVENTS, *EXTRA_EVENTS):
        key = event_key(spec, reference_at)
        row = existing.get(key)
        if row is not None and row.state != "cancelled":
            continue
        if row is not None and row.state == "cancelled":
            # 曾被取消：用新编号重建，保留旧记录
            key = f"{key}-R"
            if key in existing:
                continue

        scheduled_at = at_local_day(reference_at, spec.offset_days, spec.hour, spec.minute)
        raw_content = "\n".join(
            (f"{SOURCE_PREFIX}", f"事项编号：{key}", *spec.raw_lines)
        )
        source = service.create_source_record(
            profile,
            source_type="manual",
            created_by=merchant.id,
            file_name=None,
            row_number=None,
            raw_content=raw_content,
            import_batch_id=None,
        )
        service.create_event(
            profile,
            CashEventCreate(
                cash_key=key,
                title=spec.title,
                direction=spec.direction,
                amount_cents=spec.amount_cents,
                scheduled_at=scheduled_at,
                event_type=spec.kind,
                state="scheduled",
                source_label=spec.source_label,
                note=spec.note,
            ),
            actor=merchant,
            source_record=source,
            commit=True,
        )
        if verbose:
            print(f"  ✓ 事项 {key} {spec.title} {spec.amount_cents // 100} 元")

    # 4) 版本历史：先改到历史值，再改回标准当前数据
    for spec in (*CORE_EVENTS, *EXTRA_EVENTS):
        plan_pair = REVISION_PLAN.get(spec.title)
        if plan_pair is None:
            continue
        key = event_key(spec, reference_at)
        row = db.scalar(
            select(CashEvent).where(
                CashEvent.merchant_id == profile.id, CashEvent.cash_key == key
            )
        )
        if row is None or int(row.current_version or 1) >= 2:
            continue

        historical, corrected = plan_pair
        payload: dict = {}
        for field_name, value in historical.items():
            if field_name == "offset_days":
                payload["scheduled_at"] = at_local_day(
                    reference_at, int(value), spec.hour, spec.minute
                )
            else:
                payload[field_name] = value
        payload["change_reason"] = f"{SOURCE_PREFIX}: 按实际单据修正"
        service.update_event(row, CashEventUpdate(**payload), actor=merchant, commit=True)

        revert: dict = {}
        for field_name in historical:
            if field_name == "offset_days":
                revert["scheduled_at"] = at_local_day(
                    reference_at, spec.offset_days, spec.hour, spec.minute
                )
            else:
                revert[field_name] = corrected[field_name]
        revert["change_reason"] = f"{SOURCE_PREFIX}: 复核后回到确认值"
        service.update_event(row, CashEventUpdate(**revert), actor=merchant, commit=True)
        if verbose:
            print(f"  ✓ 版本历史 {key} → v{row.current_version}")

    _ = (plan, utcnow)


# ---------------------------------------------------------------------------
# 写入：历史经营数据与结算记录
# ---------------------------------------------------------------------------
def apply_history_and_settlements(
    db,
    *,
    profile,
    merchant,
    reference_at: datetime,
    verbose: bool = True,
) -> None:
    from sqlalchemy import select

    from app.models.enhancement import (
        SETTLEMENT_COMPLETED,
        SETTLEMENT_OPEN,
        DailyCashHistory,
        SettlementRecord,
    )
    from app.utils.timeutil import utcnow

    existing_days = {
        row.day
        for row in db.scalars(
            select(DailyCashHistory).where(DailyCashHistory.merchant_id == profile.id)
        ).all()
    }
    created_days = 0
    for row in build_history_rows(reference_at):
        if row["day"] in existing_days:
            continue
        db.add(
            DailyCashHistory(
                merchant_id=profile.id,
                day=row["day"],
                complete=True,
                inflow_cents=row["inflow_cents"],
                outflow_cents=row["outflow_cents"],
                source_refs=[row["source_label"]],
                completeness_confirmed=True,
                note=f"{SOURCE_PREFIX}: 已确认完整的自然日",
                created_by=merchant.id,
            )
        )
        created_days += 1
    db.commit()
    if verbose:
        print(f"  ✓ 历史经营数据：新增 {created_days} 天（连续完整）")

    # 结算记录：17 completed + 3 open
    existing = {
        row.external_key
        for row in db.scalars(
            select(SettlementRecord).where(SettlementRecord.merchant_id == profile.id)
        ).all()
    }
    channel = "平台结算"
    created_settlements = 0
    for index, delay in enumerate(SETTLEMENT_DELAY_SEQUENCE, start=1):
        scheduled_day = local_day(reference_at, -(len(SETTLEMENT_DELAY_SEQUENCE) - index + 20))
        external_key = f"{SOURCE_PREFIX.upper()}-STL-{scheduled_day.strftime('%Y%m%d')}-{index:03d}"
        if external_key in existing:
            continue
        scheduled_at = datetime.combine(
            scheduled_day, time(9, 0), tzinfo=UTC
        )
        actual_at = scheduled_at + timedelta(days=int(delay))
        db.add(
            SettlementRecord(
                merchant_id=profile.id,
                external_key=external_key,
                channel=channel,
                scheduled_at=scheduled_at,
                actual_at=actual_at,
                known_at=actual_at,
                status=SETTLEMENT_COMPLETED,
                source_ref=f"{SOURCE_PREFIX}:settlement:{index}",
                note=f"{SOURCE_PREFIX}: 实际延期 {delay} 天",
                created_by=merchant.id,
            )
        )
        created_settlements += 1
    for index in range(3):
        scheduled_day = local_day(reference_at, -(3 - index))
        external_key = f"{SOURCE_PREFIX.upper()}-STL-OPEN-{scheduled_day.strftime('%Y%m%d')}"
        if external_key in existing:
            continue
        db.add(
            SettlementRecord(
                merchant_id=profile.id,
                external_key=external_key,
                channel=channel,
                scheduled_at=datetime.combine(scheduled_day, time(9, 0), tzinfo=UTC),
                actual_at=None,
                known_at=utcnow(),
                status=SETTLEMENT_OPEN,
                source_ref=f"{SOURCE_PREFIX}:settlement:open:{index}",
                note=f"{SOURCE_PREFIX}: 尚未到账",
                created_by=merchant.id,
            )
        )
        created_settlements += 1
    db.commit()
    if verbose:
        print(f"  ✓ 结算记录：新增 {created_settlements} 条（completed 17 / open 3）")


# ---------------------------------------------------------------------------
# 写入：家庭协同
# ---------------------------------------------------------------------------
#: 8 张协同卡：4 决策 + 2 风险 + 2 变更，分布在最近 14 天。
#: 字段选择遵循「最小披露」：决策卡给出可提用与计划提用；风险卡给出风险摘要与缺口；
#: 变更卡只表示「有一笔事项变更过」，不带任何事项细节。
@dataclass(frozen=True)
class CardSpec:
    card_type: str
    days_ago: int
    title: str
    planned_cents: int | None
    shared_fields: tuple[str, ...]
    reaction: str | None
    read: bool
    comments: tuple[str, ...] = ()


CARD_SPECS: tuple[CardSpec, ...] = (
    CardSpec(
        card_type="decision",
        days_ago=13,
        title="家庭生活费安排",
        planned_cents=800_00,
        shared_fields=("max_withdrawable", "planned_amount", "limiting_point", "risk_summary"),
        reaction="agree",
        read=True,
        comments=("这周家庭开支可以先按 800 元安排。",),
    ),
    CardSpec(
        card_type="decision",
        days_ago=11,
        title="周末家庭开支",
        planned_cents=600_00,
        shared_fields=("max_withdrawable", "planned_amount", "limiting_point"),
        reaction=None,
        read=True,
    ),
    CardSpec(
        card_type="decision",
        days_ago=8,
        title="临时家庭支出",
        planned_cents=500_00,
        shared_fields=("max_withdrawable", "planned_amount", "risk_summary", "payment_gap"),
        reaction="discuss",
        read=True,
        comments=(
            "这笔支出建议等到结算款到账后再安排。",
            "先按当前计划走，到账后再看能否调整。",
        ),
    ),
    CardSpec(
        card_type="decision",
        days_ago=4,
        title="本周家庭资金安排",
        planned_cents=1000_00,
        shared_fields=("max_withdrawable", "planned_amount", "limiting_point", "limiting_balance"),
        reaction=None,
        read=False,
        comments=("房租日前先不增加家庭支出。",),
    ),
    CardSpec(
        card_type="risk",
        days_ago=10,
        title="结算延迟风险提醒",
        planned_cents=None,
        shared_fields=(
            "risk_summary",
            "payment_gap",
            "buffer_gap",
            "limiting_point",
            "limiting_balance",
        ),
        reaction=None,
        read=True,
    ),
    CardSpec(
        card_type="risk",
        days_ago=5,
        title="低于经营留底提醒",
        planned_cents=None,
        shared_fields=("risk_summary", "buffer_gap", "limiting_balance"),
        reaction=None,
        read=False,
    ),
    CardSpec(
        card_type="revision",
        days_ago=6,
        title="结算到账日期修正",
        planned_cents=None,
        shared_fields=("revision_summary", "limiting_point"),
        reaction=None,
        read=True,
    ),
    CardSpec(
        card_type="revision",
        days_ago=2,
        title="采购金额修正",
        planned_cents=None,
        shared_fields=("revision_summary", "risk_summary"),
        reaction=None,
        read=False,
    ),
)


def apply_household_data(
    db,
    *,
    profile,
    merchant,
    family_user,
    reference_at: datetime,
    verbose: bool = True,
) -> None:
    """建立家庭协同历史：家庭、成员关系、8 张卡片、表态与评论。"""
    from sqlalchemy import select

    from app.models.cash import AnalysisResult, CashEvent
    from app.models.household import (
        Household,
        HouseholdCard,
        HouseholdCardComment,
        HouseholdCardRecipient,
        HouseholdMembership,
    )
    from app.services.household_service import HouseholdService
    from app.utils.timeutil import utcnow

    service = HouseholdService(db)

    household = db.scalar(select(Household).where(Household.merchant_id == profile.id))
    if household is None:
        # 走产品自身的开通路径：邀请码由后端随机生成，不写死固定串
        # （旧初始化数据里的 DEVDEMO1 就是写死邀请码留下的）。
        service.create_household(profile, merchant, "王家小院")
        household = db.scalar(select(Household).where(Household.merchant_id == profile.id))
        if household is None:  # pragma: no cover - 创建失败会直接抛错
            raise RuntimeError("家庭创建失败")
        if verbose:
            print("  ✓ 创建家庭「王家小院」")
    elif is_demo_key(household.invite_code):
        # 旧演示邀请码会直接显示在「家庭协同」页上，必须换成随机码；
        # 邀请码本身是凭证，只报告长度，不打印内容。
        rotated = service.rotate_invite_code(household)
        if verbose:
            print(f"  ✓ 旧演示邀请码已更新为随机邀请码（{len(rotated)} 位）")

    membership = db.scalar(
        select(HouseholdMembership).where(
            HouseholdMembership.household_id == household.id,
            HouseholdMembership.user_id == family_user.id,
        )
    )
    if membership is None:
        db.add(
            HouseholdMembership(
                household_id=household.id,
                user_id=family_user.id,
                role="member",
                relation_label="配偶",
                status="active",
                joined_at=utcnow(),
                decided_by=merchant.id,
                decided_at=utcnow(),
            )
        )
        db.commit()
        if verbose:
            print("  ✓ 家庭成员关系已建立（配偶 / active）")

    existing_cards = len(
        db.scalars(
            select(HouseholdCard.id).where(HouseholdCard.merchant_id == profile.id)
        ).all()
    )
    if existing_cards >= len(CARD_SPECS):
        if verbose:
            print(f"  · 协同卡已存在 {existing_cards} 张，跳过")
        return

    analysis = db.scalar(
        select(AnalysisResult)
        .where(AnalysisResult.merchant_id == profile.id, AnalysisResult.is_stale.is_(False))
        .order_by(AnalysisResult.created_at.desc())
        .limit(1)
    )
    events = db.scalars(
        select(CashEvent)
        .where(CashEvent.merchant_id == profile.id, CashEvent.state == "scheduled")
        .order_by(CashEvent.scheduled_at)
    ).all()
    settlement_event = next((e for e in events if e.event_type == "settlement"), None)

    created = 0
    for spec in CARD_SPECS:
        cash_event_id = (
            settlement_event.id
            if spec.card_type == "revision" and settlement_event is not None
            else None
        )
        card = service.create_card(
            profile,
            household,
            merchant,
            card_type=spec.card_type,
            shared_fields=list(spec.shared_fields),
            title=spec.title,
            summary=None,
            analysis_result_id=analysis.id if analysis is not None else None,
            cash_event_id=cash_event_id,
            planned_amount_cents=spec.planned_cents,
        )
        created_at = reference_at - timedelta(days=spec.days_ago)
        card.created_at = created_at
        card.updated_at = created_at

        recipient = db.scalar(
            select(HouseholdCardRecipient).where(
                HouseholdCardRecipient.card_id == card.id,
                HouseholdCardRecipient.user_id == family_user.id,
            )
        )
        if recipient is not None:
            recipient.is_read = spec.read
            if spec.read:
                recipient.read_at = created_at + timedelta(hours=3)
            if spec.reaction:
                recipient.reaction = spec.reaction
                recipient.reacted_at = created_at + timedelta(hours=4)

        for index, content in enumerate(spec.comments):
            db.add(
                HouseholdCardComment(
                    card_id=card.id,
                    user_id=family_user.id,
                    content=content,
                    created_at=created_at + timedelta(hours=5 + index),
                )
            )
        db.commit()
        created += 1
        if verbose:
            print(f"  ✓ 协同卡「{spec.title}」（{spec.card_type}）")

    if verbose:
        print(f"  ✓ 协同卡合计新增 {created} 张，含表态与评论")


# ---------------------------------------------------------------------------
# 写入：经营咨询
# ---------------------------------------------------------------------------
#: 8 条咨询：2 submitted / 2 under_review / 1 need_more_information / 2 verified / 1 closed
CONSULTATION_SPECS: tuple[tuple[str, str, str, str], ...] = (
    ("平台结算到账时间核实", "settlement_time", "这笔平台结算款原定次日上午到账，账户还没有收到，想确认结算进度。", "submitted"),
    ("外卖平台结算状态核对", "settlement_time", "外卖平台的结算款显示已结算，但账户未入账，想核对一下状态。", "submitted"),
    ("顾客退款状态确认", "missing_arrival", "顾客取消订单后的退款已经提交，想确认是否已经完成。", "under_review"),
    ("平台服务费扣款核对", "fee_unknown", "本笔结算的到账金额与结算单差额较大，想核对服务费扣款。", "under_review"),
    ("团购结算批次查询", "settlement_time", "团购平台的结算批次编号与账单不一致，请协助查询。", "need_more_information"),
    ("结算款预计到账时间确认", "settlement_time", "想确认这笔结算款预计到账的具体时间。", "verified"),
    ("收款入账时间核对", "settlement_time", "门店收款已到账，但入账时间与记录不一致，请协助核对。", "verified"),
    ("某笔经营事项补充材料", "other", "这笔支出缺少对应的采购单据，想补充材料。", "closed"),
)


def apply_consultation_data(
    db,
    *,
    profile,
    merchant,
    consultant_user,
    verbose: bool = True,
) -> None:
    """建立经营咨询历史，覆盖全部状态并带完整时间线。"""
    from sqlalchemy import select

    from app.models.cash import CashEvent
    from app.models.consultation import ConsultationCase
    from app.services.consultation_service import ConsultationService

    existing = len(
        db.scalars(
            select(ConsultationCase.id).where(ConsultationCase.merchant_id == profile.id)
        ).all()
    )
    if existing >= len(CONSULTATION_SPECS):
        if verbose:
            print(f"  · 咨询已存在 {existing} 条，跳过")
        return

    events = db.scalars(
        select(CashEvent)
        .where(CashEvent.merchant_id == profile.id, CashEvent.state == "scheduled")
        .order_by(CashEvent.scheduled_at)
    ).all()
    if not events:
        if verbose:
            print("  · 没有可用事项，跳过咨询建设")
        return

    service = ConsultationService(db)
    created = 0
    for index, (title, qtype, question, target) in enumerate(CONSULTATION_SPECS):
        event = events[index % len(events)]
        case = service.create_case(
            profile,
            merchant,
            cash_event_id=event.id,
            question_type=qtype,
            question=question,
            ai_draft=None,
            status="submitted",
        )
        if target in ("under_review", "need_more_information", "verified", "closed"):
            service.start_review(case, consultant_user)
        if target == "need_more_information":
            service.request_information(
                case, consultant_user, "请提供该笔结算的结算单截图编号，便于核对。"
            )
        if target in ("verified", "closed"):
            service.verify(
                case,
                consultant_user,
                resolution_summary=f"{title}：已核对平台流水，结算状态正常。",
                resolution_fields={"settlement_status": "已核对"},
            )
        if target == "closed":
            service.close(case, consultant_user, f"{title}：已完成核对并归档。")

        created += 1
        if verbose:
            print(f"  ✓ 咨询「{title}」→ {target}")

    if verbose:
        print(f"  ✓ 咨询合计新增 {created} 条，含完整处理时间线")


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# 历史经营数据（固定种子、可复现、体现小餐饮规律）
# ---------------------------------------------------------------------------
def build_history_rows(reference_at: datetime, days: int = HISTORY_DAYS) -> list[dict]:
    """生成连续完整自然日历史。

    规律：周末销售更高、工作日相对稳定、采购有周期性、偶尔出现较大采购。
    使用固定种子，因此同一 ``reference_at`` 反复运行结果完全一致。
    """
    rng = random.Random(HISTORY_SEED)
    rows: list[dict] = []
    for offset in range(days, 0, -1):
        day = local_day(reference_at, -offset)
        weekday = day.weekday()
        weekend_lift = 1.35 if weekday >= 5 else 1.0

        base_inflow = rng.randint(900_00, 1800_00)
        inflow = int(base_inflow * weekend_lift)

        base_outflow = rng.randint(400_00, 1100_00)
        # 每 10~20 天出现一次较大采购
        if rng.random() < 0.08:
            base_outflow += rng.randint(400_00, 600_00)
        outflow = base_outflow

        rows.append(
            {
                "day": day.isoformat(),
                "inflow_cents": min(inflow, 3000_00),
                "outflow_cents": min(outflow, 1700_00),
                "source_label": f"{SOURCE_PREFIX}:daily:{day.isoformat()}",
            }
        )
    return rows


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    # Windows 控制台默认 GBK，进度行里的「✓」会让脚本在中途抛 UnicodeEncodeError：
    # 数据可能已经写入，使用者却看到报错。统一按 UTF-8 输出。
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):  # pragma: no cover - 仅少数受限环境
                pass

    parser = argparse.ArgumentParser(
        description="为正式业务账号建设完整产品数据（幂等，仅增量）"
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="只报告将执行的动作")
    mode.add_argument("--apply", action="store_true", help="实际写入数据")
    parser.add_argument(
        "--reference-at",
        default=None,
        help="期初时点（ISO-8601，可带时区）；缺省为 Asia/Shanghai 当前时间",
    )
    args = parser.parse_args(argv)

    from sqlalchemy import select

    from app.core.database import SessionLocal
    from app.models.user import ROLE_MERCHANT, User

    reference_at = resolve_reference(args.reference_at)
    print(f"期初时点（UTC）：{reference_at.isoformat()}")
    print(f"模式：{'dry-run（不写入）' if args.dry_run else 'apply（写入）'}")
    print()

    db = SessionLocal()
    try:
        # 1) 正式账号必须存在：缺失即停止，绝不新建
        missing: list[str] = []
        users: dict[str, User] = {}
        for username in (MERCHANT_USERNAME, FAMILY_USERNAME, CONSULTANT_USERNAME):
            user = db.scalar(select(User).where(User.username == username))
            if user is None:
                missing.append(username)
            else:
                users[username] = user
        if missing:
            print(f"× 缺少正式账号：{', '.join(missing)}", file=sys.stderr)
            print("  本脚本不会创建账号或设置密码，请先由上层流程开通。", file=sys.stderr)
            return 2

        merchant = users[MERCHANT_USERNAME]
        if ROLE_MERCHANT not in merchant.role_names():
            print(f"× {MERCHANT_USERNAME} 不是经营主体账户", file=sys.stderr)
            return 2

        from app.services.merchant_service import MerchantService

        profile = MerchantService(db).require_by_user(merchant.id)

        print("=== 正式账号 ===")
        for username, user in users.items():
            print(f"  {username:16} {user.display_name:8} {','.join(user.role_names())}")
        print(f"  经营主体：{profile.business_name}")
        print()

        plan = build_plan(profile, reference_at=reference_at, dry_run=args.dry_run)

        print("=== 计划动作 ===")
        if not plan.actions:
            print("  无待执行动作（数据已就绪，幂等）")
        for item in plan.actions:
            suffix = f" ×{item.count}" if item.count > 1 else ""
            print(f"  [{item.kind}] {item.detail}{suffix}")
        print()
        print("=== 汇总 ===")
        for kind, total in sorted(plan.summary().items()):
            print(f"  {kind:16} {total}")
        print()

        if args.dry_run:
            print("dry-run 结束：未写入任何数据。")
            return 0

        print("=== 执行 ===")
        apply_merchant_data(
            db,
            profile=profile,
            merchant=merchant,
            reference_at=reference_at,
            plan=plan,
        )
        print()
        apply_history_and_settlements(
            db,
            profile=profile,
            merchant=merchant,
            reference_at=reference_at,
        )
        print()
        apply_household_data(
            db,
            profile=profile,
            merchant=merchant,
            family_user=users[FAMILY_USERNAME],
            reference_at=reference_at,
        )
        print()
        apply_consultation_data(
            db,
            profile=profile,
            merchant=merchant,
            consultant_user=users[CONSULTANT_USERNAME],
        )
        print()
        print("apply 完成。")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
