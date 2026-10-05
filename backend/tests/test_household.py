"""家庭协同测试：家庭创建、成员加入、分享权限、反馈与隐私边界。"""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from tests import fixtures_api as api_fx
from tests.conftest import DEFAULT_PASSWORD, login, provision_user, register

DECISION_FIELDS = ["max_withdrawable", "limiting_point", "risk_summary", "key_payments"]


def make_family(merchant_client: TestClient, member_client: TestClient):
    """商户创建家庭，家庭成员用邀请码申请并获批。"""
    household = merchant_client.post("/api/v1/households", json={"name": "王家小院"})
    assert household.status_code == 201, household.text
    body = household.json()
    code = body["invite_code"]
    assert code

    join = member_client.post(
        "/api/v1/households/join", json={"invite_code": code, "relation_label": "配偶"}
    )
    assert join.status_code == 200, join.text
    membership_id = join.json()["membership_id"]
    assert join.json()["status"] == "pending"

    approve = merchant_client.post(f"/api/v1/households/members/{membership_id}/approve")
    assert approve.status_code == 200, approve.text
    return body, code, membership_id


@pytest.fixture
def member_client(app):
    with TestClient(app) as client:
        client.headers.update({"X-Requested-With": "XMLHttpRequest"})
        assert (
            register(client, username="family_member_a", display_name="王太太", roles=["family_member"]).status_code
            == 201
        )
        assert login(client, username="family_member_a").status_code == 200
        yield client


@pytest.fixture
def family(merchant_client: TestClient, member_client: TestClient):
    api_fx.setup_merchant(merchant_client)
    household, code, membership_id = make_family(merchant_client, member_client)
    return {
        "client": merchant_client,
        "member": member_client,
        "household": household,
        "code": code,
        "membership_id": membership_id,
    }


class TestHousehold:
    def test_create_household(self, merchant_client: TestClient):
        response = merchant_client.post("/api/v1/households", json={"name": "李家小店"})
        assert response.status_code == 201
        body = response.json()
        assert body["name"] == "李家小店"
        assert body["invite_code"]
        assert len(body["invite_code"]) == 8

    def test_cannot_create_twice(self, merchant_client: TestClient):
        merchant_client.post("/api/v1/households", json={"name": "第一家庭"})
        second = merchant_client.post("/api/v1/households", json={"name": "第二家庭"})
        assert second.status_code == 409

    def test_consultant_cannot_create_household(self, client: TestClient):
        provision_user(username="con_hh", roles=["consultant"])
        client.cookies.clear()
        login(client, username="con_hh")
        assert client.post("/api/v1/households", json={"name": "x"}).status_code == 403

    def test_member_cannot_create_household(self, member_client: TestClient):
        assert member_client.post("/api/v1/households", json={"name": "x"}).status_code == 403

    def test_rotate_invite_code_invalidates_old(self, merchant_client: TestClient, member_client: TestClient):
        created = merchant_client.post("/api/v1/households", json={"name": "轮换测试"}).json()
        old_code = created["invite_code"]
        rotated = merchant_client.post("/api/v1/households/invite-code/rotate")
        assert rotated.status_code == 200
        new_code = rotated.json()["invite_code"]
        assert new_code != old_code

        assert member_client.post("/api/v1/households/join", json={"invite_code": old_code}).status_code == 404
        assert member_client.post("/api/v1/households/join", json={"invite_code": new_code}).status_code == 200

    def test_invalid_invite_code(self, member_client: TestClient):
        response = member_client.post("/api/v1/households/join", json={"invite_code": "ZZZZZZZZ"})
        assert response.status_code == 404
        assert response.json()["code"] == "INVALID_INVITE_CODE"

    def test_duplicate_join_rejected(self, family):
        response = family["member"].post(
            "/api/v1/households/join", json={"invite_code": family["code"]}
        )
        assert response.status_code == 409

    def test_member_list_shows_status(self, family):
        members = family["client"].get("/api/v1/households/members").json()
        statuses = {item["display_name"]: item["status"] for item in members}
        assert statuses["王太太"] == "active"

    def test_pending_member_cannot_receive_cards(self, merchant_client: TestClient, member_client: TestClient):
        api_fx.setup_merchant(merchant_client)
        created = merchant_client.post("/api/v1/households", json={"name": "待确认"}).json()
        member_client.post("/api/v1/households/join", json={"invite_code": created["invite_code"]})
        response = merchant_client.post(
            "/api/v1/household-cards",
            json={"card_type": "decision", "title": "决策卡", "shared_fields": DECISION_FIELDS},
        )
        assert response.status_code == 422
        assert response.json()["code"] == "NO_ACTIVE_MEMBER"

    def test_remove_member(self, family):
        response = family["client"].post(
            f"/api/v1/households/members/{family['membership_id']}/remove"
        )
        assert response.status_code == 200
        members = family["client"].get("/api/v1/households/members").json()
        target = next(item for item in members if item["display_name"] == "王太太")
        assert target["status"] == "removed"

    def test_cannot_remove_owner(self, family):
        members = family["client"].get("/api/v1/households/members").json()
        owner = next(item for item in members if item["role"] == "owner")
        response = family["client"].post(
            f"/api/v1/households/members/{owner['membership_id']}/remove"
        )
        assert response.status_code == 422

    def test_my_memberships(self, family):
        rows = family["member"].get("/api/v1/households/memberships/mine").json()
        assert len(rows) == 1
        assert rows[0]["household_name"] == "王家小院"
        assert rows[0]["status"] == "active"


class TestSharePreview:
    def test_preview_contains_only_selected_fields(self, family):
        analysis = family["client"].post("/api/v1/analysis/run", json={"mode": "current_plan"}).json()
        preview = family["client"].post(
            "/api/v1/household-cards/preview",
            json={
                "card_type": "decision",
                "shared_fields": ["max_withdrawable", "limiting_point"],
                "analysis_result_id": analysis["id"],
                "planned_household_amount_cents": 100000,
            },
        )
        assert preview.status_code == 200, preview.text
        body = preview.json()
        assert body["payload"]["max_withdrawable_cents"] == 1200_00
        assert "limiting_timestamp" in body["payload"]
        # 未选择的字段不应出现
        assert "risk_summary" not in body["payload"]
        assert "buffer_cents" not in body["payload"]
        assert set(body["shared_fields"]) == {"max_withdrawable", "limiting_point"}

    def test_preview_requires_at_least_one_field(self, family):
        response = family["client"].post(
            "/api/v1/household-cards/preview",
            json={"card_type": "decision", "shared_fields": []},
        )
        assert response.status_code == 422

    def test_sensitive_fields_never_shareable(self, family):
        response = family["client"].post(
            "/api/v1/household-cards/preview",
            json={
                "card_type": "decision",
                "shared_fields": ["opening_balance", "all_transactions", "max_withdrawable"],
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["shared_fields"] == ["max_withdrawable"]
        assert "opening_balance_cents" not in body["payload"]

    def test_shareable_field_whitelist_endpoint(self, family):
        body = family["client"].get("/api/v1/households/shareable-fields").json()
        assert "max_withdrawable" in body["shareable"]
        assert "opening_balance" in body["sensitive_default_off"]


class TestCards:
    def test_create_decision_card(self, family):
        analysis = family["client"].post("/api/v1/analysis/run", json={"mode": "current_plan"}).json()
        response = family["client"].post(
            "/api/v1/household-cards",
            json={
                "card_type": "decision",
                "shared_fields": DECISION_FIELDS,
                "analysis_result_id": analysis["id"],
                "planned_household_amount_cents": 100000,
            },
        )
        assert response.status_code == 201, response.text
        card = response.json()
        assert card["system_max_withdrawable_cents"] == 1200_00
        assert card["planned_household_amount_cents"] == 100000
        assert len(card["recipients"]) == 1

    def test_member_sees_only_shared_cards(self, family):
        analysis = family["client"].post("/api/v1/analysis/run", json={"mode": "current_plan"}).json()
        family["client"].post(
            "/api/v1/household-cards",
            json={
                "card_type": "decision",
                "shared_fields": ["max_withdrawable"],
                "analysis_result_id": analysis["id"],
            },
        )
        cards = family["member"].get("/api/v1/household-cards").json()
        assert len(cards) == 1
        assert cards[0]["payload"]["max_withdrawable_cents"] == 1200_00

    def test_member_cannot_read_unshared_card(self, family, merchant_client: TestClient):
        analysis = family["client"].post("/api/v1/analysis/run", json={"mode": "current_plan"}).json()
        card = family["client"].post(
            "/api/v1/household-cards",
            json={
                "card_type": "decision",
                "shared_fields": ["max_withdrawable"],
                "analysis_result_id": analysis["id"],
            },
        ).json()

        # 第二个家庭成员也能加入，但不是这张卡的接收人
        with TestClient(family["member"].app) as other:
            other.headers.update({"X-Requested-With": "XMLHttpRequest"})
            register(other, username="family_member_b", display_name="王弟", roles=["family_member"])
            login(other, username="family_member_b")
            other.post("/api/v1/households/join", json={"invite_code": family["code"]})
            members = family["client"].get("/api/v1/households/members").json()
            pending = next(item for item in members if item["display_name"] == "王弟")
            family["client"].post(f"/api/v1/households/members/{pending['membership_id']}/approve")

            assert other.get(f"/api/v1/household-cards/{card['id']}").status_code == 403
            assert other.get("/api/v1/household-cards").json() == []
        _ = merchant_client

    def test_mark_read_and_react(self, family):
        analysis = family["client"].post("/api/v1/analysis/run", json={"mode": "current_plan"}).json()
        card = family["client"].post(
            "/api/v1/household-cards",
            json={
                "card_type": "decision",
                "shared_fields": ["max_withdrawable"],
                "analysis_result_id": analysis["id"],
            },
        ).json()

        read = family["member"].post(f"/api/v1/household-cards/{card['id']}/read")
        assert read.status_code == 200
        assert read.json()["is_read"] is True

        agree = family["member"].post(
            f"/api/v1/household-cards/{card['id']}/react", json={"reaction": "agree"}
        )
        assert agree.status_code == 200
        assert agree.json()["my_reaction"] == "agree"

        discuss = family["member"].post(
            f"/api/v1/household-cards/{card['id']}/react", json={"reaction": "discuss"}
        )
        assert discuss.json()["my_reaction"] == "discuss"

        recipients = family["client"].get(f"/api/v1/household-cards/{card['id']}").json()["recipients"]
        assert recipients[0]["reaction"] == "discuss"

    def test_invalid_reaction_rejected(self, family):
        analysis = family["client"].post("/api/v1/analysis/run", json={"mode": "current_plan"}).json()
        card = family["client"].post(
            "/api/v1/household-cards",
            json={
                "card_type": "decision",
                "shared_fields": ["max_withdrawable"],
                "analysis_result_id": analysis["id"],
            },
        ).json()
        response = family["member"].post(
            f"/api/v1/household-cards/{card['id']}/react", json={"reaction": "angry"}
        )
        assert response.status_code == 422

    def test_comment_on_card(self, family):
        analysis = family["client"].post("/api/v1/analysis/run", json={"mode": "current_plan"}).json()
        card = family["client"].post(
            "/api/v1/household-cards",
            json={
                "card_type": "decision",
                "shared_fields": ["max_withdrawable"],
                "analysis_result_id": analysis["id"],
            },
        ).json()

        response = family["member"].post(
            f"/api/v1/household-cards/{card['id']}/comments",
            json={"content": "这个月孩子学费也要交，能不能少提一点？"},
        )
        assert response.status_code == 200
        comments = response.json()["comments"]
        assert len(comments) == 1
        assert comments[0]["display_name"] == "王太太"

    def test_empty_comment_rejected(self, family):
        analysis = family["client"].post("/api/v1/analysis/run", json={"mode": "current_plan"}).json()
        card = family["client"].post(
            "/api/v1/household-cards",
            json={
                "card_type": "decision",
                "shared_fields": ["max_withdrawable"],
                "analysis_result_id": analysis["id"],
            },
        ).json()
        response = family["member"].post(
            f"/api/v1/household-cards/{card['id']}/comments", json={"content": "   "}
        )
        assert response.status_code == 422

    def test_risk_card_contains_gap(self, family):
        """缺口金额必须显式勾选「付款缺口」，不再由「风险摘要」隐式带出。"""
        analysis = family["client"].post(
            "/api/v1/analysis/run", json={"mode": "delayed", "delay_days": 2}
        ).json()
        card = family["client"].post(
            "/api/v1/household-cards",
            json={
                "card_type": "risk",
                "shared_fields": ["risk_summary", "limiting_point", "payment_gap"],
                "analysis_result_id": analysis["id"],
            },
        ).json()
        assert card["payload"]["payment_gap_cents"] == 200_00
        # 文案只表达「有缺口」，金额只出现在独立字段里
        assert card["payload"]["risk_summary"] == (
            "未来 7 天存在付款缺口，建议优先确认近期付款与到账安排。"
        )
        assert "¥" not in card["payload"]["risk_summary"]

    def test_risk_summary_alone_does_not_leak_gap_amounts(self, family):
        """只勾「风险摘要」时，缺口与余额一律不出现（最小披露）。"""
        analysis = family["client"].post(
            "/api/v1/analysis/run", json={"mode": "delayed", "delay_days": 2}
        ).json()
        card = family["client"].post(
            "/api/v1/household-cards",
            json={
                "card_type": "risk",
                "shared_fields": ["risk_summary"],
                "analysis_result_id": analysis["id"],
            },
        ).json()
        payload = card["payload"]
        assert "risk_summary" in payload
        for leaked in (
            "payment_gap_cents",
            "buffer_gap_cents",
            "buffer_cents",
            "limiting_balance_cents",
            "end_balance_cents",
            "limiting_timestamp",
        ):
            assert leaked not in payload, leaked

        # 摘要文案本身也不能含金额：没有货币符号，「未来 7 天」之外没有数字
        text = payload["risk_summary"]
        assert "¥" not in text
        assert "200" not in text and "1800" not in text
        assert not any(ch.isdigit() for ch in text.replace("未来 7 天", ""))

    def test_limiting_point_does_not_bring_balance(self, family):
        """勾「最紧张时间」只给出时间，不给出余额或期末余额。"""
        analysis = family["client"].post(
            "/api/v1/analysis/run", json={"mode": "current_plan"}
        ).json()
        card = family["client"].post(
            "/api/v1/household-cards",
            json={
                "card_type": "decision",
                "shared_fields": ["limiting_point"],
                "analysis_result_id": analysis["id"],
            },
        ).json()
        payload = card["payload"]
        assert "limiting_timestamp" in payload
        assert "limiting_balance_cents" not in payload
        assert "end_balance_cents" not in payload

    def test_revision_card_links_event(self, family):
        """变更卡可以关联事项，但未勾选事项字段时不带出任何事项细节。"""
        event_id = family["client"].get("/api/v1/cash-events").json()["items"][0]["id"]
        card = family["client"].post(
            "/api/v1/household-cards",
            json={
                "card_type": "revision",
                # 变更摘要来自真实版本记录；该事项创建后从未更正过，因此没有摘要可分享
                "shared_fields": ["revision_summary"],
                "cash_event_id": event_id,
            },
        ).json()
        assert card["card_type"] == "revision"
        assert card["cash_event_id"] == event_id
        assert "revision_summary" not in card["payload"]
        for leaked in ("event_title", "event_amount_cents", "event_scheduled_at", "event_version"):
            assert leaked not in card["payload"], leaked

    def test_revision_card_with_key_payments_carries_event_detail(self, family):
        """只有勾选「关键经营付款」才允许带出事项级字段。"""
        event_id = family["client"].get("/api/v1/cash-events").json()["items"][0]["id"]
        card = family["client"].post(
            "/api/v1/household-cards",
            json={
                "card_type": "revision",
                "shared_fields": ["key_payments"],
                "cash_event_id": event_id,
            },
        ).json()
        assert card["payload"]["event_version"] == 1
        assert card["payload"]["event_title"]

    def test_revision_card_without_event_fields_hides_them(self, family):
        """未勾选事项字段时，事项级内容不得出现在 payload 或标题里。"""
        event_id = family["client"].get("/api/v1/cash-events").json()["items"][0]["id"]
        card = family["client"].post(
            "/api/v1/household-cards",
            json={
                "card_type": "revision",
                "shared_fields": ["max_withdrawable", "limiting_point"],
                "cash_event_id": event_id,
            },
        ).json()
        for forbidden in (
            "event_title",
            "event_amount_cents",
            "event_scheduled_at",
            "event_version",
        ):
            assert forbidden not in card["payload"], forbidden
        assert card["title"] == "事项变更通知"

    def test_owner_can_update_planned_amount(self, family):
        analysis = family["client"].post("/api/v1/analysis/run", json={"mode": "current_plan"}).json()
        card = family["client"].post(
            "/api/v1/household-cards",
            json={
                "card_type": "decision",
                "shared_fields": ["max_withdrawable"],
                "analysis_result_id": analysis["id"],
            },
        ).json()
        updated = family["client"].patch(
            f"/api/v1/household-cards/{card['id']}",
            json={"planned_household_amount_cents": 80000},
        ).json()
        assert updated["planned_household_amount_cents"] == 80000

    def test_owner_can_delete_card(self, family):
        analysis = family["client"].post("/api/v1/analysis/run", json={"mode": "current_plan"}).json()
        card = family["client"].post(
            "/api/v1/household-cards",
            json={
                "card_type": "decision",
                "shared_fields": ["max_withdrawable"],
                "analysis_result_id": analysis["id"],
            },
        ).json()
        assert family["client"].delete(f"/api/v1/household-cards/{card['id']}").status_code == 200
        assert family["member"].get("/api/v1/household-cards").json() == []

    def test_revision_notices_endpoint(self, family):
        event_id = family["client"].get("/api/v1/cash-events").json()["items"][0]["id"]
        family["client"].post(
            "/api/v1/household-cards",
            json={
                "card_type": "revision",
                "shared_fields": ["max_withdrawable"],
                "cash_event_id": event_id,
            },
        )
        notices = family["member"].get("/api/v1/household-cards/revision-notices").json()
        assert len(notices) == 1
        assert notices[0]["card_type"] == "revision"


class TestFamilyPrivacy:
    def test_member_cannot_read_cash_events(self, family):
        assert family["member"].get("/api/v1/cash-events").status_code == 403

    def test_member_cannot_read_account_overview(self, family):
        assert family["member"].get("/api/v1/account/overview").status_code == 403

    def test_member_cannot_read_analysis(self, family):
        assert family["member"].get("/api/v1/analysis/today").status_code == 403
        assert family["member"].post("/api/v1/analysis/run", json={}).status_code == 403

    def test_member_cannot_read_merchant_profile(self, family):
        assert family["member"].get("/api/v1/merchant/profile").status_code == 403

    def test_member_cannot_read_consultations(self, family):
        assert family["member"].get("/api/v1/consultations").status_code == 403

    def test_member_cannot_create_consultation(self, family):
        response = family["member"].post(
            "/api/v1/consultations",
            json={"cash_event_id": "x", "question_type": "other", "question": "测试问题"},
        )
        assert response.status_code == 403

    def test_member_cannot_read_import_batches(self, family):
        assert family["member"].get("/api/v1/imports/batches").status_code == 403

    def test_member_cannot_manage_household_members(self, family):
        members = family["client"].get("/api/v1/households/members").json()
        target = next(item for item in members if item["display_name"] == "王太太")
        response = family["member"].post(
            f"/api/v1/households/members/{target['membership_id']}/remove"
        )
        assert response.status_code == 403

    def test_member_cannot_create_card(self, family):
        response = family["member"].post(
            "/api/v1/household-cards",
            json={"card_type": "decision", "shared_fields": ["max_withdrawable"]},
        )
        assert response.status_code == 403

    def test_consultant_cannot_read_household(self, client: TestClient, family):
        provision_user(username="con_privacy", roles=["consultant"])
        client.cookies.clear()
        login(client, username="con_privacy")
        # 咨询人员不是经营主体，没有家庭上下文
        assert client.get("/api/v1/households/current").json() is None
        # 没有分享给咨询人员的卡片，列表为空而不是泄露
        assert client.get("/api/v1/household-cards").json() == []
        # 也不能创建家庭
        assert client.post("/api/v1/households", json={"name": "x"}).status_code == 403

    def test_family_data_not_leaked_in_member_view(self, family):
        analysis = family["client"].post("/api/v1/analysis/run", json={"mode": "current_plan"}).json()
        family["client"].post(
            "/api/v1/household-cards",
            json={
                "card_type": "decision",
                "shared_fields": ["max_withdrawable", "limiting_point"],
                "analysis_result_id": analysis["id"],
            },
        )
        cards = family["member"].get("/api/v1/household-cards").json()
        text = str(cards)
        assert "opening_balance" not in text
        assert "buffer_gap" not in text
        assert "payment_gap" not in text


class TestCardAccessIsolation:
    def test_other_merchant_cannot_read_card(
        self, family, second_merchant_client: TestClient
    ):
        analysis = family["client"].post("/api/v1/analysis/run", json={"mode": "current_plan"}).json()
        card = family["client"].post(
            "/api/v1/household-cards",
            json={
                "card_type": "decision",
                "shared_fields": ["max_withdrawable"],
                "analysis_result_id": analysis["id"],
            },
        ).json()
        assert second_merchant_client.get(f"/api/v1/household-cards/{card['id']}").status_code == 404
        assert second_merchant_client.delete(f"/api/v1/household-cards/{card['id']}").status_code == 404

    def test_member_of_other_household_cannot_read(
        self, family, app
    ):
        analysis = family["client"].post("/api/v1/analysis/run", json={"mode": "current_plan"}).json()
        card = family["client"].post(
            "/api/v1/household-cards",
            json={
                "card_type": "decision",
                "shared_fields": ["max_withdrawable"],
                "analysis_result_id": analysis["id"],
            },
        ).json()

        with TestClient(app) as stranger:
            stranger.headers.update({"X-Requested-With": "XMLHttpRequest"})
            register(
                stranger,
                username="stranger_member",
                display_name="陌生人",
                roles=["family_member"],
                password=DEFAULT_PASSWORD,
            )
            login(stranger, username="stranger_member")
            assert stranger.get(f"/api/v1/household-cards/{card['id']}").status_code == 403
            assert stranger.post(f"/api/v1/household-cards/{card['id']}/read").status_code == 403
            assert (
                stranger.post(
                    f"/api/v1/household-cards/{card['id']}/comments", json={"content": "hi"}
                ).status_code
                == 403
            )


class TestAudit:
    def test_household_actions_are_audited(self, family, db_session):
        from app.models.consultation import AuditLog

        analysis = family["client"].post("/api/v1/analysis/run", json={"mode": "current_plan"}).json()
        family["client"].post(
            "/api/v1/household-cards",
            json={
                "card_type": "decision",
                "shared_fields": ["max_withdrawable"],
                "analysis_result_id": analysis["id"],
            },
        )
        actions = [row.action for row in db_session.query(AuditLog).all()]
        assert "household.created" in actions
        assert "household.join_requested" in actions
        assert "household.member_approved" in actions
        assert "household.card_shared" in actions
