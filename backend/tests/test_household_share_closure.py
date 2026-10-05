"""家庭分享收口专项：风险摘要去金额、空到账收入拒绝、绑定情景、真实更正摘要。

对应本轮四处收口（基线 S5 / S6 / S8 / S9）：

* 只勾「风险摘要」时**任何金额都不能出现**（否则等于隐式分享未勾选字段）；
* 勾了「尚未到账的收入」却没有任何数据时，预览与创建都必须 422；
* 关键付款必须来自 ``binding_scenario_index`` 指向的绑定情景，而不是 ``scenarios[0]``；
* 更正摘要来自 ``cash_event_revisions`` 的真实版本记录，取不到就不生成该键。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.models.cash import AnalysisResult
from app.services.household_service import RISK_SUMMARY_TEXTS, HouseholdService
from tests import fixtures_api as api_fx
from tests.conftest import login, register

PAYMENT_GAP_TEXT = "未来 7 天存在付款缺口，建议优先确认近期付款与到账安排。"
BELOW_BUFFER_TEXT = "未来 7 天预计会低于经营留底，建议暂缓增加家庭提用。"
FEASIBLE_TEXT = "当前已确认的收付款安排可以覆盖。"
INPUT_INCOMPLETE_TEXT = "部分收付款信息尚待确认。"


def _amount_free(text: str) -> bool:
    """文案里是否没有任何金额痕迹。

    「未来 7 天」是固定的窗口表述，不是金额；除此之外不允许出现数字，
    也不允许出现货币符号。
    """
    return "¥" not in text and not any(ch.isdigit() for ch in text.replace("未来 7 天", ""))


def _run_analysis(
    client: TestClient, *, mode: str = "current_plan", delay_days: int | None = None
) -> dict:
    body: dict = {"mode": mode}
    if delay_days is not None:
        body["delay_days"] = delay_days
    response = client.post("/api/v1/analysis/run", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def _preview(client: TestClient, **overrides):
    body = {"card_type": "decision", "shared_fields": []}
    body.update(overrides)
    return client.post("/api/v1/household-cards/preview", json=body)


def _create(client: TestClient, **overrides):
    body = {"card_type": "decision", "shared_fields": []}
    body.update(overrides)
    return client.post("/api/v1/household-cards", json=body)


def _outflow_points(curve: dict) -> list[dict]:
    """把引擎情景曲线里的支出时点渲染成 ``_key_payments()`` 的分享结构。"""
    return [
        {
            "title": point.get("event_title"),
            "amount_text": point.get("delta_text"),
            "scheduled_at": point.get("timestamp"),
            "balance_text": point.get("balance_text"),
        }
        for point in curve["points"]
        if point.get("direction") == "outflow"
    ][:5]


@pytest.fixture
def member_client(app):
    """已加入家庭的成员客户端。"""
    with TestClient(app) as client:
        client.headers.update({"X-Requested-With": "XMLHttpRequest"})
        created = register(
            client, username="closure_member", display_name="王太太", roles=["family_member"]
        )
        assert created.status_code == 201, created.text
        assert login(client, username="closure_member").status_code == 200
        yield client


@pytest.fixture
def family(merchant_client: TestClient, member_client: TestClient):
    api_fx.setup_merchant(merchant_client)
    household = merchant_client.post("/api/v1/households", json={"name": "王家小院"})
    assert household.status_code == 201, household.text
    code = household.json()["invite_code"]

    join = member_client.post(
        "/api/v1/households/join", json={"invite_code": code, "relation_label": "配偶"}
    )
    assert join.status_code == 200, join.text
    approved = merchant_client.post(
        f"/api/v1/households/members/{join.json()['membership_id']}/approve"
    )
    assert approved.status_code == 200, approved.text
    return {"client": merchant_client, "member": member_client, "household": household.json()}


# ---------------------------------------------------------------------------
# 1) 风险摘要去金额化
# ---------------------------------------------------------------------------
class TestRiskSummaryHasNoAmounts:
    def test_only_risk_summary_payload_has_exactly_summary_and_status(self, family):
        """只勾「风险摘要」：payload 只有 risk_summary + status，且文案不含金额。"""
        analysis = _run_analysis(family["client"], mode="delayed", delay_days=2)
        assert analysis["status"] == "PAYMENT_GAP"
        assert analysis["payment_gap_cents"] == 200_00

        card = _create(
            family["client"],
            card_type="risk",
            shared_fields=["risk_summary"],
            analysis_result_id=analysis["id"],
        )
        assert card.status_code == 201, card.text
        payload = card.json()["payload"]

        assert set(payload) == {"risk_summary", "status"}
        assert payload["status"] == "PAYMENT_GAP"
        assert payload["risk_summary"] == PAYMENT_GAP_TEXT
        assert _amount_free(payload["risk_summary"])

        # 隐式泄漏的具体形态：缺口金额、最紧时点余额、期末余额
        for leaked in ("200.00", "20000", "1,800.00", "1800.00", "180000"):
            assert leaked not in payload["risk_summary"], leaked

    def test_risk_summary_with_payment_gap_keeps_gap_in_own_field(self, family):
        """风险摘要 + 付款缺口：摘要仍然无金额，缺口只出现在独立字段里。"""
        analysis = _run_analysis(family["client"], mode="delayed", delay_days=2)
        card = _create(
            family["client"],
            card_type="risk",
            shared_fields=["risk_summary", "payment_gap"],
            analysis_result_id=analysis["id"],
        )
        assert card.status_code == 201, card.text
        payload = card.json()["payload"]

        assert set(payload) == {"risk_summary", "status", "payment_gap_cents"}
        assert payload["payment_gap_cents"] == 200_00
        assert payload["risk_summary"] == PAYMENT_GAP_TEXT
        assert _amount_free(payload["risk_summary"])

    def test_feasible_risk_summary_is_amount_free(self, family):
        analysis = _run_analysis(family["client"], mode="current_plan")
        assert analysis["status"] == "FEASIBLE"
        card = _create(
            family["client"],
            card_type="risk",
            shared_fields=["risk_summary"],
            analysis_result_id=analysis["id"],
        )
        assert card.status_code == 201, card.text
        payload = card.json()["payload"]
        assert set(payload) == {"risk_summary", "status"}
        assert payload["risk_summary"] == FEASIBLE_TEXT
        assert _amount_free(payload["risk_summary"])

    def test_every_status_text_is_fixed_and_amount_free(self):
        """四种状态各有一句固定文案：都只表达状态语义。"""
        cases = (
            ("PAYMENT_GAP", 200_00, 800_00, PAYMENT_GAP_TEXT),
            ("BELOW_BUFFER", 0, 800_00, BELOW_BUFFER_TEXT),
            ("FEASIBLE", 0, 0, FEASIBLE_TEXT),
            ("INPUT_INCOMPLETE", 0, 0, INPUT_INCOMPLETE_TEXT),
        )
        for status, payment_gap, buffer_gap, expected in cases:
            analysis = AnalysisResult(
                status=status, payment_gap_cents=payment_gap, buffer_gap_cents=buffer_gap
            )
            text = HouseholdService._risk_summary(analysis)
            assert text == expected, status
            assert _amount_free(text), (status, text)

        assert set(RISK_SUMMARY_TEXTS) == {
            "PAYMENT_GAP",
            "BELOW_BUFFER",
            "FEASIBLE",
            "INPUT_INCOMPLETE",
        }


# ---------------------------------------------------------------------------
# 2) pending_inflows 为空必须被拒绝
# ---------------------------------------------------------------------------
class TestPendingInflowsMustNotBeEmpty:
    def test_preview_rejects_empty_pending_inflows(self, family):
        """预览接口也要走同一校验，不能只靠前端置灰。"""
        analysis = _run_analysis(family["client"], mode="current_plan")
        assert analysis["pending_inflows_at_limit"] == []

        response = _preview(
            family["client"],
            card_type="decision",
            shared_fields=["pending_inflows"],
            analysis_result_id=analysis["id"],
        )
        assert response.status_code == 422, response.text
        assert response.json()["code"] == "NO_PENDING_INFLOW_TO_SHARE"

    def test_create_rejects_empty_pending_inflows(self, family):
        analysis = _run_analysis(family["client"], mode="current_plan")
        response = _create(
            family["client"],
            card_type="decision",
            shared_fields=["pending_inflows"],
            analysis_result_id=analysis["id"],
        )
        assert response.status_code == 422, response.text
        assert response.json()["code"] == "NO_PENDING_INFLOW_TO_SHARE"
        # 拒绝 = 不落库：家庭成员看不到任何卡片
        assert family["member"].get("/api/v1/household-cards").json() == []
        assert family["client"].get("/api/v1/household-cards").json() == []

    def test_shared_when_pending_inflows_exist(self, family):
        """有尚未到账的收入时正常分享，并只带接收端需要的三个键。"""
        analysis = _run_analysis(family["client"], mode="delayed", delay_days=2)
        assert analysis["pending_inflows_at_limit"], analysis

        preview = _preview(
            family["client"],
            card_type="decision",
            shared_fields=["pending_inflows"],
            analysis_result_id=analysis["id"],
        )
        assert preview.status_code == 200, preview.text
        pending = preview.json()["payload"]["pending_inflows"]
        assert [item["title"] for item in pending] == ["结算款"]
        assert pending[0]["amount_text"] == "¥2000.00"
        assert set(pending[0]) == {"title", "amount_text", "scheduled_at"}

        card = _create(
            family["client"],
            card_type="decision",
            shared_fields=["pending_inflows"],
            analysis_result_id=analysis["id"],
        )
        assert card.status_code == 201, card.text
        assert card.json()["payload"]["pending_inflows"] == pending

    def test_other_shared_fields_are_unaffected(self, family):
        """只勾其它字段时不受这条校验影响。"""
        analysis = _run_analysis(family["client"], mode="current_plan")
        card = _create(
            family["client"],
            card_type="decision",
            shared_fields=["max_withdrawable", "limiting_point"],
            analysis_result_id=analysis["id"],
        )
        assert card.status_code == 201, card.text
        payload = card.json()["payload"]
        assert payload["max_withdrawable_cents"] == 1200_00
        assert "pending_inflows" not in payload

    def test_rejects_when_no_analysis_exists(self, family):
        """连分析结果都没有时同样拒绝：拿不到数据就不能生成空数组。"""
        response = _create(
            family["client"], card_type="decision", shared_fields=["pending_inflows"]
        )
        assert response.status_code == 422, response.text
        assert response.json()["code"] == "NO_PENDING_INFLOW_TO_SHARE"


# ---------------------------------------------------------------------------
# 3) 关键付款必须使用绑定情景
# ---------------------------------------------------------------------------
class TestKeyPaymentsFollowBindingScenario:
    def test_joint_key_payments_come_from_binding_scenario(self, family):
        """共同约束下 scenarios[0] 不是绑定情景，分享的必须是绑定情景。"""
        analysis = _run_analysis(family["client"], mode="joint", delay_days=2)
        assert analysis["binding_scenario_index"] == 1, analysis
        binding = analysis["scenarios"][analysis["binding_scenario_index"]]
        first = analysis["scenarios"][0]
        # 前提：两个情景的关键付款确实不同，否则这条断言证明不了绑定生效
        assert _outflow_points(binding) != _outflow_points(first)

        card = _create(
            family["client"],
            card_type="decision",
            shared_fields=["key_payments"],
            analysis_result_id=analysis["id"],
        )
        assert card.status_code == 201, card.text
        payload = card.json()["payload"]

        assert payload["key_payments"] == _outflow_points(binding)
        assert payload["key_payments"] != _outflow_points(first)
        assert payload["limiting_event_title"] == binding["limiting_event_title"]

    def test_binding_scenario_falls_back_safely(self):
        """binding_scenario_index 缺失或越界时安全回退到 scenarios[0]。"""
        payload = {
            "scenarios": [
                {"points": [{"direction": "outflow", "event_title": "第一个情景"}]},
                {"points": [{"direction": "outflow", "event_title": "第二个情景"}]},
            ]
        }
        missing = AnalysisResult(status="FEASIBLE", payload=payload)
        assert HouseholdService._key_payments(missing)[0]["title"] == "第一个情景"

        out_of_range = AnalysisResult(
            status="FEASIBLE", payload={**payload, "binding_scenario_index": 9}
        )
        assert HouseholdService._key_payments(out_of_range)[0]["title"] == "第一个情景"

        bound = AnalysisResult(
            status="FEASIBLE", payload={**payload, "binding_scenario_index": 1}
        )
        assert HouseholdService._key_payments(bound)[0]["title"] == "第二个情景"

    def test_key_payment_title_uses_binding_scenario(self):
        """没有顶层 limiting_event_title 时，标题也来自绑定情景。"""
        limiting_at = datetime(2026, 10, 4, 1, 0, tzinfo=UTC)
        stamp = limiting_at.isoformat()
        analysis = AnalysisResult(
            status="PAYMENT_GAP",
            limiting_timestamp=limiting_at,
            payload={
                "binding_scenario_index": 1,
                "scenarios": [
                    {"points": [{"timestamp": stamp, "event_title": "第一个情景的事项"}]},
                    {"points": [{"timestamp": stamp, "event_title": "绑定情景的事项"}]},
                ],
            },
        )
        assert HouseholdService._key_payment_title(analysis) == "绑定情景的事项"


# ---------------------------------------------------------------------------
# 4) 更正摘要来自真实版本记录
# ---------------------------------------------------------------------------
class TestRevisionSummaryFromRealRevisions:
    def _create_event(self, family, *, amount_cents: int = 1300_00) -> str:
        created = family["client"].post(
            "/api/v1/cash-events",
            json={
                "cash_key": "CLOSURE-BUY-0001",
                "title": "鲜食原料采购",
                "direction": "outflow",
                "amount_cents": amount_cents,
                "scheduled_at": api_fx.day(2, 9).isoformat(),
                "event_type": "supplier_payment",
                "source_label": "采购单 CG-0001",
            },
        )
        assert created.status_code == 201, created.text
        return created.json()["id"]

    def _share_revision_summary(self, family, event_id: str | None):
        body = {
            "card_type": "revision",
            "shared_fields": ["revision_summary"],
        }
        if event_id is not None:
            body["cash_event_id"] = event_id
        card = family["client"].post("/api/v1/household-cards", json=body)
        assert card.status_code == 201, card.text
        return card.json()

    def test_revision_summary_reports_amount_change(self, family, db_session):
        """1300 → 1400 的真实改单：changes 里的 before/after 与文本都正确。"""
        event_id = self._create_event(family)
        updated = family["client"].patch(
            f"/api/v1/cash-events/{event_id}",
            json={"amount_cents": 1400_00, "change_reason": "product-data: 按实际单据修正"},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["current_version"] == 2

        card = self._share_revision_summary(family, event_id)
        payload = card["payload"]
        assert set(payload) == {"revision_summary"}

        summary = payload["revision_summary"]
        assert set(summary) == {
            "event_title",
            "version",
            "changed_at",
            "change_reason",
            "changes",
        }
        assert summary["event_title"] == "鲜食原料采购"
        assert summary["version"] == 2
        assert summary["change_reason"] == "product-data: 按实际单据修正"
        assert summary["changed_at"].endswith("+00:00")
        assert datetime.fromisoformat(summary["changed_at"]).tzinfo is not None

        changes = {item["field"]: item for item in summary["changes"]}
        assert set(changes) == {"amount_cents"}
        amount = changes["amount_cents"]
        assert set(amount) == {
            "field",
            "label",
            "before",
            "after",
            "before_text",
            "after_text",
        }
        assert amount["label"] == "金额"
        assert amount["before"] == 1300_00
        assert amount["after"] == 1400_00
        # 金额带千分位，与页面其它地方的写法一致（¥1,300.00 而不是 ¥1300.00）
        assert amount["before_text"] == "¥1,300.00"
        assert amount["after_text"] == "¥1,400.00"

        # 数据确实来自 cash_event_revisions 的真实版本记录（不是内存比较）
        from app.models.cash import CashEventRevision

        rows = (
            db_session.query(CashEventRevision)
            .filter(CashEventRevision.cash_event_id == event_id)
            .order_by(CashEventRevision.version.asc())
            .all()
        )
        assert [row.version for row in rows] == [1, 2]
        assert rows[1].material is True
        assert rows[1].change_reason == "product-data: 按实际单据修正"

    def test_material_revision_wins_over_later_cosmetic_change(self, family):
        """优先取最新一条 material=True：之后的非实质性改动不覆盖摘要。"""
        event_id = self._create_event(family)
        family["client"].patch(
            f"/api/v1/cash-events/{event_id}",
            json={"amount_cents": 1400_00, "change_reason": "按实际单据修正"},
        )
        family["client"].patch(
            f"/api/v1/cash-events/{event_id}",
            json={"note": "与供应商核对过"},
        )

        summary = self._share_revision_summary(family, event_id)["payload"]["revision_summary"]
        assert summary["version"] == 2
        assert {item["field"] for item in summary["changes"]} == {"amount_cents"}

    def test_non_material_change_is_still_summarised(self, family):
        """只有非实质性改动时退而取最新一条，且空值渲染成可读文本。"""
        event_id = self._create_event(family)
        family["client"].patch(f"/api/v1/cash-events/{event_id}", json={"note": "与供应商核对过"})

        summary = self._share_revision_summary(family, event_id)["payload"]["revision_summary"]
        assert summary["version"] == 2
        note = next(item for item in summary["changes"] if item["field"] == "note")
        assert note["label"] == "备注"
        assert note["before"] is None
        assert note["after"] == "与供应商核对过"
        assert note["before_text"] == "（未填写）"
        assert note["after_text"] == "与供应商核对过"

    def test_no_revision_means_no_key(self, family):
        """只有创建记录（没有「改前」）时不生成 revision_summary。"""
        # 夹具事项从创建后从未修改：版本 1 只是创建记录
        event_id = family["client"].get("/api/v1/cash-events").json()["items"][0]["id"]
        card = self._share_revision_summary(family, event_id)
        assert "revision_summary" not in card["payload"]

    def test_no_event_means_no_key(self, family):
        """没有关联事项时不生成 revision_summary。"""
        card = self._share_revision_summary(family, None)
        assert "revision_summary" not in card["payload"]

    def test_revision_summary_carries_only_that_event(self, family):
        """摘要只带该事项自身的差异：不带余额、不带其它事项、不带完整流水。"""
        event_id = self._create_event(family)
        family["client"].patch(
            f"/api/v1/cash-events/{event_id}",
            json={"amount_cents": 1400_00, "change_reason": "按实际单据修正"},
        )
        family["client"].post("/api/v1/analysis/run", json={"mode": "current_plan"})

        card = self._share_revision_summary(family, event_id)
        payload = card["payload"]
        assert set(payload) == {"revision_summary"}
        text = json.dumps(payload, ensure_ascii=False)
        for forbidden in (
            # 其它事项
            "进货款",
            "结算款",
            "房租",
            "已确认退款",
            # 账户余额 / 缺口 / 完整流水
            "opening_balance",
            "limiting_balance",
            "end_balance",
            "payment_gap",
            "buffer_gap",
            "max_withdrawable",
            "planned_household_amount",
            "key_payments",
        ):
            assert forbidden not in text, forbidden
        # 接收端（家庭成员）读到的也是同一份摘要
        received = family["member"].get(f"/api/v1/household-cards/{card['id']}")
        assert received.status_code == 200, received.text
        assert received.json()["payload"]["revision_summary"] == payload["revision_summary"]
