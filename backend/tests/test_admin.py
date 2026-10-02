"""管理员接口测试。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.security import hash_password
from tests.conftest import DEFAULT_PASSWORD, login, register
from tests import fixtures_api as api_fx


@pytest.fixture
def admin_client(app):
    """直接写入管理员账户（自助注册被禁止）。"""
    from app.core.database import SessionLocal
    from app.models.user import ROLE_ADMIN, User, UserRole

    db = SessionLocal()
    try:
        user = User(
            username="admin_root",
            display_name="系统管理员",
            password_hash=hash_password(DEFAULT_PASSWORD),
            status="active",
        )
        user.roles = [UserRole(role=ROLE_ADMIN)]
        db.add(user)
        db.commit()
    finally:
        db.close()

    with TestClient(app) as client:
        client.headers.update({"X-Requested-With": "XMLHttpRequest"})
        response = login(client, username="admin_root")
        assert response.status_code == 200, response.text
        yield client


class TestAdminOverview:
    def test_overview_counts(self, admin_client: TestClient, merchant_client: TestClient):
        api_fx.setup_merchant(merchant_client)
        body = admin_client.get("/api/v1/admin/overview").json()
        assert body["users"] >= 2
        assert body["merchants"] >= 1
        assert body["cash_events"] == 3
        assert body["ai_enabled"] is False
        assert body["version"]

    def test_runtime_status(self, admin_client: TestClient):
        body = admin_client.get("/api/v1/admin/runtime").json()
        assert body["database_ok"] is True
        assert body["wal_enabled"] is True
        assert body["database_size_bytes"] > 0
        assert isinstance(body["stale_results"], int)
        assert "database_path" not in body

    def test_runtime_does_not_leak_paths_or_secrets(self, admin_client: TestClient):
        text = admin_client.get("/api/v1/admin/runtime").text.lower()
        for forbidden in ("jwt", "secret", "api_key", ".db", "sqlite:///"):
            assert forbidden not in text

    def test_audit_logs(self, admin_client: TestClient, merchant_client: TestClient):
        api_fx.setup_merchant(merchant_client)
        rows = admin_client.get("/api/v1/admin/audit-logs", params={"limit": 50}).json()
        actions = {item["action"] for item in rows}
        assert "auth.login" in actions
        assert "cash_event.created" in actions

    def test_audit_logs_have_no_secrets(self, admin_client: TestClient, merchant_client: TestClient):
        api_fx.setup_merchant(merchant_client)
        rows = admin_client.get("/api/v1/admin/audit-logs").json()
        text = str(rows).lower()
        assert "password" not in text
        assert "refresh_token" not in text
        assert "gew_refresh" not in text


class TestAdminUsers:
    def test_list_users(self, admin_client: TestClient, merchant_client: TestClient):
        body = admin_client.get("/api/v1/admin/users").json()
        usernames = {item["username"] for item in body["items"]}
        assert "admin_root" in usernames
        assert "merchant_a" in usernames

    def test_search_users(self, admin_client: TestClient, merchant_client: TestClient):
        body = admin_client.get("/api/v1/admin/users", params={"search": "merchant_a"}).json()
        assert body["meta"]["total"] == 1

    def test_disable_and_enable_user(self, admin_client: TestClient, app):
        with TestClient(app) as victim:
            victim.headers.update({"X-Requested-With": "XMLHttpRequest"})
            register(victim, username="victim_user", display_name="待停用")
            victim_id = victim.get("/api/v1/me").json()["id"]

            disabled = admin_client.post(
                f"/api/v1/admin/users/{victim_id}/status", json={"status": "disabled"}
            )
            assert disabled.status_code == 200

            # 被停用后无法再访问业务接口
            assert victim.get("/api/v1/me").status_code == 403

            enabled = admin_client.post(
                f"/api/v1/admin/users/{victim_id}/status", json={"status": "active"}
            )
            assert enabled.status_code == 200
            assert victim.get("/api/v1/me").status_code == 200

    def test_cannot_disable_self(self, admin_client: TestClient):
        me = admin_client.get("/api/v1/me").json()
        response = admin_client.post(
            f"/api/v1/admin/users/{me['id']}/status", json={"status": "disabled"}
        )
        assert response.status_code == 422

    def test_invalid_status_rejected(self, admin_client: TestClient, merchant_client: TestClient):
        me = merchant_client.get("/api/v1/me").json()
        response = admin_client.post(
            f"/api/v1/admin/users/{me['id']}/status", json={"status": "banana"}
        )
        assert response.status_code == 422

    def test_unknown_user(self, admin_client: TestClient):
        response = admin_client.post(
            "/api/v1/admin/users/does-not-exist/status", json={"status": "disabled"}
        )
        assert response.status_code == 404


class TestAdminPermissions:
    def test_merchant_cannot_access_admin(self, merchant_client: TestClient):
        assert merchant_client.get("/api/v1/admin/overview").status_code == 403
        assert merchant_client.get("/api/v1/admin/users").status_code == 403
        assert merchant_client.get("/api/v1/admin/runtime").status_code == 403
        assert merchant_client.get("/api/v1/admin/audit-logs").status_code == 403

    def test_consultant_cannot_access_admin(self, client: TestClient):
        register(client, username="con_admin", roles=["consultant"])
        client.cookies.clear()
        login(client, username="con_admin")
        assert client.get("/api/v1/admin/overview").status_code == 403

    def test_family_member_cannot_access_admin(self, client: TestClient):
        register(client, username="fam_admin", roles=["family_member"])
        client.cookies.clear()
        login(client, username="fam_admin")
        assert client.get("/api/v1/admin/users").status_code == 403

    def test_unauthenticated_blocked(self, client: TestClient):
        client.cookies.clear()
        assert client.get("/api/v1/admin/overview").status_code == 401

    def test_admin_cannot_read_merchant_business_data(self, admin_client: TestClient):
        """管理员不是经营主体，不能读取商户经营数据（避免越权）。"""
        assert admin_client.get("/api/v1/cash-events").status_code == 403
        assert admin_client.get("/api/v1/account/overview").status_code == 403
        assert admin_client.get("/api/v1/analysis/today").status_code == 403

    def test_self_service_admin_registration_disabled(self, client: TestClient):
        response = register(client, username="selfmade_admin", roles=["admin"])
        assert response.status_code == 422
