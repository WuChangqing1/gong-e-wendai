"""预置产品数据脚本（``scripts/populate_product_data.py``）的一致性回归。

脚本为三个正式账号建设预置业务数据（幂等、可反复执行）。这里重点守住：

* 8 条经营咨询与关联事项**语义绑定**（问结算的必须挂 settlement，绝不按列表位置关联）
* 更正通知卡关联到正确事项，并且差异来自**真实** ``CashEventRevision``
* 预置结算记录的渠道与结算事项来源说明一致（否则延期压力会显示「已完成 0 笔」）
* 风险卡标题与实际 payload 状态自洽（不允许「结算延迟风险提醒」显示资金安排可行）
* ensure / repair 分离且幂等：第二次运行必须报告「无待执行动作」

用例只使用临时 SQLite 库（``tests/conftest_env.py`` 已把 ``DATABASE_URL``
指向临时目录），绝不接触生产库或本地 ``data/app.db``。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import provision_user, register

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "populate_product_data.py"
REFERENCE_AT = "2026-10-04T09:00:00+08:00"

MERCHANT_USERNAME = "wangzhanggui"
FAMILY_USERNAME = "wangtaitai"
CONSULTANT_USERNAME = "zixunxiaoli"

#: 问题文案关键词 → 关联事项**必须**属于的类型。
#: 「问结算的不能挂采购」这条规则在这里被逐条钉死。
QUESTION_TYPE_RULES = {
    "结算": "settlement",
    "退款": "refund",
    "收款": "sale_receipt",
    "采购": "supplier_payment",
}

#: 逐条期望的「关联事项 → 事项类型」。第 6 条必须落在 settlement，
#: 且绝不能是「包装耗材采购」（那正是按列表位置关联造成的错配）。
EXPECTED_BINDINGS = {
    "平台结算到账时间核实": ("平台结算款", "settlement"),
    "外卖平台结算状态核对": ("外卖平台结算", "settlement"),
    "顾客退款状态确认": ("顾客退款", "refund"),
    "平台服务费扣款核对": ("平台结算款", "settlement"),
    "团购结算批次查询": ("团购平台结算", "settlement"),
    "结算款预计到账时间确认": ("平台结算款", "settlement"),
    "收款入账时间核对": ("门店销售收款", "sale_receipt"),
    "经营事项补充材料": ("包装耗材采购", "supplier_payment"),
}

#: 每类事项的结论必须用对应的措辞，不允许所有已核实/已完成共用一句。
RESOLUTION_KEYWORD = {
    "settlement": "结算",
    "refund": "退款",
    "sale_receipt": "收款",
    "supplier_payment": "采购",
}

#: 生产库现状：8 条咨询当时按列表位置关联到的（错误）事项标题。
PRODUCTION_CONSULTATION_LINKS = {
    "平台结算到账时间核实": "鲜食原料采购",
    "外卖平台结算状态核对": "平台结算款",
    "顾客退款状态确认": "门店租金",
    "平台服务费扣款核对": "顾客退款",
    "团购结算批次查询": "外卖平台结算",
    "结算款预计到账时间确认": "包装耗材采购",
    "收款入账时间核对": "门店销售收款",
    "经营事项补充材料": "员工工资",
}


def _load_script():
    spec = importlib.util.spec_from_file_location("populate_product_data", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["populate_product_data"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


script = _load_script()


# ---------------------------------------------------------------------------
# 装置与读取辅助
# ---------------------------------------------------------------------------
@pytest.fixture
def product_client(client: TestClient):
    """三个正式账号：经营者（含经营档案）+ 家庭成员 + 咨询人员。"""
    response = register(
        client,
        username=MERCHANT_USERNAME,
        display_name="王掌柜",
        roles=["merchant"],
        business_name="王家小馆",
    )
    assert response.status_code == 201, response.text
    provision_user(username=FAMILY_USERNAME, roles=["family_member"], display_name="王太太")
    provision_user(username=CONSULTANT_USERNAME, roles=["consultant"], display_name="咨询小李")
    return client


def _run(*args: str) -> int:
    return script.main(["--reference-at", REFERENCE_AT, *args])


@pytest.fixture
def applied(product_client: TestClient, db_session, capsys):
    """执行一次完整 apply，返回 ``(db, profile, merchant_user, 输出)``。"""
    assert _run("--apply") == 0
    captured = capsys.readouterr()
    profile, merchant_user = _merchant(db_session)
    return db_session, profile, merchant_user, captured.out


def _merchant(db):
    from sqlalchemy import select

    from app.models.merchant import MerchantProfile
    from app.models.user import User

    merchant = db.scalar(select(User).where(User.username == MERCHANT_USERNAME))
    profile = db.scalar(select(MerchantProfile).where(MerchantProfile.user_id == merchant.id))
    return profile, merchant


def _events_by_title(db, profile) -> dict:
    """``{标题: 事项}``，只包含**未取消**的事项 —— 脚本也只允许绑定到它们。"""
    from sqlalchemy import select

    from app.models.cash import CashEvent

    rows = db.scalars(
        select(CashEvent).where(
            CashEvent.merchant_id == profile.id, CashEvent.state != "cancelled"
        )
    ).all()
    return {row.title: row for row in rows}


def _cases_by_question(db, profile) -> dict:
    from sqlalchemy import select

    from app.models.consultation import ConsultationCase

    rows = db.scalars(
        select(ConsultationCase).where(ConsultationCase.merchant_id == profile.id)
    ).all()
    return {row.question: row for row in rows}


def _cards_by_title(db, profile) -> dict:
    from sqlalchemy import select

    from app.models.household import HouseholdCard

    rows = db.scalars(
        select(HouseholdCard).where(HouseholdCard.merchant_id == profile.id)
    ).all()
    return {row.title: row for row in rows}


def _card_by_spec(db, profile, spec):
    cards = _cards_by_title(db, profile)
    for title in script.card_title_candidates(spec):
        if title in cards:
            return cards[title]
    return None


def _revision_changes(revision) -> dict:
    """``{字段: (before, after)}`` —— 直接复用系统真实的版本差异实现。"""
    from app.services.event_service import diff_snapshots

    return {
        item["field"]: (item["before"], item["after"])
        for item in diff_snapshots(revision.before_json, revision.after_json)
    }


def _regenerate_payload(db, profile, card, spec) -> dict:
    """用现有 service 按同一关联事项重新生成 payload（用于证明 payload 非手写）。"""
    from app.services.household_service import HouseholdService

    _title, _summary, payload, _fields = HouseholdService(db).build_preview(
        profile,
        card_type=card.card_type,
        shared_fields=list(spec.shared_fields),
        analysis_result_id=card.analysis_result_id,
        cash_event_id=card.cash_event_id,
        planned_amount_cents=spec.planned_cents,
    )
    return payload


def _run_apply() -> str:
    """再跑一次 apply 并返回输出（供幂等断言使用）。"""
    import io
    from contextlib import redirect_stdout

    buffer = io.StringIO()
    with redirect_stdout(buffer):
        exit_code = _run("--apply")
    assert exit_code == 0
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# 规格自检（不需要数据库）
# ---------------------------------------------------------------------------
class TestSpecsAreSelfConsistent:
    def test_every_spec_event_title_resolves(self):
        for spec in script.CONSULTATION_SPECS:
            assert script.require_event_spec(spec.event_title).title == spec.event_title

    def test_unknown_event_title_raises_instead_of_falling_back(self):
        with pytest.raises(RuntimeError):
            script.require_event_spec("包装耗材采购（不存在）")

    def test_resolution_fields_are_whitelisted(self):
        allowed = set(script.RESOLUTION_FIELD_WHITELIST)
        assert allowed == {
            "amount_cents",
            "scheduled_at",
            "state",
            "title",
            "note",
            "source_label",
        }
        for spec in script.CONSULTATION_SPECS:
            assert set(spec.resolution_fields or {}) <= allowed

    def test_internal_field_is_refused(self):
        with pytest.raises(RuntimeError):
            script.sanitise_resolution_fields({"settlement_status": "已核对"})

    def test_two_revision_cards_declare_their_event(self):
        revisions = {
            spec.title: spec for spec in script.CARD_SPECS if spec.card_type == "revision"
        }
        assert revisions["采购金额修正"].event_title == "鲜食原料采购"
        assert revisions["结算到账日期修正"].event_title == "平台结算款"
        # 两张更正卡都必须把「事项变更摘要」放进 shared_fields，接收端才看得到差异
        for spec in revisions.values():
            assert "revision_summary" in spec.shared_fields
        # 其它卡片不关联事项
        for spec in script.CARD_SPECS:
            if spec.card_type != "revision":
                assert spec.event_title is None

    def test_revision_plan_covers_platform_settlement(self):
        assert "平台结算款" in script.REVISION_PLAN


# ---------------------------------------------------------------------------
# 经营咨询：事项 → 问题 → 结论
# ---------------------------------------------------------------------------
class TestConsultationSemantics:
    def test_all_eight_cases_exist_with_semantic_links(self, applied):
        db, profile, _merchant_user, _out = applied
        from app.models.cash import CashEvent

        cases = _cases_by_question(db, profile)
        assert len(script.CONSULTATION_SPECS) == 8
        assert len(cases) == 8

        for spec in script.CONSULTATION_SPECS:
            case = cases[spec.question]
            event = db.get(CashEvent, case.cash_event_id)
            expected_title, expected_type = EXPECTED_BINDINGS[spec.title]

            assert event is not None, spec.title
            assert event.title == spec.event_title == expected_title
            assert event.event_type == expected_type
            # 共享给咨询人员的快照同样指向这一个事项
            assert case.shared_fields["event_title"] == event.title
            assert case.shared_fields["event_type"] == event.event_type

    def test_question_keyword_matches_event_type(self, applied):
        """问「结算」的必须是结算款事项，不能是采购。"""
        db, profile, _merchant_user, _out = applied
        from app.models.cash import CashEvent

        cases = _cases_by_question(db, profile)
        for spec in script.CONSULTATION_SPECS:
            event = db.get(CashEvent, cases[spec.question].cash_event_id)
            for keyword, expected_type in QUESTION_TYPE_RULES.items():
                if keyword in spec.question:
                    assert event.event_type == expected_type, (spec.title, keyword)

    def test_first_case_is_bound_to_settlement(self, applied):
        db, profile, _merchant_user, _out = applied
        from app.models.cash import CashEvent

        spec = next(s for s in script.CONSULTATION_SPECS if s.title == "平台结算到账时间核实")
        case = _cases_by_question(db, profile)[spec.question]
        event = db.get(CashEvent, case.cash_event_id)
        assert event.event_type == "settlement"
        assert event.title == "平台结算款"
        assert "到账" in case.question

    def test_sixth_case_is_settlement_and_not_packaging_purchase(self, applied):
        db, profile, _merchant_user, _out = applied
        from app.models.cash import CashEvent

        spec = next(
            s for s in script.CONSULTATION_SPECS if s.title == "结算款预计到账时间确认"
        )
        case = _cases_by_question(db, profile)[spec.question]
        event = db.get(CashEvent, case.cash_event_id)
        assert event.event_type == "settlement"
        assert event.title != "包装耗材采购"
        assert event.title in ("平台结算款", "外卖平台结算")

    def test_resolutions_match_event_semantics(self, applied):
        db, profile, _merchant_user, _out = applied
        from app.models.cash import CashEvent

        cases = _cases_by_question(db, profile)
        summaries = []
        for spec in script.CONSULTATION_SPECS:
            case = cases[spec.question]
            if case.status not in ("verified", "closed"):
                assert case.resolution_summary is None
                assert not (case.resolution_fields or {})
                continue
            event = db.get(CashEvent, case.cash_event_id)
            summary = case.resolution_summary or ""
            summaries.append(summary)
            assert RESOLUTION_KEYWORD[event.event_type] in summary, spec.title
            assert case.resolution_summary == script._expected_resolution(spec)
            # 结论字段只允许系统真正支持回写的字段
            assert set(case.resolution_fields or {}) <= set(script.RESOLUTION_FIELD_WHITELIST)
            assert "settlement_status" not in (case.resolution_fields or {})

        # 不允许所有已核实/已完成共用同一句
        assert len(summaries) == len(set(summaries)) == 3

    def test_status_distribution_matches_specs(self, applied):
        db, profile, _merchant_user, _out = applied
        cases = _cases_by_question(db, profile)
        assert [cases[spec.question].status for spec in script.CONSULTATION_SPECS] == [
            spec.target_status for spec in script.CONSULTATION_SPECS
        ]
        assert sorted(cases[spec.question].status for spec in script.CONSULTATION_SPECS) == [
            "closed",
            "need_more_information",
            "submitted",
            "submitted",
            "under_review",
            "under_review",
            "verified",
            "verified",
        ]


# ---------------------------------------------------------------------------
# 更正卡与真实版本历史
# ---------------------------------------------------------------------------
class TestRevisionCards:
    def test_cards_link_to_the_declared_events(self, applied):
        db, profile, _merchant_user, _out = applied
        events = _events_by_title(db, profile)

        purchase = next(s for s in script.CARD_SPECS if s.title == "采购金额修正")
        card = _card_by_spec(db, profile, purchase)
        assert card is not None
        assert card.cash_event_id == events["鲜食原料采购"].id

        settlement = next(s for s in script.CARD_SPECS if s.title == "结算到账日期修正")
        card = _card_by_spec(db, profile, settlement)
        assert card is not None
        assert card.cash_event_id == events["平台结算款"].id

    def test_purchase_amount_has_real_revision_1300_to_1400(self, applied):
        db, profile, merchant_user, _out = applied
        event = _events_by_title(db, profile)["鲜食原料采购"]

        assert event.current_version >= 2
        revisions = sorted(event.revisions, key=lambda row: row.version)
        changes = [_revision_changes(row) for row in revisions]
        assert any(item.get("amount_cents") == (1300_00, 1400_00) for item in changes), changes

        target = next(
            row
            for row in revisions
            if _revision_changes(row).get("amount_cents") == (1300_00, 1400_00)
        )
        # 必须是 EventService.update_event 产生的真实版本，而不是直接 INSERT 的 revision
        assert target.material is True
        assert target.changed_by == merchant_user.id
        assert target.changed_by_name == merchant_user.display_name
        assert (target.change_reason or "").startswith(script.SOURCE_PREFIX)
        assert event.amount_cents == 1400_00

    def test_settlement_date_has_real_revision_d3_to_d2(self, applied):
        db, profile, merchant_user, _out = applied
        event = _events_by_title(db, profile)["平台结算款"]

        reference = script.resolve_reference(REFERENCE_AT)
        day3 = script.at_local_day(reference, 3, 9, 0).isoformat()
        day2 = script.at_local_day(reference, 2, 9, 0).isoformat()
        assert event.scheduled_at.isoformat() == day2

        revisions = sorted(event.revisions, key=lambda row: row.version)
        changes = [_revision_changes(row) for row in revisions]
        assert any(item.get("scheduled_at") == (day3, day2) for item in changes), changes

        target = next(
            row for row in revisions if _revision_changes(row).get("scheduled_at") == (day3, day2)
        )
        assert target.material is True
        assert target.changed_by == merchant_user.id
        assert (target.change_reason or "").startswith(script.SOURCE_PREFIX)

    def test_card_payload_is_regenerated_by_service(self, applied):
        """两张更正卡的 payload 必须等于「用同一关联事项重新生成」的结果。

        这里刻意做**相对比较**（库里的 payload vs service 现算的 payload），
        因此既不依赖 payload 具体包含哪些键，也不手写任何金额或时间。
        """
        db, profile, _merchant_user, _out = applied
        for spec in (s for s in script.CARD_SPECS if s.card_type == "revision"):
            card = _card_by_spec(db, profile, spec)
            assert card is not None, spec.title
            assert card.payload == _regenerate_payload(db, profile, card, spec)
            assert card.shared_fields == list(spec.shared_fields)
            assert "revision_summary" in card.shared_fields

    def test_revision_cards_point_at_distinct_events(self, applied):
        db, profile, _merchant_user, _out = applied
        linked = {
            spec.title: _card_by_spec(db, profile, spec).cash_event_id
            for spec in script.CARD_SPECS
            if spec.card_type == "revision"
        }
        assert None not in linked.values()
        assert len(set(linked.values())) == 2

    def test_payload_revision_summary_is_never_fabricated(self, applied):
        """若 payload 里带了「事项变更摘要」，它的差异必须与关联事项的真实版本一致。

        这里既不断言该键一定存在、也不断言它不存在：键由 service 的字段白名单决定，
        脚本无权改写；但只要它出现，就必须能在 ``CashEventRevision`` 里逐字对上。
        """
        db, profile, _merchant_user, _out = applied
        from app.models.cash import CashEvent

        for spec in (s for s in script.CARD_SPECS if s.card_type == "revision"):
            card = _card_by_spec(db, profile, spec)
            summary = card.payload.get("revision_summary")
            if summary is None:
                continue

            event = db.get(CashEvent, card.cash_event_id)
            material = [row for row in event.revisions if row.material]
            latest = max(material, key=lambda row: row.version)
            assert summary["event_title"] == event.title
            assert summary["version"] == latest.version
            assert summary["change_reason"] == latest.change_reason

            actual = _revision_changes(latest)
            reported = {
                item["field"]: (item["before"], item["after"]) for item in summary["changes"]
            }
            assert reported == actual
            assert reported, "变更摘要不允许是空壳"


# ---------------------------------------------------------------------------
# 结算渠道
# ---------------------------------------------------------------------------
class TestSettlementChannel:
    def test_all_seeded_channels_are_canonical(self, applied):
        db, profile, _merchant_user, _out = applied
        rows = script.script_settlements(db, profile)
        assert len(rows) == len(script.SETTLEMENT_DELAY_SEQUENCE) + 3
        assert {row.channel for row in rows} == {script.SETTLEMENT_CHANNEL}
        assert script.SETTLEMENT_CHANNEL == "平台结算单"

    def test_channel_matches_settlement_event_source_label(self, applied):
        db, profile, _merchant_user, _out = applied
        assert _events_by_title(db, profile)["平台结算款"].source_label == script.SETTLEMENT_CHANNEL

    def test_legacy_channel_is_repaired_and_user_rows_untouched(self, applied):
        """只修明确由本脚本产生的记录，用户真实导入的其它渠道绝不能被改写。"""
        db, profile, _merchant_user, _out = applied
        from sqlalchemy import select

        from app.models.enhancement import SETTLEMENT_COMPLETED, SettlementRecord

        reference = script.resolve_reference(REFERENCE_AT)
        stamp = script.at_local_day(reference, -1, 9, 0)
        db.add(
            SettlementRecord(
                merchant_id=profile.id,
                external_key="WX-USER-0001",
                channel="微信支付",
                scheduled_at=stamp,
                actual_at=stamp,
                known_at=stamp,
                status=SETTLEMENT_COMPLETED,
                source_ref="manual:wx-user",
            )
        )
        seeded = db.scalars(
            select(SettlementRecord).where(SettlementRecord.merchant_id == profile.id)
        ).all()
        for row in seeded:
            if row.external_key.startswith(script.SETTLEMENT_KEY_PREFIX):
                row.channel = "平台结算"
        db.commit()

        out = _run_apply()
        assert "repair_settlement_channel" in out
        db.expire_all()
        assert {row.channel for row in script.script_settlements(db, profile)} == {
            script.SETTLEMENT_CHANNEL
        }
        assert db.query(SettlementRecord).filter_by(external_key="WX-USER-0001").one().channel == (
            "微信支付"
        )


# ---------------------------------------------------------------------------
# 风险卡
# ---------------------------------------------------------------------------
class TestRiskCards:
    def test_settlement_delay_card_shows_real_payment_gap(self, applied):
        db, profile, _merchant_user, _out = applied
        from app.models.cash import AnalysisResult

        spec = next(s for s in script.CARD_SPECS if s.title == "结算延迟风险提醒")
        card = _card_by_spec(db, profile, spec)
        assert card is not None
        payload = card.payload
        assert payload["status"] == "PAYMENT_GAP"
        assert payload["payment_gap_cents"] == 200_00
        assert payload["buffer_gap_cents"] == 800_00
        assert "付款缺口" in payload["risk_summary"]
        assert "资金安排可行" not in payload["risk_summary"]

        analysis = db.get(AnalysisResult, card.analysis_result_id)
        assert analysis is not None
        assert analysis.mode == "delayed"
        assert analysis.status == "PAYMENT_GAP"
        assert analysis.payment_gap_cents == 200_00

    def test_below_buffer_card_shows_real_buffer_gap(self, applied):
        db, profile, _merchant_user, _out = applied
        from app.models.cash import AnalysisResult

        spec = next(s for s in script.CARD_SPECS if s.title == "低于经营留底提醒")
        card = _card_by_spec(db, profile, spec)
        assert card is not None
        payload = card.payload
        assert payload["status"] == "BELOW_BUFFER"
        assert payload["buffer_gap_cents"] == 200_00
        assert "留底" in payload["risk_summary"]
        assert "资金安排可行" not in payload["risk_summary"]

        analysis = db.get(AnalysisResult, card.analysis_result_id)
        assert analysis is not None
        assert analysis.mode == "delayed"
        assert analysis.status == "BELOW_BUFFER"
        assert analysis.payment_gap_cents == 0

    def test_risk_cards_payload_matches_regenerated_and_title(self, applied):
        db, profile, _merchant_user, _out = applied
        for spec in (s for s in script.CARD_SPECS if s.card_type == "risk"):
            card = _card_by_spec(db, profile, spec)
            assert card is not None, spec.title
            assert card.payload == _regenerate_payload(db, profile, card, spec)
            # 本算例下真实状态与预置标题自洽
            assert card.title == spec.title

    def test_decision_cards_are_bound_to_current_plan(self, applied):
        db, profile, _merchant_user, _out = applied
        from app.models.cash import AnalysisResult

        for spec in (s for s in script.CARD_SPECS if s.card_type == "decision"):
            card = _card_by_spec(db, profile, spec)
            analysis = db.get(AnalysisResult, card.analysis_result_id)
            assert analysis.mode == "current_plan"
            assert card.payload["max_withdrawable_cents"] == 1200_00


# ---------------------------------------------------------------------------
# 分析结果
# ---------------------------------------------------------------------------
class TestAnalysisBinding:
    def test_three_analyses_are_generated(self, applied):
        db, profile, _merchant_user, _out = applied
        from sqlalchemy import select

        from app.models.cash import AnalysisResult

        rows = db.scalars(
            select(AnalysisResult).where(AnalysisResult.merchant_id == profile.id)
        ).all()
        assert sorted(f"{row.mode}:{row.status}" for row in rows) == [
            "current_plan:FEASIBLE",
            "delayed:BELOW_BUFFER",
            "delayed:PAYMENT_GAP",
        ]

    def test_idempotency_check_is_signature_based(self, applied):
        """同一账本下每个口径都能找到既有结果，因此第二次不会再生成。"""
        db, profile, _merchant_user, _out = applied
        reference = script.resolve_reference(REFERENCE_AT)
        for spec in script.ANALYSIS_SPECS:
            assert script.find_matching_analysis(db, profile, spec, reference) is not None


# ---------------------------------------------------------------------------
# ensure / repair 与幂等
# ---------------------------------------------------------------------------
class TestRepairAndIdempotency:
    def test_second_run_reports_nothing_to_do(self, applied):
        out = _run_apply()
        assert "无待执行动作（数据已就绪，幂等）" in out

    def test_second_run_creates_no_new_objects(self, applied):
        from sqlalchemy import func, select

        from app.models.cash import AnalysisResult, CashEvent, CashEventRevision
        from app.models.consultation import ConsultationCase
        from app.models.enhancement import SettlementRecord
        from app.models.household import HouseholdCard

        db, profile, _merchant_user, _out = applied

        def counts():
            return {
                "events": db.scalar(
                    select(func.count())
                    .select_from(CashEvent)
                    .where(CashEvent.merchant_id == profile.id)
                ),
                "revisions": db.scalar(select(func.count()).select_from(CashEventRevision)),
                "cards": db.scalar(
                    select(func.count())
                    .select_from(HouseholdCard)
                    .where(HouseholdCard.merchant_id == profile.id)
                ),
                "cases": db.scalar(
                    select(func.count())
                    .select_from(ConsultationCase)
                    .where(ConsultationCase.merchant_id == profile.id)
                ),
                "settlements": db.scalar(
                    select(func.count())
                    .select_from(SettlementRecord)
                    .where(SettlementRecord.merchant_id == profile.id)
                ),
                "analyses": db.scalar(
                    select(func.count())
                    .select_from(AnalysisResult)
                    .where(AnalysisResult.merchant_id == profile.id)
                ),
            }

        before = counts()
        out = _run_apply()
        assert "无待执行动作（数据已就绪，幂等）" in out
        db.expire_all()
        assert counts() == before

    def test_dry_run_writes_nothing(self, product_client, db_session, capsys):
        from sqlalchemy import func, select

        from app.models.cash import CashEvent
        from app.models.consultation import ConsultationCase
        from app.models.household import HouseholdCard

        assert _run("--dry-run") == 0
        out = capsys.readouterr().out
        assert "dry-run 结束：未写入任何数据。" in out
        assert db_session.scalar(select(func.count()).select_from(CashEvent)) == 0
        assert db_session.scalar(select(func.count()).select_from(ConsultationCase)) == 0
        assert db_session.scalar(select(func.count()).select_from(HouseholdCard)) == 0

    def test_dry_run_reports_planned_repairs_without_secrets(
        self, product_client, db_session, capsys
    ):
        assert _run("--dry-run") == 0
        out = capsys.readouterr().out
        assert "[consultation]" in out
        assert "关联 平台结算款" in out
        for forbidden in ("password", "secret", "token", "wendai@"):
            assert forbidden not in out.lower()

    def test_repair_fixes_production_shaped_data(self, applied):
        """按**生产库现状**构造缺陷数据，逐条验证 repair 的「修复前 → 修复后」。

        生产现状（只读查询得到，见交付报告）：
        * 8 条咨询的关联事项与问题错配，且 0006/0007 共用同一句结论、0008 用归档句
        * 两张更正卡都挂在「平台结算款」上
        * 20 条结算记录渠道都是「平台结算」
        * 平台结算款**没有任何 material 修订**（需要补 D3 → D2 的真实版本历史）
        * 另有一张非预置卡「家庭提用决策确认」绝不能被改动
        """
        from sqlalchemy import select

        from app.models.cash import AnalysisResult, CashEvent, CashEventRevision
        from app.models.consultation import ConsultationCase
        from app.models.enhancement import SettlementRecord
        from app.models.household import (
            HouseholdCard,
            HouseholdCardComment,
            HouseholdCardRecipient,
        )

        db, profile, _merchant_user, _out = applied

        # --- 1) 把 8 条咨询改成生产库里的错配形态 ---
        events = _events_by_title(db, profile)
        cases = db.scalars(
            select(ConsultationCase)
            .where(ConsultationCase.merchant_id == profile.id)
            .order_by(ConsultationCase.case_no)
        ).all()
        assert len(cases) == 8
        for case, spec in zip(cases, script.CONSULTATION_SPECS, strict=True):
            wrong_title = PRODUCTION_CONSULTATION_LINKS[spec.title]
            wrong = events[wrong_title]
            case.cash_event_id = wrong.id
            case.shared_fields = {
                **(case.shared_fields or {}),
                "event_title": wrong.title,
                "event_type": wrong.event_type,
            }
            # 旧实现：所有单据共用一句话；已核实/已完成还写了系统无法回写的内部字段
            if spec.target_status == "verified":
                case.resolution_summary = f"{spec.title}：已核对平台流水，结算状态正常。"
                case.resolution_fields = {"settlement_status": "已核对"}
            elif spec.target_status == "closed":
                case.resolution_summary = f"{spec.title}：已完成核对并归档。"
                case.resolution_fields = {"settlement_status": "已核对"}

        # --- 2) 两张更正卡都挂「平台结算款」 ---
        cards = db.scalars(
            select(HouseholdCard).where(HouseholdCard.merchant_id == profile.id)
        ).all()
        for card in cards:
            if card.card_type == "revision":
                card.cash_event_id = events["平台结算款"].id

        # --- 3) 结算记录渠道用旧值 ---
        for row in db.scalars(
            select(SettlementRecord).where(SettlementRecord.merchant_id == profile.id)
        ).all():
            row.channel = "平台结算"

        # --- 4) 平台结算款没有 material 修订（退回 v1 创建态） ---
        for revision in db.scalars(
            select(CashEventRevision).where(
                CashEventRevision.cash_event_id == events["平台结算款"].id,
                CashEventRevision.version > 1,
            )
        ).all():
            db.delete(revision)
        events["平台结算款"].current_version = 1

        # --- 5) 风险卡挂「按当前计划」分析，标题与内容矛盾 ---
        current_plan = db.scalar(
            select(AnalysisResult).where(
                AnalysisResult.merchant_id == profile.id,
                AnalysisResult.mode == "current_plan",
            )
        )
        risk_card = next(
            card
            for card in cards
            if card.card_type == "risk" and card.title == "结算延迟风险提醒"
        )
        risk_card.analysis_result_id = current_plan.id
        risk_card.payload = {
            "risk_summary": "未来 7 天已确认的收付款事项都可以覆盖，资金安排可行。",
            "status": "FEASIBLE",
        }

        # --- 6) 同名但已取消的旧初始化事项：不能被误选为关联对象 ---
        cancelled = CashEvent(
            merchant_id=profile.id,
            cash_key="DEMO-SETTLE-9999",
            event_type="settlement",
            title="平台结算款",
            amount_cents=2000_00,
            direction="inflow",
            scheduled_at=events["平台结算款"].scheduled_at,
            state="cancelled",
            source_type="manual",
        )
        db.add(cancelled)
        db.flush()

        # --- 7) 非预置卡：必须原样保留 ---
        household_id = cards[0].household_id
        extra = HouseholdCard(
            household_id=household_id,
            merchant_id=profile.id,
            card_type="decision",
            title="家庭提用决策确认",
            summary="家人自己发起的一张卡",
            payload={"max_withdrawable_cents": 123_45},
            shared_fields=["max_withdrawable"],
            created_by=None,
        )
        db.add(extra)
        db.flush()
        db.add(HouseholdCardRecipient(card_id=extra.id, user_id=cards[0].created_by))
        db.commit()

        # --- 记录必须被保留的历史交互 ---
        kept = {}
        for card in cards:
            kept[card.title] = {
                "created_at": card.created_at,
                "recipients": {
                    item.user_id: (item.is_read, item.reaction, item.read_at)
                    for item in db.scalars(
                        select(HouseholdCardRecipient).where(
                            HouseholdCardRecipient.card_id == card.id
                        )
                    ).all()
                },
                "comments": sorted(
                    content
                    for content in db.scalars(
                        select(HouseholdCardComment.content).where(
                            HouseholdCardComment.card_id == card.id
                        )
                    ).all()
                ),
            }
        extra_snapshot = (extra.title, extra.summary, dict(extra.payload), extra.created_at)

        # --- 执行修复 ---
        out = _run_apply()
        assert "[repair_consultation" in out
        assert "[repair_card" in out
        assert "[repair_settlement_channel" in out
        assert "无待执行动作" not in out
        db.expire_all()

        # 修复后：8 条咨询的「事项 → 结论」全部与 Spec 一致
        events = _events_by_title(db, profile)
        for spec in script.CONSULTATION_SPECS:
            case = _cases_by_question(db, profile)[spec.question]
            event = db.get(CashEvent, case.cash_event_id)
            expected_title, expected_type = EXPECTED_BINDINGS[spec.title]
            assert event.title == expected_title == spec.event_title
            assert event.event_type == expected_type
            assert event.state == "scheduled"
            # 同名但已取消的旧初始化事项绝不能被选中
            assert case.cash_event_id != cancelled.id
            assert case.shared_fields["event_title"] == event.title
            assert case.shared_fields["event_type"] == event.event_type
            assert case.resolution_fields == {}
            assert case.resolution_summary == script._expected_resolution(spec)

        # 修复后：平台结算款有了真实版本历史（D3 → D2）
        settlement_event = events["平台结算款"]
        assert settlement_event.current_version >= 2
        reference = script.resolve_reference(REFERENCE_AT)
        day3 = script.at_local_day(reference, 3, 9, 0).isoformat()
        day2 = script.at_local_day(reference, 2, 9, 0).isoformat()
        material = [
            row
            for row in db.scalars(
                select(CashEventRevision).where(
                    CashEventRevision.cash_event_id == settlement_event.id
                )
            ).all()
            if row.material
        ]
        assert any(
            _revision_changes(row).get("scheduled_at") == (day3, day2) for row in material
        )

        # 修复后：两张更正卡各自关联正确事项，payload 由 service 真实生成
        cards_by_title = _cards_by_title(db, profile)
        assert cards_by_title["采购金额修正"].cash_event_id == events["鲜食原料采购"].id
        assert cards_by_title["结算到账日期修正"].cash_event_id == settlement_event.id
        for spec in (s for s in script.CARD_SPECS if s.card_type == "revision"):
            card = cards_by_title[spec.title]
            assert card.payload == _regenerate_payload(db, profile, card, spec)

        # 修复后：结算渠道统一
        assert {row.channel for row in script.script_settlements(db, profile)} == {
            script.SETTLEMENT_CHANNEL
        }

        # 修复后：风险卡内容、分析绑定与标题一致
        risk_card = db.get(HouseholdCard, risk_card.id)
        assert risk_card.payload["status"] == "PAYMENT_GAP"
        assert risk_card.payload["payment_gap_cents"] == 200_00
        assert risk_card.title == "结算延迟风险提醒"

        # 历史交互保留、非预置卡未被触碰
        for card in db.scalars(
            select(HouseholdCard).where(HouseholdCard.merchant_id == profile.id)
        ).all():
            if card.title == extra_snapshot[0]:
                continue
            snapshot = kept[card.title]
            assert card.created_at == snapshot["created_at"]
            for item in db.scalars(
                select(HouseholdCardRecipient).where(
                    HouseholdCardRecipient.card_id == card.id
                )
            ).all():
                assert (item.is_read, item.reaction, item.read_at) == snapshot["recipients"][
                    item.user_id
                ]
            assert sorted(
                content
                for content in db.scalars(
                    select(HouseholdCardComment.content).where(
                        HouseholdCardComment.card_id == card.id
                    )
                ).all()
            ) == snapshot["comments"]

        extra = db.get(HouseholdCard, extra.id)
        assert (extra.title, extra.summary, dict(extra.payload), extra.created_at) == (
            extra_snapshot
        )
        assert extra.analysis_result_id is None
        assert extra.cash_event_id is None

        # --- 再跑一次必须幂等 ---
        assert "无待执行动作（数据已就绪，幂等）" in _run_apply()

    def test_repair_preserves_consultation_history(self, applied):
        from sqlalchemy import select

        from app.models.cash import CashEvent
        from app.models.consultation import ConsultationCase, ConsultationUpdate

        db, profile, _merchant_user, _out = applied

        events = db.scalars(
            select(CashEvent)
            .where(CashEvent.merchant_id == profile.id, CashEvent.state == "scheduled")
            .order_by(CashEvent.scheduled_at)
        ).all()
        cases = db.scalars(
            select(ConsultationCase)
            .where(ConsultationCase.merchant_id == profile.id)
            .order_by(ConsultationCase.case_no)
        ).all()
        for index, case in enumerate(cases):
            case.cash_event_id = events[index % len(events)].id
        db.commit()

        before = {
            case.case_no: (
                case.created_at,
                case.status,
                case.submitted_at,
                case.closed_at,
                case.assignee_id,
                len(case.updates),
            )
            for case in cases
        }
        update_count = db.query(ConsultationUpdate).count()

        _run_apply()
        db.expire_all()

        for case in db.scalars(
            select(ConsultationCase).where(ConsultationCase.merchant_id == profile.id)
        ).all():
            created_at, status, submitted_at, closed_at, assignee, updates = before[case.case_no]
            assert case.created_at == created_at
            assert case.status == status
            assert case.submitted_at == submitted_at
            assert case.closed_at == closed_at
            assert case.assignee_id == assignee
            assert len(case.updates) == updates
        assert db.query(ConsultationUpdate).count() == update_count

    def test_unknown_event_title_stops_the_script(
        self, monkeypatch, product_client, db_session
    ):
        """Spec 声明的标题找不到时必须报错停止，绝不退回按位置关联。"""
        original = script.CONSULTATION_SPECS[5]
        broken = script.ConsultationSpec(
            title=original.title,
            event_title="不存在的事项",
            question_type=original.question_type,
            question=original.question,
            target_status=original.target_status,
            resolution_summary=original.resolution_summary,
        )
        monkeypatch.setattr(
            script,
            "CONSULTATION_SPECS",
            (*script.CONSULTATION_SPECS[:5], broken, *script.CONSULTATION_SPECS[6:]),
        )
        with pytest.raises(RuntimeError):
            _run("--apply")
