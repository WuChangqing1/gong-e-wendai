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

两个阶段
--------
* ``ensure``：只补缺失的对象。数量够了并不代表内容正确，因此所有检查都按
  **语义 Spec**（标题 / 问题文案 / 编号前缀）逐条比对，而不是 ``count < N`` 就跳过。
* ``repair``（:func:`repair_product_data`）：修正**已经存在但内容错误**的预置对象，
  覆盖咨询关联与结论、更正卡关联与 payload、结算渠道、风险卡内容。
  修复范围严格限定在本脚本自己产生的数据（见「识别依据」一节），
  并保留已读状态、表态、评论、``created_at`` 与咨询时间线。

关联关系全部是**语义绑定**：咨询与更正卡都按事项标题精确查找预置事项
（``resolve_event_by_title``），找不到就报错停止 —— 绝不退回
``events[index % len(events)]`` 那种按列表位置关联的做法。

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

#: 结算渠道唯一 canonical 值。
#: 延期压力按渠道统计时用的渠道名就是结算类 ``CashEvent.source_label``
#: （见 ``EnhancementService._channel_of``）；结算款事项的来源说明是「平台结算单」，
#: 因此 SettlementRecord.channel 必须与它逐字一致，否则会出现
#: 「该渠道已完成 0 笔」的假象。
SETTLEMENT_CHANNEL = "平台结算单"
#: 本脚本历史上写入过的错误渠道值：只在确认记录来自本脚本时才修正。
LEGACY_SETTLEMENT_CHANNELS = ("平台结算",)
#: 本脚本预置结算记录的编号 / 来源前缀（识别依据，绝不触碰用户真实导入的渠道数据）
SETTLEMENT_KEY_PREFIX = f"{SOURCE_PREFIX.upper()}-STL-"
SETTLEMENT_SOURCE_PREFIX = f"{SOURCE_PREFIX}:settlement"

#: 咨询结论允许回写的字段：与 ``ConsultationService._resolution_to_event_fields``
#: 的白名单严格一致。系统无法回写的内部字段（例如 ``settlement_status``）
#: 绝不允许写进 ``resolution_fields``。
RESOLUTION_FIELD_WHITELIST = (
    "amount_cents",
    "scheduled_at",
    "state",
    "title",
    "note",
    "source_label",
)


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
def preset_snapshot_at(db, merchant_id: str) -> datetime | None:  # noqa: ANN001
    """本脚本登记的那条资金时点的时点值（没有则 None）。"""
    from sqlalchemy import select  # noqa: PLC0415

    from app.models.merchant import BusinessAccountSnapshot  # noqa: PLC0415

    rows = db.scalars(
        select(BusinessAccountSnapshot)
        .where(BusinessAccountSnapshot.merchant_id == merchant_id)
        .order_by(BusinessAccountSnapshot.snapshot_at.desc())
    ).all()
    for row in rows:
        note = row.note or ""
        if note.startswith(SOURCE_PREFIX):
            return row.snapshot_at
    return rows[0].snapshot_at if rows else None


def resolve_reference(
    reference_at: str | None,
    *,
    db=None,  # noqa: ANN001
    merchant_id: str | None = None,
) -> datetime:
    """解析期初时点。

    显式传入就用传入值；否则**锚定到已经登记的经营资金时点**
    （优先本脚本登记的那条，其次最近一条），都没有才用当前时间。

    为什么不能默认「现在」：事项编号、结算编号都带日期
    （``PAY-20261007-001``），隔一天再跑就会算出全新的一批编号，
    于是把整套预置数据又建一遍。锚定到首次建设的时点后，
    任何一天重跑都得到同一批编号，幂等才真正成立。
    """
    from app.utils.timeutil import APP_TIMEZONE, to_utc  # noqa: PLC0415

    if reference_at:
        parsed = datetime.fromisoformat(reference_at)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=APP_TIMEZONE)
        return to_utc(parsed).replace(second=0, microsecond=0)

    if db is not None and merchant_id:
        anchor = preset_snapshot_at(db, merchant_id)
        if anchor is not None:
            return to_utc(anchor).replace(second=0, microsecond=0)

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

#: 全部预置事项（按标题索引）：咨询与更正卡都靠**标题精确匹配**关联事项。
ALL_EVENT_SPECS: tuple[EventSpec, ...] = (*CORE_EVENTS, *EXTRA_EVENTS)

EVENT_SPECS_BY_TITLE: dict[str, EventSpec] = {}
for _spec in ALL_EVENT_SPECS:
    if _spec.title in EVENT_SPECS_BY_TITLE:  # pragma: no cover - 定义期自检
        raise RuntimeError(f"预置事项标题重复，语义绑定无法唯一确定：{_spec.title}")
    EVENT_SPECS_BY_TITLE[_spec.title] = _spec
del _spec


def require_event_spec(title: str) -> EventSpec:
    """按标题精确取预置事项定义。

    找不到就**直接报错停止**：绝不退回 ``events[index % len(events)]``
    这种按列表位置关联的做法（那正是「问的是结算款、关联的却是采购」的根因）。
    """
    spec = EVENT_SPECS_BY_TITLE.get(title)
    if spec is None:
        raise RuntimeError(
            f"声明的关联事项标题不存在：{title!r}；"
            f"可选标题：{'、'.join(EVENT_SPECS_BY_TITLE)}"
        )
    return spec


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
    "平台结算款": (
        # 历史值：预计到账 D3 09:00；复核后回到确认值 D2 09:00。
        # 「结算到账日期修正」卡依赖这段真实版本历史。
        {"offset_days": 3},
        {"offset_days": 2},
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


# ---------------------------------------------------------------------------
# 分析口径：风险卡必须挂真实分析结果，不允许伪造状态或金额
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class AnalysisSpec:
    key: str
    mode: str
    delay_days: int
    detail: str


#: 生成顺序即「新鲜度」顺序：最后一条是商户熟悉的「按当前计划」，
#: 于是最新一次分析结果不会被风险口径顶掉。
ANALYSIS_SPECS: tuple[AnalysisSpec, ...] = (
    AnalysisSpec(key="settlement_delay", mode="delayed", delay_days=2, detail="到账延迟 2 天"),
    AnalysisSpec(key="below_buffer", mode="delayed", delay_days=1, detail="到账延迟 1 天"),
    AnalysisSpec(key="current_plan", mode="current_plan", delay_days=0, detail="按当前计划"),
)

ANALYSIS_SPECS_BY_KEY: dict[str, AnalysisSpec] = {spec.key: spec for spec in ANALYSIS_SPECS}

#: 风险卡要求的真实分析状态。引擎产不出该状态时**不伪造**，
#: 改用与实际 payload 相符的普通资金提醒标题。
RISK_TITLE_BY_STATUS = {
    "FEASIBLE": "资金安排提醒",
    "BELOW_BUFFER": "经营留底提醒",
    "PAYMENT_GAP": "资金缺口提醒",
    "INPUT_INCOMPLETE": "资金信息待补充",
}


def build_plan(profile, *, merchant, reference_at: datetime, dry_run: bool) -> Plan:
    """根据当前库内状态推导出需要执行的动作。

    分两部分，且**幂等**：

    * ``ensure``：只补缺失的对象（绝不因为「数量够了」就跳过内容校验）；
    * ``repair``：只修**明确属于本脚本预置数据集**、且与 Spec 不一致的对象。

    dry-run 和 apply 共用本函数，保证「计划里写什么」与「实际做什么」一致。
    """

    from sqlalchemy import select

    from app.models.cash import CashEvent
    from app.models.enhancement import (
        DailyCashHistory,
        ReserveAdviceConfirmation,
    )
    from app.models.household import Household
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
    for spec in ALL_EVENT_SPECS:
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
    for spec in ALL_EVENT_SPECS:
        if spec.title not in REVISION_PLAN:
            continue
        day = local_day(reference_at, spec.offset_days)
        key = cash_key_for(spec.kind, day, _index_of(spec))
        row = existing_keys.get(key)
        if row is not None and int(row.current_version or 1) < 2:
            plan.add("revision", f"为 {key}（{spec.title}）生成版本历史")

    # --- 历史经营数据 ---
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
    # 按**编号**逐条判断，而不是「总数够了就跳过」：经营者自己导入的结算记录
    # 不应该被算成预置数据，从而把预置记录挤掉。
    existing_keys_set = {row.external_key for row in script_settlements(db, profile)}
    missing_settlements = [
        key for key in expected_settlement_keys(reference_at) if key not in existing_keys_set
    ]
    if missing_settlements:
        plan.add(
            "settlement",
            f"补齐结算记录 {len(missing_settlements)} 条"
            f"（渠道统一为「{SETTLEMENT_CHANNEL}」）",
            count=len(missing_settlements),
        )

    # --- 家庭与邀请码 ---
    household = db.scalar(
        select(Household).where(Household.merchant_id == profile.id).limit(1)
    )
    if household is None:
        plan.add("household", "创建家庭「王家小院」（邀请码由后端随机生成）")
    elif is_demo_key(household.invite_code):
        plan.add("rotate_invite_code", "旧演示邀请码更新为随机邀请码")

    # --- 分析结果（风险卡必须挂真实分析） ---
    for spec in ANALYSIS_SPECS:
        if find_matching_analysis(db, profile, spec, reference_at) is None:
            plan.add("analysis", f"生成分析结果（{spec.detail}）")

    # --- 家庭协同卡与评论 ---
    existing_cards = _script_cards(db, profile, merchant)
    for spec in CARD_SPECS:
        if not any(
            (spec.card_type, title) in existing_cards for title in card_title_candidates(spec)
        ):
            link = f"，关联 {spec.event_title}" if spec.event_title else ""
            plan.add("household_card", f"补建协同卡「{spec.title}」（{spec.card_type}）{link}")

    # --- 经营咨询 ---
    existing_cases = _script_cases(db, profile, merchant)
    for spec in CONSULTATION_SPECS:
        if spec.question not in existing_cases:
            plan.add(
                "consultation",
                f"补建咨询「{spec.title}」（关联 {spec.event_title} / {spec.target_status}）",
            )

    # --- 修复阶段：只针对已存在的预置对象 ---
    for case, issues in consultation_repairs(db, profile, merchant, reference_at):
        plan.add("repair_consultation", f"{case.case_no}「{case.question[:16]}…」：{'、'.join(issues)}")
    for card, issues in card_repairs(db, profile, merchant, reference_at):
        plan.add("repair_card", f"{card.card_type}「{card.title}」：{'、'.join(issues)}")
    for row in settlement_channel_repairs(db, profile):
        legacy = "（已知旧值）" if row.channel in LEGACY_SETTLEMENT_CHANNELS else ""
        plan.add(
            "repair_settlement_channel",
            f"结算记录 {row.external_key} 渠道 {row.channel} → {SETTLEMENT_CHANNEL}{legacy}",
        )

    _ = dry_run
    return plan


def expected_settlement_keys(reference_at: datetime) -> list[str]:
    """本脚本应当存在的结算记录编号（与写入逻辑共用同一套推导）。"""
    keys: list[str] = []
    total = len(SETTLEMENT_DELAY_SEQUENCE)
    for index in range(1, total + 1):
        day = local_day(reference_at, -(total - index + 20))
        keys.append(f"{SETTLEMENT_KEY_PREFIX}{day.strftime('%Y%m%d')}-{index:03d}")
    for index in range(3):
        day = local_day(reference_at, -(3 - index))
        keys.append(f"{SETTLEMENT_KEY_PREFIX}OPEN-{day.strftime('%Y%m%d')}")
    return keys


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
# 语义绑定与「属于本脚本预置数据集」的识别依据
#
# 判断依据全部是**语义**的（来源原文前缀 / 标题 / 问题文案 / 创建人 / 编号前缀），
# 不依赖任何硬编码日期（例如 20261004），因此新环境靠 Spec 就能建立与修复。
# ---------------------------------------------------------------------------


def is_product_data_event(row) -> bool:  # noqa: ANN001
    """事项是否由本脚本预置：来源原文首行带统一前缀。"""
    record = getattr(row, "source_record", None)
    raw = getattr(record, "raw_content", None) if record is not None else None
    return (raw or "").splitlines()[:1] == [SOURCE_PREFIX]


def _try_resolve_event(db, profile, title: str, reference_at: datetime):  # noqa: ANN001
    """按标题取本脚本预置的事项；不存在返回 ``None``（修复阶段需要它做探测）。"""
    from sqlalchemy import select  # noqa: PLC0415

    from app.models.cash import CashEvent  # noqa: PLC0415

    spec = require_event_spec(title)
    rows = [
        row
        for row in db.scalars(
            select(CashEvent).where(
                CashEvent.merchant_id == profile.id, CashEvent.title == title
            )
        ).all()
        if row.state != "cancelled" and is_product_data_event(row)
    ]
    if not rows:
        return None
    expected_key = event_key(spec, reference_at)
    rows.sort(key=lambda row: (row.cash_key != expected_key, row.scheduled_at, row.cash_key))
    return rows[0]


def resolve_event_by_title(db, profile, title: str, reference_at: datetime):  # noqa: ANN001
    """按标题取本脚本预置的事项；不存在就报错停止。

    同标题可能被经营者自己录过一笔，因此只接受来源原文带预置前缀的记录；
    若同时存在多笔（例如曾被取消后以 ``-R`` 重建），优先取本次期初时点推导出的编号。
    """
    row = _try_resolve_event(db, profile, title, reference_at)
    if row is None:
        raise RuntimeError(
            f"预置事项缺失：{title!r}（请先执行 ensure 阶段建设事项，再执行修复）"
        )
    return row


def _script_cases(db, profile, merchant) -> dict[str, object]:  # noqa: ANN001
    """本脚本预置的经营咨询：``{问题文案: case}``。

    识别依据（全部命中才算）：咨询编号形如 ``ZX`` 前缀 + 属于该经营主体 +
    创建人是该经营者 + 问题文案与 :data:`CONSULTATION_SPECS` 逐字一致。
    """
    from sqlalchemy import select  # noqa: PLC0415

    from app.models.consultation import ConsultationCase  # noqa: PLC0415

    known = {spec.question for spec in CONSULTATION_SPECS}
    found: dict[str, object] = {}
    for row in db.scalars(
        select(ConsultationCase).where(ConsultationCase.merchant_id == profile.id)
    ).all():
        if row.created_by != merchant.id:
            continue
        if not (row.case_no or "").startswith("ZX"):
            continue
        if row.question in known and row.question not in found:
            found[row.question] = row
    return found


def card_title_candidates(spec: CardSpec) -> tuple[str, ...]:
    """卡片识别标题：预置标题 + 状态不符时可能退化的普通提醒标题。

    识别时把所有可能的退化标题都算进来，避免「标题被改过就找不到卡片、
    于是又新建一张」的重复建设。
    """
    if spec.required_status is None:
        return (spec.title,)
    return (spec.title, *RISK_TITLE_BY_STATUS.values())


def _script_cards(db, profile, merchant) -> dict[tuple[str, str], object]:  # noqa: ANN001
    """本脚本预置的协同卡：``{(card_type, 标题): card}``。

    只接受「创建人是该经营者 + 标题命中预置标题（含退化标题）」的卡片。
    """
    from sqlalchemy import select  # noqa: PLC0415

    from app.models.household import HouseholdCard  # noqa: PLC0415

    known: set[tuple[str, str]] = set()
    for spec in CARD_SPECS:
        for title in card_title_candidates(spec):
            known.add((spec.card_type, title))

    found: dict[tuple[str, str], object] = {}
    for row in db.scalars(
        select(HouseholdCard).where(HouseholdCard.merchant_id == profile.id)
    ).all():
        if row.created_by != merchant.id:
            continue
        key = (row.card_type, row.title)
        if key in known and key not in found:
            found[key] = row
    return found


def script_settlements(db, profile) -> list[object]:  # noqa: ANN001
    """本脚本预置的结算记录：编号或来源前缀命中统一前缀。"""
    from sqlalchemy import select  # noqa: PLC0415

    from app.models.enhancement import SettlementRecord  # noqa: PLC0415

    rows = db.scalars(
        select(SettlementRecord).where(SettlementRecord.merchant_id == profile.id)
    ).all()
    return [
        row
        for row in rows
        if (row.external_key or "").startswith(SETTLEMENT_KEY_PREFIX)
        or (row.source_ref or "").startswith(SETTLEMENT_SOURCE_PREFIX)
    ]


def sanitise_resolution_fields(fields: dict | None) -> dict:
    """按系统真正支持回写的字段白名单裁剪咨询结论字段。

    声明了白名单之外的字段直接报错：宁可停止，也不写系统读不回来的内部字段
    （例如 ``settlement_status``）。
    """
    cleaned: dict = {}
    for key, value in (fields or {}).items():
        if key not in RESOLUTION_FIELD_WHITELIST:
            raise RuntimeError(
                f"resolution_fields 含系统不支持回写的字段：{key!r}；"
                f"允许：{'、'.join(RESOLUTION_FIELD_WHITELIST)}"
            )
        cleaned[key] = value
    return cleaned


# ---------------------------------------------------------------------------
# 分析结果：风险卡必须挂真实分析，不允许伪造状态或金额
# ---------------------------------------------------------------------------


def analysis_signature(  # noqa: ANN001
    db, profile, spec: AnalysisSpec, reference_at: datetime
) -> tuple:
    """本脚本口径下应有的分析签名。

    用确定性引擎**重新计算**而不是读库标记，所以不依赖硬编码日期，
    也能自动发现「账本已变化、旧结果不再成立」的情况。
    """
    from app.services.analysis_service import AnalysisService  # noqa: PLC0415
    from app.services.cash_engine import events_version_hash, run_engine  # noqa: PLC0415

    service = AnalysisService(db)
    inputs = service.build_inputs(
        profile, mode=spec.mode, delay_days=spec.delay_days, snapshot_at=reference_at
    )
    result = run_engine(inputs[0])
    # 依据版本必须与 AnalysisService._version_hash 同源：它取**未平移**的原始事项，
    # 而 delayed 口径下的 inputs[0].events 已经被推后过，不能拿来算指纹。
    base_hash = events_version_hash(
        [service.to_input(event) for event in service.load_events(profile.id)]
    )
    return (
        str(result.status),
        int(result.payment_gap_cents or 0),
        int(result.buffer_gap_cents or 0),
        result.max_withdrawable_cents,
        base_hash,
    )


def find_matching_analysis(  # noqa: ANN001
    db, profile, spec: AnalysisSpec, reference_at: datetime, *, exclude=()
):
    """找出与当前账本一致、且口径相同的既有分析结果（幂等复用的关键）。"""
    from sqlalchemy import select  # noqa: PLC0415

    from app.models.cash import AnalysisResult  # noqa: PLC0415

    expected = analysis_signature(db, profile, spec, reference_at)
    rows = db.scalars(
        select(AnalysisResult)
        .where(AnalysisResult.merchant_id == profile.id)
        .order_by(AnalysisResult.created_at.desc())
    ).all()
    for row in rows:
        if row.id in exclude or str(row.mode) != spec.mode:
            continue
        actual = (
            str(row.status),
            int(row.payment_gap_cents or 0),
            int(row.buffer_gap_cents or 0),
            row.max_withdrawable_cents,
            row.events_version_hash,
        )
        if actual == expected:
            return row
    return None


def ensure_analyses(  # noqa: ANN001
    db, profile, merchant, reference_at: datetime, *, verbose: bool = True
) -> dict:
    """按语义 Spec 真实生成分析结果；已存在的同签名结果直接复用（幂等）。"""
    from app.models.cash import AnalysisResult  # noqa: PLC0415
    from app.schemas.analysis import AnalysisRunRequest  # noqa: PLC0415
    from app.services.analysis_service import AnalysisService  # noqa: PLC0415

    resolved: dict = {}
    used: set[str] = set()
    for spec in ANALYSIS_SPECS:
        row = find_matching_analysis(db, profile, spec, reference_at, exclude=used)
        if row is None:
            kwargs: dict = {"mode": spec.mode, "snapshot_at": reference_at}
            if spec.mode == "delayed":
                kwargs["delay_days"] = spec.delay_days
            out = AnalysisService(db).run(
                profile, AnalysisRunRequest(**kwargs), actor_id=merchant.id
            )
            row = db.get(AnalysisResult, out.id)
            if verbose:
                withdrawable = row.max_withdrawable_cents
                amount = 0 if withdrawable is None else withdrawable // 100
                print(f"  ✓ 分析结果（{spec.detail}）：{row.status}（可提用 {amount} 元）")
        resolved[spec.key] = row
        used.add(row.id)
    return resolved


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
    channel = SETTLEMENT_CHANNEL
    created_settlements = 0
    for index, delay in enumerate(SETTLEMENT_DELAY_SEQUENCE, start=1):
        scheduled_day = local_day(reference_at, -(len(SETTLEMENT_DELAY_SEQUENCE) - index + 20))
        external_key = f"{SETTLEMENT_KEY_PREFIX}{scheduled_day.strftime('%Y%m%d')}-{index:03d}"
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
                source_ref=f"{SETTLEMENT_SOURCE_PREFIX}:{index}",
                note=f"{SOURCE_PREFIX}: 实际延期 {delay} 天",
                created_by=merchant.id,
            )
        )
        created_settlements += 1
    for index in range(3):
        scheduled_day = local_day(reference_at, -(3 - index))
        external_key = f"{SETTLEMENT_KEY_PREFIX}OPEN-{scheduled_day.strftime('%Y%m%d')}"
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
                source_ref=f"{SETTLEMENT_SOURCE_PREFIX}:open:{index}",
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
    #: 关联事项标题：必须能在 CORE_EVENTS / EXTRA_EVENTS 里精确找到；
    #: ``None`` 表示该卡不关联具体事项。
    event_title: str | None = None
    #: 该卡 payload 绑定的分析口径（见 ANALYSIS_SPECS）
    analysis_key: str = "current_plan"
    #: 风险卡要求的**真实**分析状态；引擎产不出该状态时不伪造，
    #: 改用与实际 payload 相符的普通资金提醒标题（见 RISK_TITLE_BY_STATUS）。
    required_status: str | None = None


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
        # 挂真实「到账延迟 2 天」分析：只有它才会产出付款缺口，
        # 标题与 payload 才自洽（不能拿「按当前计划」的分析冒充风险）。
        analysis_key="settlement_delay",
        required_status="PAYMENT_GAP",
    ),
    CardSpec(
        card_type="risk",
        days_ago=5,
        title="低于经营留底提醒",
        planned_cents=None,
        shared_fields=("risk_summary", "buffer_gap", "limiting_balance"),
        reaction=None,
        read=False,
        # 真实「到账延迟 1 天」分析：余额 400 元低于留底 600 元，
        # 状态为 BELOW_BUFFER（付款缺口为 0），与标题一致。
        analysis_key="below_buffer",
        required_status="BELOW_BUFFER",
    ),
    CardSpec(
        card_type="revision",
        days_ago=6,
        title="结算到账日期修正",
        planned_cents=None,
        shared_fields=("revision_summary", "limiting_point"),
        reaction=None,
        read=True,
        # 关联「平台结算款」：真实版本历史为 D3 09:00 → D2 09:00
        event_title="平台结算款",
    ),
    CardSpec(
        card_type="revision",
        days_ago=2,
        title="采购金额修正",
        planned_cents=None,
        shared_fields=("revision_summary", "risk_summary"),
        reaction=None,
        read=False,
        # 关联「鲜食原料采购」：真实版本历史为 ¥1,300 → ¥1,400
        event_title="鲜食原料采购",
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

    from app.models.household import (
        Household,
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

    # 分析结果：风险卡与决策卡都必须挂**真实**分析，先按语义 Spec 备好。
    analyses = ensure_analyses(db, profile, merchant, reference_at, verbose=verbose)

    existing = _script_cards(db, profile, merchant)
    created = 0
    for spec in CARD_SPECS:
        if any((spec.card_type, title) in existing for title in card_title_candidates(spec)):
            continue

        analysis = analyses[spec.analysis_key]
        cash_event_id = None
        if spec.event_title is not None:
            cash_event_id = resolve_event_by_title(
                db, profile, spec.event_title, reference_at
            ).id

        # 状态不符时**不伪造**：改用与实际 payload 相符的普通资金提醒标题。
        title = spec.title
        if spec.required_status is not None and str(analysis.status) != spec.required_status:
            title = RISK_TITLE_BY_STATUS.get(str(analysis.status), spec.title)

        card = service.create_card(
            profile,
            household,
            merchant,
            card_type=spec.card_type,
            shared_fields=list(spec.shared_fields),
            title=title,
            summary=None,
            analysis_result_id=analysis.id,
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
            link = f" → {spec.event_title}" if spec.event_title else ""
            print(f"  ✓ 协同卡「{title}」（{spec.card_type}）{link}")

    if verbose:
        print(f"  ✓ 协同卡合计新增 {created} 张，含表态与评论")


# ---------------------------------------------------------------------------
# 写入：经营咨询
# ---------------------------------------------------------------------------
#: 8 条咨询：2 submitted / 2 under_review / 1 need_more_information / 2 verified / 1 closed
#:
#: 每条咨询都用 :class:`ConsultationSpec` 做**语义绑定**：
#: ``event_title`` 必须能在 CORE_EVENTS / EXTRA_EVENTS 里按标题精确找到，
#: 找不到就报错停止 —— 绝不退回 ``events[index % len(events)]`` 那种按位置关联
#: （那正是「问的是结算款、关联的却是包装耗材采购」的根因）。
#:
#: ``resolution_summary`` 按事项语义逐条撰写，不允许所有已核实/已完成共用一句；
#: ``resolution_fields`` 只允许 :data:`RESOLUTION_FIELD_WHITELIST` 内的字段
#: （纯核实的咨询留空即可，不强行造字段）。
@dataclass(frozen=True)
class ConsultationSpec:
    title: str
    event_title: str
    question_type: str
    question: str
    target_status: str
    resolution_summary: str | None = None
    resolution_fields: dict | None = None
    #: 状态流转到「待补充资料」时咨询人员发出的补充要求
    info_request: str | None = None
    #: 状态流转到「已完成」时的归档说明（会覆盖 resolution_summary）
    close_summary: str | None = None


CONSULTATION_SPECS: tuple[ConsultationSpec, ...] = (
    ConsultationSpec(
        title="平台结算到账时间核实",
        event_title="平台结算款",
        question_type="settlement_time",
        question="这笔平台结算款原定次日上午到账，账户还没有收到，想确认结算进度。",
        target_status="submitted",
        resolution_summary="已核对该笔平台结算记录，当前结算状态正常。",
    ),
    ConsultationSpec(
        title="外卖平台结算状态核对",
        event_title="外卖平台结算",
        question_type="settlement_time",
        question="外卖平台的结算款显示已结算，但账户未入账，想核对一下状态。",
        target_status="submitted",
        resolution_summary="已核对外卖平台结算记录，当前结算状态正常。",
    ),
    ConsultationSpec(
        title="顾客退款状态确认",
        event_title="顾客退款",
        question_type="missing_arrival",
        question="顾客取消订单后的退款已经提交，想确认是否已经完成。",
        target_status="under_review",
        resolution_summary="已核对退款处理记录，退款状态已确认。",
    ),
    ConsultationSpec(
        title="平台服务费扣款核对",
        event_title="平台结算款",
        question_type="fee_unknown",
        question="本笔结算的到账金额与结算单差额较大，想核对服务费扣款。",
        target_status="under_review",
        resolution_summary="已核对平台服务费的扣款明细。",
    ),
    ConsultationSpec(
        title="团购结算批次查询",
        event_title="团购平台结算",
        question_type="settlement_time",
        question="团购平台的结算批次编号与账单不一致，请协助查询。",
        target_status="need_more_information",
        resolution_summary="已核对团购平台的结算批次记录。",
        info_request="请提供该笔结算的结算单截图编号，便于核对。",
    ),
    ConsultationSpec(
        title="结算款预计到账时间确认",
        event_title="平台结算款",
        question_type="settlement_time",
        question="想确认这笔结算款预计到账的具体时间。",
        target_status="verified",
        resolution_summary="已核对该笔平台结算记录，当前结算状态正常。",
    ),
    ConsultationSpec(
        title="收款入账时间核对",
        event_title="门店销售收款",
        question_type="settlement_time",
        question="门店收款已到账，但入账时间与记录不一致，请协助核对。",
        target_status="verified",
        resolution_summary="已核对门店收款记录及入账时间。",
    ),
    ConsultationSpec(
        title="经营事项补充材料",
        event_title="包装耗材采购",
        question_type="other",
        question="这笔支出缺少对应的采购单据，想补充材料。",
        target_status="closed",
        resolution_summary="已收到并核对补充的采购材料。",
        info_request="请补充该笔采购的供应商单据编号，便于核对。",
        close_summary="已收到并核对补充的采购材料，该事项已完成归档。",
    ),
)


def apply_consultation_data(
    db,
    *,
    profile,
    merchant,
    consultant_user,
    reference_at: datetime,
    verbose: bool = True,
) -> None:
    """建立经营咨询历史，覆盖全部状态并带完整时间线。"""
    from app.services.consultation_service import ConsultationService

    service = ConsultationService(db)
    existing = _script_cases(db, profile, merchant)
    created = 0
    for spec in CONSULTATION_SPECS:
        if spec.question in existing:
            continue
        # 语义绑定：按标题精确取事项；取不到直接报错停止
        event = resolve_event_by_title(db, profile, spec.event_title, reference_at)
        if event.event_type != require_event_spec(spec.event_title).kind:  # pragma: no cover
            raise RuntimeError(f"事项类型与 Spec 不一致：{spec.event_title}")

        case = service.create_case(
            profile,
            merchant,
            cash_event_id=event.id,
            question_type=spec.question_type,
            question=spec.question,
            ai_draft=None,
            status="submitted",
        )
        if spec.target_status in ("under_review", "need_more_information", "verified", "closed"):
            service.start_review(case, consultant_user)
        if spec.target_status == "need_more_information":
            service.request_information(
                case,
                consultant_user,
                spec.info_request or "请补充相关单据，便于核对。",
            )
        if spec.target_status in ("verified", "closed"):
            service.verify(
                case,
                consultant_user,
                resolution_summary=spec.resolution_summary or "",
                resolution_fields=sanitise_resolution_fields(spec.resolution_fields),
            )
        if spec.target_status == "closed":
            service.close(
                case,
                consultant_user,
                spec.close_summary or spec.resolution_summary or "事项已完成",
            )

        created += 1
        if verbose:
            print(
                f"  ✓ 咨询「{spec.title}」→ {spec.target_status}"
                f"（关联事项：{event.title} / {event.event_type}）"
            )

    if verbose:
        print(f"  ✓ 咨询合计新增 {created} 条，含完整处理时间线")


# ---------------------------------------------------------------------------
# 修复阶段：只修「明确属于本脚本预置数据集」且与 Spec 不一致的对象
#
# 识别依据（全部是语义的，不含硬编码日期）：
#   * 事项：标题精确匹配 + 来源原文首行带 SOURCE_PREFIX
#   * 咨询：编号 ZX 前缀 + merchant_id + created_by + 问题文案与 Spec 逐字一致
#   * 卡片：merchant_id + created_by + 卡片类型 + 预置标题（含退化标题）
#   * 结算：external_key / source_ref 前缀
#
# 探测函数与执行函数共用：dry-run 报告的就是真正会被修的。
# ---------------------------------------------------------------------------
def _consultation_spec_of(question: str) -> ConsultationSpec:
    for spec in CONSULTATION_SPECS:
        if spec.question == question:
            return spec
    raise RuntimeError(f"未登记的预置咨询文案：{question[:24]}")


def _expected_resolution(spec: ConsultationSpec) -> str | None:
    if spec.target_status == "closed":
        return spec.close_summary or spec.resolution_summary
    if spec.target_status == "verified":
        return spec.resolution_summary
    return None


def consultation_repairs(  # noqa: ANN001
    db, profile, merchant, reference_at: datetime
) -> list[tuple]:
    """返回 ``[(case, 待修复项), ...]``：只包含本脚本预置、且与 Spec 不一致的咨询。"""
    result: list[tuple] = []
    for question, case in _script_cases(db, profile, merchant).items():
        spec = _consultation_spec_of(question)
        event = _try_resolve_event(db, profile, spec.event_title, reference_at)
        issues: list[str] = []
        if event is None:
            issues.append("关联事项缺失")
        else:
            if case.cash_event_id != event.id:
                issues.append("关联事项")
            if (case.shared_fields or {}).get("event_title") != event.title:
                issues.append("共享字段快照")
        if case.question_type != spec.question_type:
            issues.append("问题类型")
        expected = _expected_resolution(spec)
        if expected is None:
            # 尚未给出核实结果的咨询不应带结论（旧实现给所有单据都写了同一句）
            if case.resolution_summary is not None:
                issues.append("多余结论")
        elif (case.resolution_summary or "") != expected:
            issues.append("核实结论")
        # 结论字段只允许系统真正支持回写的字段（settlement_status 之类必须清掉）
        if dict(case.resolution_fields or {}) != sanitise_resolution_fields(
            spec.resolution_fields
        ):
            issues.append("结论字段")
        if issues:
            result.append((case, issues))
    result.sort(key=lambda item: item[0].case_no)
    return result


def expected_card_title(spec: CardSpec, analysis) -> str:  # noqa: ANN001
    """卡片标题必须与实际 payload 的分析状态相符：产不出目标状态就用普通提醒标题。"""
    status = str(analysis.status)
    if spec.required_status is None or status == spec.required_status:
        return spec.title
    return RISK_TITLE_BY_STATUS.get(status, spec.title)


def card_payload_preview(db, profile, spec: CardSpec, card):  # noqa: ANN001
    """用现有 service 按关联事项重算 payload —— 修复与校验共用同一口径。"""
    from app.services.household_service import HouseholdService  # noqa: PLC0415

    _title, summary, payload, _fields = HouseholdService(db).build_preview(
        profile,
        card_type=spec.card_type,
        shared_fields=list(spec.shared_fields),
        analysis_result_id=card.analysis_result_id,
        cash_event_id=card.cash_event_id,
        planned_amount_cents=spec.planned_cents,
    )
    return summary, payload


def card_repairs(db, profile, merchant, reference_at: datetime) -> list[tuple]:  # noqa: ANN001
    """返回 ``[(card, 待修复项), ...]``：只包含本脚本预置、且与 Spec 不一致的卡片。"""
    from app.models.cash import AnalysisResult  # noqa: PLC0415

    cards = _script_cards(db, profile, merchant)
    result: list[tuple] = []
    for spec in CARD_SPECS:
        card = None
        for title in card_title_candidates(spec):
            card = cards.get((spec.card_type, title))
            if card is not None:
                break
        if card is None:
            continue

        issues: list[str] = []
        if spec.event_title is not None:
            event = _try_resolve_event(db, profile, spec.event_title, reference_at)
            if event is None:
                issues.append("关联事项缺失")
            elif card.cash_event_id != event.id:
                issues.append("关联事项")
        elif card.cash_event_id is not None:
            issues.append("多余的事项关联")

        analysis = (
            db.get(AnalysisResult, card.analysis_result_id)
            if card.analysis_result_id
            else None
        )
        expected_analysis = find_matching_analysis(
            db, profile, ANALYSIS_SPECS_BY_KEY[spec.analysis_key], reference_at
        )
        if analysis is None or expected_analysis is None or analysis.id != expected_analysis.id:
            issues.append("关联分析结果")
        else:
            if card.title != expected_card_title(spec, analysis):
                issues.append("标题与内容不符")
            summary, payload = card_payload_preview(db, profile, spec, card)
            if dict(card.payload or {}) != payload:
                issues.append("payload 与关联事项不一致")
            elif (card.summary or "") != (summary or "")[:2000]:
                issues.append("摘要与 payload 不一致")
        if issues:
            result.append((card, issues))
    result.sort(key=lambda item: (item[0].card_type, item[0].title))
    return result


def settlement_channel_repairs(db, profile) -> list:  # noqa: ANN001
    """返回渠道值不是 canonical 的**预置**结算记录（绝不碰用户导入的其它渠道）。"""
    return [row for row in script_settlements(db, profile) if row.channel != SETTLEMENT_CHANNEL]


def repair_product_data(
    db,
    *,
    profile,
    merchant,
    reference_at: datetime,
    verbose: bool = True,
) -> int:
    """修复阶段：把已经存在但内容错误的预置数据改成与 Spec 一致（幂等）。

    只动本脚本预置的对象；修复时**保留**咨询/卡片的历史交互
    （已读状态、表态、评论、created_at、时间线与状态流转）。
    """
    from app.models.cash import AnalysisResult  # noqa: PLC0415
    from app.services.consultation_service import ConsultationService  # noqa: PLC0415
    from app.services.enhancement_service import EnhancementService  # noqa: PLC0415

    repaired = 0

    # --- 1) 结算渠道统一 ---
    channel_rows = settlement_channel_repairs(db, profile)
    if channel_rows:
        for row in channel_rows:
            row.channel = SETTLEMENT_CHANNEL
        db.commit()
        # 渠道参与结算依据串，必须让既有增强结果失效并前进 history_revision
        enhancement = EnhancementService(db)
        enhancement.bump_history_revision(profile.id, commit=False)
        enhancement.mark_runs_stale(profile.id, f"{SOURCE_PREFIX}: 结算渠道已统一")
        repaired += len(channel_rows)
        if verbose:
            print(f"  ✓ 结算渠道统一为「{SETTLEMENT_CHANNEL}」：{len(channel_rows)} 条")

    # --- 2) 经营咨询：关联事项 / 问题类型 / 核实结论 ---
    service = ConsultationService(db)
    for case, issues in consultation_repairs(db, profile, merchant, reference_at):
        spec = _consultation_spec_of(case.question)
        event = _try_resolve_event(db, profile, spec.event_title, reference_at)
        if event is not None and ("关联事项" in issues or "共享字段快照" in issues):
            case.cash_event_id = event.id
            case.cash_event_version = event.current_version
            # 共享字段快照必须按新关联事项重算（仍是服务端白名单裁剪）
            case.shared_fields = service.build_shared_fields(case.case_no, event, case.question)
        if "问题类型" in issues:
            case.question_type = spec.question_type
        expected = _expected_resolution(spec)
        expected_fields = sanitise_resolution_fields(spec.resolution_fields)
        if expected is None and "多余结论" in issues:
            # 该单据尚未给出核实结果，清掉误写的结论
            case.resolution_summary = None
        if expected is not None and "核实结论" in issues:
            case.resolution_summary = expected
        if "结论字段" in issues:
            case.resolution_fields = expected_fields
        db.commit()
        repaired += 1
        if verbose:
            print(f"  ✓ 修复咨询 {case.case_no}：{'、'.join(issues)}")

    # --- 3) 协同卡：关联事项 / 分析结果 / 标题 / payload ---
    for card, issues in card_repairs(db, profile, merchant, reference_at):
        spec = next(
            s
            for s in CARD_SPECS
            if s.card_type == card.card_type and card.title in card_title_candidates(s)
        )
        if spec.event_title is not None:
            event = _try_resolve_event(db, profile, spec.event_title, reference_at)
            if event is not None:
                card.cash_event_id = event.id
        else:
            card.cash_event_id = None

        analysis = find_matching_analysis(
            db, profile, ANALYSIS_SPECS_BY_KEY[spec.analysis_key], reference_at
        )
        if analysis is not None:
            card.analysis_result_id = analysis.id
        elif card.analysis_result_id:  # pragma: no cover - ensure 阶段已保证分析存在
            analysis = db.get(AnalysisResult, card.analysis_result_id)

        if analysis is not None:
            card.title = expected_card_title(spec, analysis)[:128]
        summary, payload = card_payload_preview(db, profile, spec, card)
        card.payload = payload
        card.summary = (summary or "")[:2000]
        card.shared_fields = list(spec.shared_fields)
        card.planned_household_amount_cents = spec.planned_cents
        card.system_max_withdrawable_cents = payload.get("max_withdrawable_cents")
        # 注意：不触碰 created_at / updated_at / 已读状态 / 表态 / 评论
        db.commit()
        repaired += 1
        if verbose:
            print(f"  ✓ 修复协同卡「{card.title}」（{card.card_type}）：{'、'.join(issues)}")

    return repaired


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
    mode_label = "dry-run（不写入）" if args.dry_run else "apply（写入）"

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

        reference_at = resolve_reference(
            args.reference_at, db=db, merchant_id=profile.id
        )
        print(f"期初时点（UTC）：{reference_at.isoformat()}")
        print(f"模式：{mode_label}")
        print()

        print("=== 正式账号 ===")
        for username, user in users.items():
            print(f"  {username:16} {user.display_name:8} {','.join(user.role_names())}")
        print(f"  经营主体：{profile.business_name}")
        print()

        plan = build_plan(
            profile,
            merchant=merchant,
            reference_at=reference_at,
            dry_run=args.dry_run,
        )

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

        print("=== ensure：补齐缺失数据 ===")
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
            reference_at=reference_at,
        )
        print()

        print("=== repair：修正已存在但与 Spec 不一致的预置数据 ===")
        repaired = repair_product_data(
            db,
            profile=profile,
            merchant=merchant,
            reference_at=reference_at,
        )
        if repaired == 0:
            print("  无待修复对象")
        print()
        print("apply 完成。")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
