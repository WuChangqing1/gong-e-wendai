"""收付款事项（现金事件）、来源追溯与版本系统测试。"""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from tests import fixtures_api as api_fx
from tests.conftest import provision_user

BASE_PAYLOAD = {
    "title": "门店租金",
    "direction": "outflow",
    "amount_cents": 300_00,
    "scheduled_at": "2025-10-03T02:00:00+00:00",
    "event_type": "rent",
}


class TestCreate:
    def test_create_generates_cash_key_and_version(self, merchant_client: TestClient):
        response = merchant_client.post("/api/v1/cash-events", json=BASE_PAYLOAD)
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["cash_key"].startswith("OUT-")
        assert body["current_version"] == 1
        assert body["state"] == "scheduled"
        assert body["source_type"] == "manual"
        assert body["confirmed"] is True

    def test_create_with_explicit_cash_key(self, merchant_client: TestClient):
        payload = {**BASE_PAYLOAD, "cash_key": "MY-KEY-001"}
        response = merchant_client.post("/api/v1/cash-events", json=payload)
        assert response.status_code == 201
        assert response.json()["cash_key"] == "MY-KEY-001"

    def test_duplicate_cash_key_rejected(self, merchant_client: TestClient):
        payload = {**BASE_PAYLOAD, "cash_key": "DUP-001"}
        assert merchant_client.post("/api/v1/cash-events", json=payload).status_code == 201
        second = merchant_client.post("/api/v1/cash-events", json=payload)
        assert second.status_code == 409
        assert second.json()["code"] == "DUPLICATE_CASH_KEY"
        assert second.json()["details"]["cash_key"] == "DUP-001"

    @pytest.mark.parametrize(
        "override",
        [
            {"amount_cents": -1},
            {"direction": "sideways"},
            {"state": "unknown_state"},
            {"event_type": "not_a_type"},
            {"title": "   "},
            {"scheduled_at": "not-a-date"},
        ],
    )
    def test_invalid_payload_rejected(self, merchant_client: TestClient, override):
        response = merchant_client.post("/api/v1/cash-events", json={**BASE_PAYLOAD, **override})
        assert response.status_code == 422
        assert response.json()["code"] == "VALIDATION_FAILED"

    def test_same_cash_key_allowed_for_other_merchant(
        self, merchant_client: TestClient, second_merchant_client: TestClient
    ):
        payload = {**BASE_PAYLOAD, "cash_key": "SHARED-KEY"}
        assert merchant_client.post("/api/v1/cash-events", json=payload).status_code == 201
        assert second_merchant_client.post("/api/v1/cash-events", json=payload).status_code == 201

    def test_create_requires_authentication(self, client: TestClient):
        client.cookies.clear()
        assert client.post("/api/v1/cash-events", json=BASE_PAYLOAD).status_code == 401


class TestList:
    def test_filter_by_direction_and_state(self, merchant_client: TestClient):
        api_fx.setup_merchant(merchant_client)
        inflows = merchant_client.get("/api/v1/cash-events", params={"direction": "inflow"})
        assert inflows.status_code == 200
        assert inflows.json()["meta"]["total"] == 1
        assert all(item["direction"] == "inflow" for item in inflows.json()["items"])

        outflows = merchant_client.get("/api/v1/cash-events", params={"direction": "outflow"})
        assert outflows.json()["meta"]["total"] == 3

        scheduled = merchant_client.get("/api/v1/cash-events", params={"state": "scheduled"})
        assert scheduled.json()["meta"]["total"] == 4

    def test_search_by_title(self, merchant_client: TestClient):
        api_fx.setup_merchant(merchant_client)
        response = merchant_client.get("/api/v1/cash-events", params={"search": "进货款"})
        assert response.json()["meta"]["total"] == 1
        assert response.json()["items"][0]["title"] == "进货款"

    def test_pagination(self, merchant_client: TestClient):
        api_fx.setup_merchant(merchant_client)
        response = merchant_client.get(
            "/api/v1/cash-events", params={"page": 2, "page_size": 2}
        )
        meta = response.json()["meta"]
        assert meta["total"] == 4
        assert meta["page"] == 2
        assert len(response.json()["items"]) == 2

    def test_stats(self, merchant_client: TestClient):
        api_fx.setup_merchant(merchant_client)
        stats = merchant_client.get("/api/v1/cash-events/stats").json()
        assert stats["total"] == 4
        assert stats["scheduled"] == 4
        assert stats["inflow_cents"] == 2000_00
        assert stats["outflow_cents"] == 3800_00


class TestUpdateAndRevision:
    def test_amount_change_creates_new_version(self, merchant_client: TestClient):
        created = merchant_client.post("/api/v1/cash-events", json=BASE_PAYLOAD).json()
        response = merchant_client.patch(
            f"/api/v1/cash-events/{created['id']}",
            json={"amount_cents": 350_00, "change_reason": "租金调整"},
        )
        assert response.status_code == 200
        assert response.json()["current_version"] == 2
        assert response.json()["amount_cents"] == 350_00

        revisions = merchant_client.get(
            f"/api/v1/cash-events/{created['id']}/revisions"
        ).json()
        assert len(revisions) == 2
        latest = revisions[0]
        assert latest["version"] == 2
        assert latest["changed_fields"] == ["amount_cents"]
        assert latest["material"] is True
        assert latest["change_reason"] == "租金调整"
        assert latest["changes"][0]["before_text"] == "¥300.00"
        assert latest["changes"][0]["after_text"] == "¥350.00"

    def test_scheduled_at_change_creates_new_version(self, merchant_client: TestClient):
        created = merchant_client.post("/api/v1/cash-events", json=BASE_PAYLOAD).json()
        new_time = (api_fx.anchor() + timedelta(days=4)).isoformat()
        response = merchant_client.patch(
            f"/api/v1/cash-events/{created['id']}", json={"scheduled_at": new_time}
        )
        assert response.json()["current_version"] == 2
        revisions = merchant_client.get(
            f"/api/v1/cash-events/{created['id']}/revisions"
        ).json()
        assert revisions[0]["material"] is True
        assert "scheduled_at" in revisions[0]["changed_fields"]

    def test_direction_change_creates_new_version(self, merchant_client: TestClient):
        created = merchant_client.post("/api/v1/cash-events", json=BASE_PAYLOAD).json()
        response = merchant_client.patch(
            f"/api/v1/cash-events/{created['id']}", json={"direction": "inflow"}
        )
        assert response.json()["current_version"] == 2
        assert response.json()["direction"] == "inflow"

    def test_title_only_change_is_not_material(self, merchant_client: TestClient):
        created = merchant_client.post("/api/v1/cash-events", json=BASE_PAYLOAD).json()
        response = merchant_client.patch(
            f"/api/v1/cash-events/{created['id']}", json={"title": "门店租金（新）"}
        )
        assert response.json()["current_version"] == 2
        revisions = merchant_client.get(
            f"/api/v1/cash-events/{created['id']}/revisions"
        ).json()
        assert revisions[0]["material"] is False
        assert revisions[0]["changed_fields"] == ["title"]

    def test_no_change_does_not_create_version(self, merchant_client: TestClient):
        created = merchant_client.post("/api/v1/cash-events", json=BASE_PAYLOAD).json()
        response = merchant_client.patch(
            f"/api/v1/cash-events/{created['id']}", json={"title": "门店租金"}
        )
        assert response.json()["current_version"] == 1

    def test_history_is_never_overwritten(self, merchant_client: TestClient):
        created = merchant_client.post("/api/v1/cash-events", json=BASE_PAYLOAD).json()
        for amount in (310_00, 320_00, 330_00):
            merchant_client.patch(
                f"/api/v1/cash-events/{created['id']}", json={"amount_cents": amount}
            )
        revisions = merchant_client.get(
            f"/api/v1/cash-events/{created['id']}/revisions"
        ).json()
        assert len(revisions) == 4
        assert [item["version"] for item in revisions] == [4, 3, 2, 1]
        assert revisions[0]["after"]["amount_cents"] == 330_00
        assert revisions[-1]["before"] is None

    def test_cash_key_can_be_corrected_and_is_traced(self, merchant_client: TestClient):
        """单据编号录错时必须能更正，并且同样留下版本记录。"""
        created = merchant_client.post("/api/v1/cash-events", json=BASE_PAYLOAD).json()
        response = merchant_client.patch(
            f"/api/v1/cash-events/{created['id']}",
            json={"cash_key": "API-RENT-0002", "change_reason": "单据编号录错"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["cash_key"] == "API-RENT-0002"
        assert response.json()["current_version"] == 2

        revisions = merchant_client.get(
            f"/api/v1/cash-events/{created['id']}/revisions"
        ).json()
        assert revisions[0]["changed_fields"] == ["cash_key"]
        # 编号只影响识别，不影响金额口径，因此不是「实质变更」
        assert revisions[0]["material"] is False
        assert revisions[0]["changes"][0]["label"] == "事项编号"

    def test_duplicate_cash_key_is_rejected(self, merchant_client: TestClient):
        api_fx.setup_merchant(merchant_client)
        items = merchant_client.get("/api/v1/cash-events").json()["items"]
        first, second = items[0], items[1]
        response = merchant_client.patch(
            f"/api/v1/cash-events/{second['id']}", json={"cash_key": first["cash_key"]}
        )
        assert response.status_code == 422
        assert response.json()["code"] == "DUPLICATE_CASH_KEY"

    def test_blank_cash_key_is_rejected(self, merchant_client: TestClient):
        created = merchant_client.post("/api/v1/cash-events", json=BASE_PAYLOAD).json()
        response = merchant_client.patch(
            f"/api/v1/cash-events/{created['id']}", json={"cash_key": "   "}
        )
        assert response.status_code == 422


class TestCancel:
    def test_cancel_sets_state_and_keeps_history(self, merchant_client: TestClient):
        created = merchant_client.post("/api/v1/cash-events", json=BASE_PAYLOAD).json()
        response = merchant_client.post(
            f"/api/v1/cash-events/{created['id']}/cancel", json={"reason": "重复录入"}
        )
        assert response.status_code == 200
        assert response.json()["state"] == "cancelled"
        assert response.json()["current_version"] == 2

        # 记录仍然存在
        detail = merchant_client.get(f"/api/v1/cash-events/{created['id']}")
        assert detail.status_code == 200

    def test_cancel_is_idempotent_guard(self, merchant_client: TestClient):
        created = merchant_client.post("/api/v1/cash-events", json=BASE_PAYLOAD).json()
        merchant_client.post(f"/api/v1/cash-events/{created['id']}/cancel")
        second = merchant_client.post(f"/api/v1/cash-events/{created['id']}/cancel")
        assert second.status_code == 422
        assert second.json()["code"] == "ALREADY_CANCELLED"

    def test_hard_delete_is_not_supported(self, merchant_client: TestClient):
        created = merchant_client.post("/api/v1/cash-events", json=BASE_PAYLOAD).json()
        response = merchant_client.delete(f"/api/v1/cash-events/{created['id']}")
        assert response.status_code == 200
        assert response.json()["code"] == "HARD_DELETE_NOT_SUPPORTED"
        assert merchant_client.get(f"/api/v1/cash-events/{created['id']}").status_code == 200


class TestSourceTracing:
    def test_source_drawer_data(self, merchant_client: TestClient):
        payload = {**BASE_PAYLOAD, "source_label": "租赁合同 2025-01"}
        created = merchant_client.post("/api/v1/cash-events", json=payload).json()
        detail = merchant_client.get(f"/api/v1/cash-events/{created['id']}/source").json()
        assert detail["source"]["source_type"] == "manual"
        assert detail["source"]["source_label"] == "租赁合同 2025-01"
        assert detail["source"]["created_at"]

    def test_result_to_event_to_source_chain(self, merchant_client: TestClient):
        """结果 -> 限制事件 -> CashEvent -> SourceRecord 完整可追踪。"""
        _, event_ids = api_fx.setup_merchant(merchant_client)
        analysis = merchant_client.get("/api/v1/analysis/today").json()
        limiting_id = analysis["limiting_event_id"]
        assert limiting_id == event_ids["API-REFUND-0001"]

        event = merchant_client.get(f"/api/v1/cash-events/{limiting_id}").json()
        assert event["id"] == limiting_id
        assert event["source"] is not None
        assert event["source"]["source_type"] == "manual"

        source = merchant_client.get(f"/api/v1/cash-events/{limiting_id}/source").json()
        assert source["source_record"] is not None
        assert source["source_record"]["source_type"] == "manual"


class TestOwnershipIsolation:
    def test_other_merchant_cannot_read_event(
        self, merchant_client: TestClient, second_merchant_client: TestClient
    ):
        created = merchant_client.post("/api/v1/cash-events", json=BASE_PAYLOAD).json()
        response = second_merchant_client.get(f"/api/v1/cash-events/{created['id']}")
        assert response.status_code == 404

    def test_other_merchant_cannot_modify_event(
        self, merchant_client: TestClient, second_merchant_client: TestClient
    ):
        created = merchant_client.post("/api/v1/cash-events", json=BASE_PAYLOAD).json()
        response = second_merchant_client.patch(
            f"/api/v1/cash-events/{created['id']}", json={"amount_cents": 1}
        )
        assert response.status_code == 404

    def test_other_merchant_cannot_cancel_event(
        self, merchant_client: TestClient, second_merchant_client: TestClient
    ):
        created = merchant_client.post("/api/v1/cash-events", json=BASE_PAYLOAD).json()
        response = second_merchant_client.post(f"/api/v1/cash-events/{created['id']}/cancel")
        assert response.status_code == 404

    def test_other_merchant_cannot_read_revisions(
        self, merchant_client: TestClient, second_merchant_client: TestClient
    ):
        created = merchant_client.post("/api/v1/cash-events", json=BASE_PAYLOAD).json()
        response = second_merchant_client.get(
            f"/api/v1/cash-events/{created['id']}/revisions"
        )
        assert response.status_code == 404

    def test_other_merchant_cannot_read_source(
        self, merchant_client: TestClient, second_merchant_client: TestClient
    ):
        created = merchant_client.post("/api/v1/cash-events", json=BASE_PAYLOAD).json()
        response = second_merchant_client.get(f"/api/v1/cash-events/{created['id']}/source")
        assert response.status_code == 404

    def test_other_merchant_list_is_empty(
        self, merchant_client: TestClient, second_merchant_client: TestClient
    ):
        merchant_client.post("/api/v1/cash-events", json=BASE_PAYLOAD)
        listing = second_merchant_client.get("/api/v1/cash-events").json()
        assert listing["meta"]["total"] == 0

    def test_family_member_cannot_access_events(self, client: TestClient):
        from tests.conftest import login, provision_user, register

        register(client, username="fam_only", roles=["family_member"])
        client.cookies.clear()
        login(client, username="fam_only")
        assert client.get("/api/v1/cash-events").status_code == 403

    def test_consultant_cannot_access_events(self, client: TestClient):
        from tests.conftest import login, register

        provision_user(username="con_only", roles=["consultant"])
        client.cookies.clear()
        login(client, username="con_only")
        assert client.get("/api/v1/cash-events").status_code == 403


class TestAudit:
    def test_create_update_cancel_are_audited(
        self, merchant_client: TestClient, db_session
    ):
        from app.models.consultation import AuditLog

        created = merchant_client.post("/api/v1/cash-events", json=BASE_PAYLOAD).json()
        merchant_client.patch(
            f"/api/v1/cash-events/{created['id']}", json={"amount_cents": 400_00}
        )
        merchant_client.post(f"/api/v1/cash-events/{created['id']}/cancel")

        actions = [
            row.action
            for row in db_session.query(AuditLog).order_by(AuditLog.created_at.asc()).all()
        ]
        assert "cash_event.created" in actions
        assert "cash_event.updated" in actions
        assert "cash_event.cancelled" in actions

    def test_audit_never_stores_password_or_token(self, merchant_client: TestClient, db_session):
        from app.models.consultation import AuditLog

        merchant_client.post("/api/v1/cash-events", json=BASE_PAYLOAD)
        for row in db_session.query(AuditLog).all():
            text = str(row.metadata_json)
            assert "password" not in text.lower()
            assert "token" not in text.lower()
