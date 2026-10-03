"""权限与隐私加固测试：家庭分享白名单、咨询白名单、角色自助注册限制。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests import fixtures_api as api_fx
from tests.conftest import login, register


def _seed_opening_balance(client: TestClient) -> None:
    """给已登录的商户登记期初资金（不重新注册、不重新登录）。"""
    base = api_fx.anchor()
    snapshot = client.post(
        "/api/v1/account/snapshots",
        json={
            "opening_balance_cents": api_fx.MAIN_OPENING_BALANCE_CENTS,
            "pending_settlement_cents": 0,
            "snapshot_at": base.isoformat(),
        },
    )
    assert snapshot.status_code == 201, snapshot.text


# ---------------------------------------------------------------------------
# 公开注册角色收紧
# ---------------------------------------------------------------------------
class TestSelfRegistrationRoles:
    def test_consultant_cannot_self_register(self, client: TestClient):
        response = client.post(
            "/api/v1/auth/register",
            json={
                "username": "self_consult",
                "password": "Wendai2025",
                "display_name": "自助咨询",
                "roles": ["consultant"],
            },
        )
        assert response.status_code == 422
        assert "self_consult" not in response.text

    def test_admin_role_no_longer_exists(self, client: TestClient):
        """V3：本系统不存在 admin 业务身份，注册 admin 一律被拒。"""
        response = client.post(
            "/api/v1/auth/register",
            json={
                "username": "self_admin",
                "password": "Wendai2025",
                "display_name": "自助管理",
                "roles": ["admin"],
            },
        )
        assert response.status_code == 422

    def test_mixed_role_list_is_rejected(self, client: TestClient):
        """即使用户名合法，只要含咨询人员角色就整体拒绝。"""
        response = client.post(
            "/api/v1/auth/register",
            json={
                "username": "mixed_role",
                "password": "Wendai2025",
                "display_name": "混合角色",
                "roles": ["merchant", "consultant"],
            },
        )
        assert response.status_code == 422

    def test_merchant_and_family_member_can_register(self, client: TestClient):
        for index, role in enumerate(("merchant", "family_member")):
            response = client.post(
                "/api/v1/auth/register",
                json={
                    "username": f"self_ok_{index}",
                    "password": "Wendai2025",
                    "display_name": "正常注册",
                    "roles": [role],
                    "business_name": "小店" if role == "merchant" else None,
                },
            )
            assert response.status_code == 201, response.text
            assert role in response.json()["user"]["roles"]
            client.cookies.clear()


# ---------------------------------------------------------------------------
# 家庭分享白名单：服务端过滤，不是前端隐藏
# ---------------------------------------------------------------------------
@pytest.fixture
def household_fixture(merchant_client: TestClient):
    base, event_ids = api_fx.setup_merchant(merchant_client)
    created = merchant_client.post("/api/v1/households", json={"name": "测试家庭"})
    assert created.status_code == 201, created.text
    household = created.json()
    invite = merchant_client.post("/api/v1/households/invite-code/rotate")
    assert invite.status_code == 200, invite.text
    code = invite.json()["invite_code"]

    from tests.conftest import register

    member_client = TestClient(merchant_client.app)
    member_client.headers.update({"X-Requested-With": "XMLHttpRequest"})
    register(member_client, username="family_share", roles=["family_member"])
    joined = member_client.post("/api/v1/households/join", json={"invite_code": code})
    assert joined.status_code in (200, 201), joined.text
    membership_id = joined.json()["membership_id"]
    approved = merchant_client.post(
        f"/api/v1/households/members/{membership_id}/approve"
    )
    assert approved.status_code in (200, 201), approved.text

    return merchant_client, member_client, household, base, event_ids


class TestHouseholdShareWhitelist:
    def _analysis_id(self, client: TestClient) -> str:
        return client.post("/api/v1/analysis/run", json={"mode": "current_plan"}).json()["id"]

    def test_unselected_key_payments_absent_from_backend_json(self, household_fixture):
        """不勾「关键付款」时，接收端接口里根本不能出现关键付款字段。"""
        merchant, member, household, _, event_ids = household_fixture
        analysis_id = self._analysis_id(merchant)

        card = merchant.post(
            "/api/v1/household-cards",
            json={
                "card_type": "decision",
                "shared_fields": ["max_withdrawable", "risk_summary", "limiting_point"],
                "analysis_result_id": analysis_id,
                # 刻意传入事项 id：它绝不能把未共享的事项字段带进 payload
                "cash_event_id": event_ids["API-REFUND-0001"],
            },
        )
        assert card.status_code == 201, card.text
        payload = card.json()["payload"]

        assert "max_withdrawable_cents" in payload
        assert "risk_summary" in payload
        for forbidden in (
            "key_payments",
            "event_title",
            "event_amount_cents",
            "event_scheduled_at",
            "event_version",
        ):
            assert forbidden not in payload, forbidden

        # 接收端（家庭成员）读到的持久化 payload 必须同样干净
        received = member.get(f"/api/v1/household-cards/{card.json()['id']}")
        assert received.status_code == 200, received.text
        body = received.json()
        for forbidden in (
            "key_payments",
            "event_title",
            "event_amount_cents",
            "event_scheduled_at",
            "event_version",
        ):
            assert forbidden not in body["payload"], forbidden
        assert "关键付款" not in received.text

    def test_selected_key_payments_are_included(self, household_fixture):
        merchant, _, household, _, event_ids = household_fixture
        analysis_id = self._analysis_id(merchant)
        card = merchant.post(
            "/api/v1/household-cards",
            json={
                "card_type": "decision",
                "shared_fields": ["max_withdrawable", "key_payments"],
                "analysis_result_id": analysis_id,
            },
        )
        assert card.status_code == 201, card.text
        payload = card.json()["payload"]
        assert "key_payments" in payload
        assert payload["key_payments"]
        assert event_ids  # 仅用于确认算例已建立

    def test_revision_card_without_shared_event_fields_hides_title(self, household_fixture):
        merchant, _, household, _, event_ids = household_fixture
        card = merchant.post(
            "/api/v1/household-cards",
            json={
                "card_type": "revision",
                "shared_fields": ["risk_summary"],
                "cash_event_id": event_ids["API-REFUND-0001"],
            },
        )
        assert card.status_code == 201, card.text
        body = card.json()
        assert body["title"] == "事项变更通知"
        assert "已确认退款" not in body["title"]
        assert "event_title" not in body["payload"]

    def test_revision_summary_requires_explicit_selection(self, household_fixture):
        merchant, _, household, _, event_ids = household_fixture
        card = merchant.post(
            "/api/v1/household-cards",
            json={
                "card_type": "revision",
                "shared_fields": ["revision_summary"],
                "cash_event_id": event_ids["API-REFUND-0001"],
            },
        )
        assert card.status_code == 201, card.text
        payload = card.json()["payload"]
        assert payload["event_title"] == "已确认退款"
        assert payload["event_amount_cents"] == 60_000
        assert payload["event_version"] == 1

    def test_opening_balance_is_never_shared(self, household_fixture):
        merchant, member, household, _, _ = household_fixture
        analysis_id = self._analysis_id(merchant)
        card = merchant.post(
            "/api/v1/household-cards",
            json={
                "card_type": "decision",
                # 即使显式请求，白名单之外的字段也必须被丢弃
                "shared_fields": ["max_withdrawable", "opening_balance", "all_transactions"],
                "analysis_result_id": analysis_id,
            },
        )
        assert card.status_code == 201, card.text
        assert "opening_balance_cents" not in card.json()["payload"]
        assert card.json()["shared_fields"] == ["max_withdrawable"]
        received = member.get(f"/api/v1/household-cards/{card.json()['id']}").json()
        assert "opening_balance_cents" not in received["payload"]

    def test_other_merchant_cannot_read_card(self, household_fixture, second_merchant_client):
        merchant, _, household, _, _ = household_fixture
        analysis_id = self._analysis_id(merchant)
        card = merchant.post(
            "/api/v1/household-cards",
            json={
                "card_type": "decision",
                "shared_fields": ["max_withdrawable"],
                "analysis_result_id": analysis_id,
            },
        ).json()
        assert second_merchant_client.get(f"/api/v1/household-cards/{card['id']}").status_code == 404


# ---------------------------------------------------------------------------
# 咨询白名单：最小必要字段
# ---------------------------------------------------------------------------
class TestConsultationWhitelist:
    def test_consultation_never_carries_balance_or_reserve(self, merchant_client: TestClient):
        base, event_ids = api_fx.setup_merchant(merchant_client)
        created = merchant_client.post(
            "/api/v1/consultations",
            json={
                "cash_event_id": event_ids["API-SETTLE-0001"],
                "question_type": "settlement_time",
                "question": "这笔结算款预计什么时候能到账？",
                "status": "submitted",
            },
        )
        assert created.status_code == 201, created.text
        shared = created.json()["shared_fields"]
        for forbidden in (
            "opening_balance_cents",
            "buffer_cents",
            "max_withdrawable_cents",
            "full_balance_curve",
            "household_info",
            "household_comments",
            "credit_score",
        ):
            assert forbidden not in shared, forbidden
        assert shared["amount_cents"] == 2000_00
        assert shared["event_title"] == "结算款"
        assert base  # 算例已建立

    def test_consultant_cannot_read_merchant_financial_data(
        self, merchant_client: TestClient, consultant_client: TestClient
    ):
        """咨询人员只能看到提交给咨询中心的事项。"""
        base, event_ids = api_fx.setup_merchant(merchant_client)
        merchant_client.post(
            "/api/v1/consultations",
            json={
                "cash_event_id": event_ids["API-SETTLE-0001"],
                "question_type": "settlement_time",
                "question": "预计什么时候到账？",
                "status": "submitted",
            },
        )
        assert consultant_client.get("/api/v1/consultations/queue").status_code == 200
        # 经营数据接口一律拒绝
        for path in (
            "/api/v1/cash-events",
            "/api/v1/analysis/today",
            "/api/v1/enhancements/overview",
            "/api/v1/history/daily",
            "/api/v1/settlement-records",
            "/api/v1/account/overview",
            "/api/v1/households/members",
        ):
            response = consultant_client.get(path)
            assert response.status_code == 403, (path, response.status_code)

        # 家庭相关接口按「本人可见」原则返回空，绝不能看到别人家庭的卡片
        mine = consultant_client.get("/api/v1/households/current")
        assert mine.status_code == 200
        assert mine.json() is None
        cards = consultant_client.get("/api/v1/household-cards")
        assert cards.status_code == 200
        assert cards.json() == []
        assert base

    def test_consultant_cannot_read_merchant_analysis_payload(
        self, merchant_client: TestClient, consultant_client: TestClient
    ):
        api_fx.setup_merchant(merchant_client)
        merchant_client.post("/api/v1/analysis/run", json={"mode": "current_plan"})
        assert consultant_client.get("/api/v1/analysis/history").status_code == 403
        assert consultant_client.get("/api/v1/analysis/stale").status_code == 403


# ---------------------------------------------------------------------------
# Admin 产品模块已被移除（V3）
# ---------------------------------------------------------------------------
class TestAdminModuleRemoved:
    """V3 要求：不存在管理员等级，也不存在 /admin 业务 API。

    这些路径必须表现为「接口不存在」（404），而不是「无权限」（403）——
    后者意味着后台仍然存在，只是被拦住了。
    """

    ADMIN_PATHS = (
        "/api/v1/admin/overview",
        "/api/v1/admin/runtime",
        "/api/v1/admin/users",
        "/api/v1/admin/audit-logs",
    )

    def test_merchant_sees_admin_api_as_not_found(self, merchant_client: TestClient):
        api_fx.setup_merchant(merchant_client)
        for path in self.ADMIN_PATHS:
            assert merchant_client.get(path).status_code == 404, path

    def test_consultant_sees_admin_api_as_not_found(self, consultant_client: TestClient):
        for path in self.ADMIN_PATHS:
            assert consultant_client.get(path).status_code == 404, path

    def test_anonymous_sees_admin_api_as_not_found(self, client: TestClient):
        for path in self.ADMIN_PATHS:
            assert client.get(path).status_code == 404, path

    def test_admin_path_is_absent_from_openapi(self, client: TestClient):
        schema = client.get("/api/openapi.json").json()
        assert not [path for path in schema["paths"] if path.startswith("/api/v1/admin")]

    def test_role_registry_has_no_admin(self, client: TestClient):
        from app.models.user import ALL_ROLES

        assert "admin" not in ALL_ROLES
        assert set(ALL_ROLES) == {"merchant", "family_member", "consultant"}


# ---------------------------------------------------------------------------
# 三种业务身份 × 跨主体隔离（V3 第 105 / 126 节）
# ---------------------------------------------------------------------------
class TestCrossSubjectIsolation:
    """三种身份一视同仁，但数据边界必须由**后端**强制。

    这里验证的是「接口层面拿不到别人的数据」，而不是「前端没显示按钮」。
    """

    def test_merchant_cannot_read_other_merchant_data(
        self, merchant_client: TestClient, second_merchant_client: TestClient
    ):
        # merchant_client 已经是注册并登录的甲商户（conftest 的 merchant_a）
        _seed_opening_balance(merchant_client)
        for payload in api_fx.event_payloads():
            created = merchant_client.post("/api/v1/cash-events", json=payload)
            assert created.status_code == 201, created.text
        mine = merchant_client.get("/api/v1/cash-events").json()
        assert mine["meta"]["total"] > 0
        first_id = mine["items"][0]["id"]

        # 乙商户看自己的数据：应为空，绝不能出现甲商户的事项
        other_list = second_merchant_client.get("/api/v1/cash-events").json()
        assert other_list["meta"]["total"] == 0, other_list
        assert other_list["items"] == []

        # 乙商户按 id 直接读取甲商户的事项：必须 404（不是 403，避免泄露「存在」）
        assert second_merchant_client.get(f"/api/v1/cash-events/{first_id}").status_code == 404
        assert (
            second_merchant_client.get(f"/api/v1/cash-events/{first_id}/revisions").status_code
            == 404
        )
        assert (
            second_merchant_client.get(f"/api/v1/cash-events/{first_id}/source").status_code == 404
        )

    def test_family_member_cannot_read_merchant_endpoints(
        self, client: TestClient, merchant_client: TestClient
    ):
        _seed_opening_balance(merchant_client)
        client.cookies.clear()
        register(client, username="family_only_v3", roles=["family_member"])
        client.cookies.clear()
        assert login(client, username="family_only_v3").status_code == 200

        for path in (
            "/api/v1/cash-events",
            "/api/v1/account/overview",
            "/api/v1/analysis/today",
            "/api/v1/enhancements/overview",
            "/api/v1/history/daily",
            "/api/v1/settlement-records",
            "/api/v1/consultations",
        ):
            assert client.get(path).status_code == 403, path

    def test_family_member_only_sees_shared_fields(self, client: TestClient):
        """家庭成员只能读经营者持久化后的白名单字段，不能靠 cash_event_id 补全。"""
        client.cookies.clear()
        register(client, username="family_share_v3", roles=["family_member"])
        client.cookies.clear()
        assert login(client, username="family_share_v3").status_code == 200

        # 没有任何分享时，卡片列表为空；不会因为知道 id 就能读到内容
        assert client.get("/api/v1/household-cards").json() == []

    def test_consultant_cannot_read_merchant_or_household_private(
        self, consultant_client: TestClient, merchant_client: TestClient
    ):
        _seed_opening_balance(merchant_client)
        for path in (
            "/api/v1/cash-events",
            "/api/v1/account/overview",
            "/api/v1/analysis/today",
            "/api/v1/enhancements/overview",
            "/api/v1/history/daily",
            "/api/v1/merchant/profile",
        ):
            assert consultant_client.get(path).status_code == 403, path

        # 咨询人员只能看到咨询队列，且队列里没有商户的经营金额汇总
        queue = consultant_client.get("/api/v1/consultations/queue")
        assert queue.status_code == 200
        for item in queue.json()["items"]:
            for forbidden in (
                "opening_balance_cents",
                "buffer_cents",
                "max_withdrawable_cents",
                "payment_gap_cents",
                "buffer_gap_cents",
            ):
                assert forbidden not in item, (forbidden, item)

    def test_no_role_can_reach_ops_or_management_api(
        self,
        client: TestClient,
        merchant_client: TestClient,
        consultant_client: TestClient,
    ):
        """V3：系统运维与平台用户管理不属于本模块，任何身份都拿不到。"""
        for path in (
            "/api/v1/admin/overview",
            "/api/v1/admin/users",
            "/api/v1/admin/runtime",
            "/api/v1/admin/audit-logs",
        ):
            assert client.get(path).status_code == 404, path
            assert merchant_client.get(path).status_code == 404, path
            assert consultant_client.get(path).status_code == 404, path
