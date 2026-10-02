"""经营咨询测试：字段白名单、状态流转、权限边界、结果更正。"""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from tests import fixtures_api as api_fx
from tests.conftest import login, register


@pytest.fixture
def merchant_with_event(merchant_client: TestClient):
    base, event_ids = api_fx.setup_merchant(merchant_client)
    return merchant_client, base, event_ids


@pytest.fixture
def consultant_client(app):
    with TestClient(app) as client:
        client.headers.update({"X-Requested-With": "XMLHttpRequest"})
        register(client, username="consultant_a", display_name="咨询小李", roles=["consultant"])
        login(client, username="consultant_a")
        yield client


def create_case(merchant_client: TestClient, event_id: str, *, submit: bool = True):
    response = merchant_client.post(
        "/api/v1/consultations",
        json={
            "cash_event_id": event_id,
            "question_type": "settlement_time",
            "question": "这笔结算款原定 10 月 3 日到账，但账户还没收到，想确认结算进度。",
            "status": "submitted" if submit else "draft",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


class TestCreateConsultation:
    def test_create_and_submit(self, merchant_with_event):
        client, _, event_ids = merchant_with_event
        case = create_case(client, event_ids["API-SETTLE-0001"])
        assert case["status"] == "submitted"
        assert case["case_no"].startswith("ZX")
        assert case["submitted_at"]
        assert len(case["updates"]) >= 2

    def test_create_as_draft(self, merchant_with_event):
        client, _, event_ids = merchant_with_event
        case = create_case(client, event_ids["API-SETTLE-0001"], submit=False)
        assert case["status"] == "draft"
        assert case["submitted_at"] is None

    def test_unknown_question_type_rejected(self, merchant_with_event):
        client, _, event_ids = merchant_with_event
        response = client.post(
            "/api/v1/consultations",
            json={
                "cash_event_id": event_ids["API-SETTLE-0001"],
                "question_type": "not_a_type",
                "question": "测试问题内容",
            },
        )
        assert response.status_code == 422

    def test_other_merchant_event_rejected(
        self, merchant_with_event, second_merchant_client: TestClient
    ):
        _, _, event_ids = merchant_with_event
        response = second_merchant_client.post(
            "/api/v1/consultations",
            json={
                "cash_event_id": event_ids["API-SETTLE-0001"],
                "question_type": "other",
                "question": "越权测试问题",
            },
        )
        assert response.status_code == 404


class TestFieldWhitelist:
    def test_allowed_fields_preview(self, merchant_with_event):
        client, _, event_ids = merchant_with_event
        body = client.get(
            "/api/v1/consultations/allowed-fields",
            params={"cash_event_id": event_ids["API-SETTLE-0001"]},
        ).json()
        assert "event_title" in body["allowed_fields"]
        assert "amount_cents" in body["allowed_fields"]
        assert "opening_balance_cents" in body["forbidden_fields"]
        assert "max_withdrawable_cents" in body["forbidden_fields"]
        assert "household_info" in body["forbidden_fields"]

    def test_shared_fields_only_contain_whitelist(self, merchant_with_event):
        client, _, event_ids = merchant_with_event
        case = create_case(client, event_ids["API-SETTLE-0001"])
        shared = case["shared_fields"]
        assert set(shared) <= set(case["allowed_field_names"])
        # 敏感字段绝不出现在共享字段中
        for forbidden in (
            "opening_balance_cents",
            "buffer_cents",
            "max_withdrawable_cents",
            "household_info",
            "full_balance_curve",
        ):
            assert forbidden not in shared

    def test_shared_amount_matches_event(self, merchant_with_event):
        client, _, event_ids = merchant_with_event
        case = create_case(client, event_ids["API-SETTLE-0001"])
        assert case["shared_fields"]["amount_cents"] == 2200_00
        assert case["shared_fields"]["event_version"] == 1

    def test_resolution_fields_are_sanitised(self, merchant_with_event, consultant_client):
        client, _, event_ids = merchant_with_event
        case = create_case(client, event_ids["API-SETTLE-0001"])
        consultant_client.post(f"/api/v1/consultations/{case['id']}/start")
        verified = consultant_client.post(
            f"/api/v1/consultations/{case['id']}/verify",
            json={
                "resolution_summary": "该笔结算已完成。",
                "resolution_fields": {
                    "amount_cents": 235860,
                    "max_withdrawable_cents": 999999,
                    "opening_balance_cents": 123456,
                },
            },
        ).json()
        assert verified["resolution_fields"]["amount_cents"] == 235860
        assert "max_withdrawable_cents" not in verified["resolution_fields"]
        assert "opening_balance_cents" not in verified["resolution_fields"]


class TestStatusFlow:
    def test_full_lifecycle(self, merchant_with_event, consultant_client):
        client, _, event_ids = merchant_with_event
        case = create_case(client, event_ids["API-SETTLE-0001"])

        started = consultant_client.post(f"/api/v1/consultations/{case['id']}/start")
        assert started.status_code == 200
        assert started.json()["status"] == "under_review"

        info = consultant_client.post(
            f"/api/v1/consultations/{case['id']}/request-info",
            json={"content": "请提供该笔结算的批次号。"},
        )
        assert info.json()["status"] == "need_more_information"

        # 商户补充资料后，咨询人员重新进入处理中
        back = consultant_client.post(f"/api/v1/consultations/{case['id']}/start")
        assert back.status_code == 200
        assert back.json()["status"] == "under_review"

        verified = consultant_client.post(
            f"/api/v1/consultations/{case['id']}/verify",
            json={"resolution_summary": "已核实该笔结算于 10 月 3 日完成。"},
        )
        assert verified.json()["status"] == "verified"

        closed = consultant_client.post(f"/api/v1/consultations/{case['id']}/close", json={})
        assert closed.json()["status"] == "closed"
        assert closed.json()["closed_at"]

    def test_closed_case_cannot_transition(self, merchant_with_event, consultant_client):
        client, _, event_ids = merchant_with_event
        case = create_case(client, event_ids["API-SETTLE-0001"])
        consultant_client.post(f"/api/v1/consultations/{case['id']}/start")
        consultant_client.post(
            f"/api/v1/consultations/{case['id']}/verify",
            json={"resolution_summary": "核实完成"},
        )
        consultant_client.post(f"/api/v1/consultations/{case['id']}/close", json={})
        again = consultant_client.post(
            f"/api/v1/consultations/{case['id']}/request-info", json={"content": "再补充"}
        )
        assert again.status_code == 409

    def test_cannot_verify_draft(self, merchant_with_event, consultant_client):
        client, _, event_ids = merchant_with_event
        case = create_case(client, event_ids["API-SETTLE-0001"], submit=False)
        response = consultant_client.post(
            f"/api/v1/consultations/{case['id']}/verify",
            json={"resolution_summary": "跳步测试"},
        )
        assert response.status_code == 403

    def test_cannot_skip_states(self, merchant_with_event, consultant_client):
        client, _, event_ids = merchant_with_event
        case = create_case(client, event_ids["API-SETTLE-0001"])
        response = consultant_client.post(
            f"/api/v1/consultations/{case['id']}/verify",
            json={"resolution_summary": "跳步测试"},
        )
        assert response.status_code == 409
        assert response.json()["code"] == "INVALID_STATUS_TRANSITION"

    def test_timeline_records_all_steps(self, merchant_with_event, consultant_client):
        client, _, event_ids = merchant_with_event
        case = create_case(client, event_ids["API-SETTLE-0001"])
        consultant_client.post(f"/api/v1/consultations/{case['id']}/start")
        consultant_client.post(
            f"/api/v1/consultations/{case['id']}/verify",
            json={"resolution_summary": "核实完成"},
        )
        detail = client.get(f"/api/v1/consultations/{case['id']}").json()
        actions = [item["action"] for item in detail["updates"]]
        assert actions == ["created", "submitted", "under_review", "verified"]
        assert all(item["created_at"] for item in detail["updates"])

    def test_queue_buckets(self, merchant_with_event, consultant_client):
        client, _, event_ids = merchant_with_event
        create_case(client, event_ids["API-SETTLE-0001"])

        pending = consultant_client.get(
            "/api/v1/consultations/queue", params={"bucket": "submitted"}
        ).json()
        assert pending["meta"]["total"] == 1

        closed = consultant_client.get(
            "/api/v1/consultations/queue", params={"bucket": "closed"}
        ).json()
        assert closed["meta"]["total"] == 0

    def test_draft_not_visible_to_consultant(self, merchant_with_event, consultant_client):
        client, _, event_ids = merchant_with_event
        case = create_case(client, event_ids["API-SETTLE-0001"], submit=False)
        assert (
            consultant_client.get(f"/api/v1/consultations/{case['id']}").status_code == 403
        )
        queue = consultant_client.get("/api/v1/consultations/queue").json()
        assert queue["meta"]["total"] == 0


class TestApplyUpdate:
    def test_merchant_can_apply_verified_result(self, merchant_with_event, consultant_client):
        client, _, event_ids = merchant_with_event
        case = create_case(client, event_ids["API-SETTLE-0001"])
        consultant_client.post(f"/api/v1/consultations/{case['id']}/start")
        consultant_client.post(
            f"/api/v1/consultations/{case['id']}/verify",
            json={
                "resolution_summary": "实际到账金额为 2358.60 元，时间 10 月 5 日。",
                "resolution_fields": {"amount_cents": 235860},
            },
        )
        response = client.post(f"/api/v1/consultations/{case['id']}/apply-update")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["new_version"] == 2
        assert "amount_cents" in body["changed_fields"]

        event = client.get(f"/api/v1/cash-events/{event_ids['API-SETTLE-0001']}").json()
        assert event["amount_cents"] == 235860
        assert event["current_version"] == 2
        assert event["source_type"] == "consultation_update"

    def test_apply_triggers_recalculation(self, merchant_with_event, consultant_client):
        client, _, event_ids = merchant_with_event
        # 先落库一次分析结果，才能观察到"失效"效果
        assert client.post("/api/v1/analysis/run", json={"mode": "current_plan"}).status_code == 200
        assert client.get("/api/v1/analysis/stale").json()["is_stale"] is False
        before = client.get("/api/v1/analysis/today").json()
        assert before["max_withdrawable_cents"] == 1200_00

        case = create_case(client, event_ids["API-SETTLE-0001"])
        consultant_client.post(f"/api/v1/consultations/{case['id']}/start")
        consultant_client.post(
            f"/api/v1/consultations/{case['id']}/verify",
            json={
                "resolution_summary": "金额更正",
                "resolution_fields": {"amount_cents": 100000},
            },
        )
        applied = client.post(f"/api/v1/consultations/{case['id']}/apply-update")
        assert applied.status_code == 200, applied.text

        stale = client.get("/api/v1/analysis/stale").json()
        assert stale["is_stale"] is True
        assert "变更" in (stale["stale_reason"] or "")

        after = client.get("/api/v1/analysis/today").json()
        # 结算款从 2200 元下调到 1000 元后，最紧张时点余额降到留底水平，可提用金额归零
        assert after["max_withdrawable_cents"] == 0
        assert after["limiting_balance_cents"] == 600_00
        assert after["max_withdrawable_cents"] != before["max_withdrawable_cents"]

    def test_revision_history_records_consultation_source(
        self, merchant_with_event, consultant_client
    ):
        client, _, event_ids = merchant_with_event
        case = create_case(client, event_ids["API-SETTLE-0001"])
        consultant_client.post(f"/api/v1/consultations/{case['id']}/start")
        consultant_client.post(
            f"/api/v1/consultations/{case['id']}/verify",
            json={"resolution_summary": "金额更正", "resolution_fields": {"amount_cents": 77700}},
        )
        client.post(f"/api/v1/consultations/{case['id']}/apply-update")

        revisions = client.get(
            f"/api/v1/cash-events/{event_ids['API-SETTLE-0001']}/revisions"
        ).json()
        assert revisions[0]["version"] == 2
        assert "咨询" in (revisions[0]["change_reason"] or "")

        source = client.get(f"/api/v1/cash-events/{event_ids['API-SETTLE-0001']}/source").json()
        assert source["source_type"] == "consultation_update"
        assert case["case_no"] in (source["source_record"]["raw_content"] or "")

    def test_apply_before_verified_rejected(self, merchant_with_event):
        client, _, event_ids = merchant_with_event
        case = create_case(client, event_ids["API-SETTLE-0001"])
        response = client.post(f"/api/v1/consultations/{case['id']}/apply-update")
        assert response.status_code == 409
        assert response.json()["code"] == "CASE_NOT_VERIFIED"

    def test_consultant_cannot_apply_update(self, merchant_with_event, consultant_client):
        client, _, event_ids = merchant_with_event
        case = create_case(client, event_ids["API-SETTLE-0001"])
        consultant_client.post(f"/api/v1/consultations/{case['id']}/start")
        consultant_client.post(
            f"/api/v1/consultations/{case['id']}/verify",
            json={"resolution_summary": "核实完成", "resolution_fields": {"amount_cents": 1}},
        )
        response = consultant_client.post(f"/api/v1/consultations/{case['id']}/apply-update")
        assert response.status_code == 403

    def test_consultant_cannot_modify_cash_event(self, merchant_with_event, consultant_client):
        _, _, event_ids = merchant_with_event
        assert (
            consultant_client.patch(
                f"/api/v1/cash-events/{event_ids['API-SETTLE-0001']}", json={"amount_cents": 1}
            ).status_code
            == 403
        )
        assert (
            consultant_client.post(
                f"/api/v1/cash-events/{event_ids['API-SETTLE-0001']}/cancel"
            ).status_code
            == 403
        )


class TestConsultationPermissions:
    def test_unauthenticated_blocked(self, client: TestClient):
        client.cookies.clear()
        assert client.get("/api/v1/consultations").status_code == 401
        assert client.get("/api/v1/consultations/queue").status_code == 401

    def test_family_member_blocked(self, client: TestClient):
        register(client, username="fam_consult", roles=["family_member"])
        client.cookies.clear()
        login(client, username="fam_consult")
        assert client.get("/api/v1/consultations").status_code == 403
        assert client.get("/api/v1/consultations/queue").status_code == 403

    def test_merchant_cannot_access_queue(self, merchant_with_event):
        client, _, _ = merchant_with_event
        assert client.get("/api/v1/consultations/queue").status_code == 403

    def test_merchant_cannot_read_other_merchant_case(
        self, merchant_with_event, second_merchant_client: TestClient
    ):
        client, _, event_ids = merchant_with_event
        case = create_case(client, event_ids["API-SETTLE-0001"])
        assert (
            second_merchant_client.get(f"/api/v1/consultations/{case['id']}").status_code == 404
        )
        assert (
            second_merchant_client.post(
                f"/api/v1/consultations/{case['id']}/apply-update"
            ).status_code
            == 404
        )

    def test_consultant_cannot_read_merchant_cash_events(self, merchant_with_event, consultant_client):
        assert consultant_client.get("/api/v1/cash-events").status_code == 403
        assert consultant_client.get("/api/v1/account/overview").status_code == 403
        assert consultant_client.get("/api/v1/analysis/today").status_code == 403

    def test_consultant_cannot_read_household_cards(self, merchant_with_event, consultant_client):
        assert consultant_client.get("/api/v1/household-cards").json() == []

    def test_consultant_queue_does_not_leak_family_data(self, merchant_with_event, consultant_client):
        client, _, event_ids = merchant_with_event
        create_case(client, event_ids["API-SETTLE-0001"])
        queue = consultant_client.get("/api/v1/consultations/queue").json()
        text = str(queue)
        assert "household" not in text.lower()
        assert "opening_balance" not in text
        assert "max_withdrawable" not in text


class TestConsultationMeta:
    def test_meta_endpoint(self, merchant_client: TestClient):
        body = merchant_client.get("/api/v1/consultations/meta").json()
        statuses = {item["value"] for item in body["statuses"]}
        assert {"draft", "submitted", "under_review", "need_more_information", "verified", "closed"} == statuses
        assert any(item["value"] == "settlement_time" for item in body["question_types"])

    def test_list_with_status_filter(self, merchant_with_event):
        client, _, event_ids = merchant_with_event
        create_case(client, event_ids["API-SETTLE-0001"])
        create_case(client, event_ids["API-SETTLE-0002"], submit=False)

        submitted = client.get("/api/v1/consultations", params={"status": "submitted"}).json()
        assert submitted["meta"]["total"] == 1
        drafts = client.get("/api/v1/consultations", params={"status": "draft"}).json()
        assert drafts["meta"]["total"] == 1
