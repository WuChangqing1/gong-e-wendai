"""认证、账户与 CSRF 安全测试。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.security import CSRF_HEADER, CSRF_HEADER_VALUE
from tests.conftest import DEFAULT_PASSWORD, login, provision_user, register


class TestRegister:
    def test_register_merchant_creates_profile(self, client: TestClient):
        response = register(client, username="shop_owner", business_name="王记小吃")
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["user"]["roles"] == ["merchant"]
        assert body["access_token"]

        me = client.get("/api/v1/me")
        assert me.status_code == 200
        assert me.json()["merchant"]["business_name"] == "王记小吃"
        assert "event:write" in me.json()["permissions"]

    def test_register_returns_http_only_cookies(self, client: TestClient):
        response = register(client, username="cookie_user")
        assert response.status_code == 201
        cookies = response.headers.get_list("set-cookie")
        joined = " ".join(cookies).lower()
        assert "httponly" in joined
        assert "samesite=lax" in joined
        assert "gew_access" in joined
        assert "gew_refresh" in joined

    def test_register_rejects_duplicate_username(self, client: TestClient):
        assert register(client, username="dup_user").status_code == 201
        second = register(client, username="dup_user")
        assert second.status_code == 409
        assert second.json()["code"] == "USERNAME_TAKEN"

    @pytest.mark.parametrize(
        "password",
        ["short1", "12345678", "abcdefgh"],
    )
    def test_register_rejects_weak_password(self, client: TestClient, password: str):
        response = register(client, username="weak_user", password=password)
        assert response.status_code == 422
        assert response.json()["code"] == "VALIDATION_FAILED"

    def test_register_rejects_unknown_role(self, client: TestClient):
        response = register(client, username="role_user", roles=["superuser"])
        assert response.status_code == 422

    def test_register_rejects_removed_admin_role(self, client: TestClient):
        """V3：admin 不再是合法业务身份，注册直接被拒。"""
        response = register(client, username="admin_user", roles=["admin"])
        assert response.status_code == 422
        assert "不支持的业务角色" in response.text

    def test_register_multi_role(self, client: TestClient):
        response = register(
            client, username="multi_role", roles=["merchant", "family_member"]
        )
        assert response.status_code == 201
        assert response.json()["user"]["roles"] == ["family_member", "merchant"]


class TestLogin:
    def test_login_success(self, client: TestClient):
        register(client, username="login_user")
        client.cookies.clear()
        response = login(client, username="login_user")
        assert response.status_code == 200
        assert response.json()["user"]["username"] == "login_user"

    def test_login_wrong_password(self, client: TestClient):
        register(client, username="pw_user")
        client.cookies.clear()
        response = login(client, username="pw_user", password="WrongPass123")
        assert response.status_code == 401
        assert response.json()["code"] == "UNAUTHENTICATED"

    def test_login_unknown_user(self, client: TestClient):
        response = login(client, username="ghost_user")
        assert response.status_code == 401

    def test_login_is_case_insensitive_username(self, client: TestClient):
        register(client, username="CaseUser")
        client.cookies.clear()
        response = login(client, username="caseuser")
        assert response.status_code == 200

    def test_password_is_hashed_in_database(self, client: TestClient, db_session):
        register(client, username="hash_user", password="Wendai@2025")
        from app.models.user import User

        user = db_session.query(User).filter_by(username="hash_user").one()
        assert user.password_hash != "Wendai@2025"
        assert user.password_hash.startswith("$argon2")


class TestSession:
    def test_me_requires_authentication(self, client: TestClient):
        response = client.get("/api/v1/me")
        assert response.status_code == 401
        assert response.json()["code"] == "UNAUTHENTICATED"

    def test_refresh_rotates_token(self, client: TestClient):
        register(client, username="refresh_user")
        first = client.post("/api/v1/auth/refresh")
        assert first.status_code == 200
        second = client.post("/api/v1/auth/refresh")
        assert second.status_code == 200
        assert first.json()["access_token"] != second.json()["access_token"]

    def test_logout_invalidates_session(self, client: TestClient):
        register(client, username="logout_user")
        assert client.post("/api/v1/auth/logout").status_code == 200
        client.cookies.clear()
        assert client.get("/api/v1/me").status_code == 401

    def test_change_password_then_login_with_new(self, client: TestClient):
        register(client, username="chpw_user")
        response = client.post(
            "/api/v1/me/password",
            json={"current_password": DEFAULT_PASSWORD, "new_password": "NewPass2025"},
        )
        assert response.status_code == 200, response.text

        client.cookies.clear()
        assert login(client, username="chpw_user", password=DEFAULT_PASSWORD).status_code == 401
        assert login(client, username="chpw_user", password="NewPass2025").status_code == 200

    def test_change_password_requires_correct_current(self, client: TestClient):
        register(client, username="chpw2_user")
        response = client.post(
            "/api/v1/me/password",
            json={"current_password": "NopePass123", "new_password": "NewPass2025"},
        )
        assert response.status_code == 422
        assert response.json()["code"] == "INVALID_CURRENT_PASSWORD"

    def test_bearer_token_authentication_works(self, app):
        with TestClient(app) as api:
            api.headers.update({CSRF_HEADER: CSRF_HEADER_VALUE})
            token = register(api, username="bearer_user").json()["access_token"]
        with TestClient(app) as plain:
            response = plain.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
            assert response.status_code == 200
            assert response.json()["username"] == "bearer_user"


class TestCsrf:
    def test_write_without_csrf_header_is_rejected(self, anon_client: TestClient):
        response = anon_client.post(
            "/api/v1/auth/login", json={"username": "x", "password": "y"}
        )
        assert response.status_code == 403
        assert response.json()["code"] == "CSRF_HEADER_MISSING"

    def test_read_without_csrf_header_is_allowed(self, anon_client: TestClient):
        assert anon_client.get("/api/v1/health").status_code == 200


class TestOwnership:
    def test_merchant_cannot_access_other_merchant(self, merchant_client, second_merchant_client):
        mine = merchant_client.get("/api/v1/merchant/profile").json()
        theirs = second_merchant_client.get("/api/v1/merchant/profile").json()
        assert mine["id"] != theirs["id"]
        assert mine["business_name"] == "甲商户"
        assert theirs["business_name"] == "乙商户"

    def test_consultant_cannot_read_merchant_profile(self, client: TestClient):
        provision_user(username="consult_only", roles=["consultant"])
        client.cookies.clear()
        login(client, username="consult_only")
        response = client.get("/api/v1/merchant/profile")
        assert response.status_code == 403
        assert response.json()["code"] == "FORBIDDEN"

    def test_family_member_cannot_read_merchant_profile(self, client: TestClient):
        register(client, username="family_only", roles=["family_member"])
        client.cookies.clear()
        login(client, username="family_only")
        assert client.get("/api/v1/merchant/profile").status_code == 403
        assert client.get("/api/v1/account/overview").status_code == 403

    def test_removed_admin_role_cannot_register(self, client: TestClient):
        response = register(client, username="wannabe_admin", roles=["admin"])
        assert response.status_code == 422


class TestHealth:
    def test_health_ok(self, client: TestClient):
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["database"] == "ok"
        assert set(body) == {"status", "database", "ai_enabled", "version", "env"}

    def test_health_does_not_leak_secrets(self, client: TestClient):
        text = client.get("/api/v1/health").text.lower()
        for forbidden in ("secret", "api_key", "apikey", "password", ".db", "token"):
            assert forbidden not in text

    def test_unknown_api_path_returns_api_404(self, client: TestClient):
        response = client.get("/api/v1/does-not-exist")
        assert response.status_code == 404
        assert response.json()["code"] == "NOT_FOUND"
        assert "index.html" not in response.text
