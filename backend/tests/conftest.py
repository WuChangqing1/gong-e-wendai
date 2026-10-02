"""pytest 全局装置。

* 在导入应用之前把数据库/上传目录指向临时路径
* 提供 ``client``（匿名）与 ``auth_client``（已登录商户）两个 HTTP 客户端
* 每个测试用例使用干净的数据库
"""

from __future__ import annotations

from tests import conftest_env  # noqa: F401  # 必须最先导入以设置环境变量

import pytest
from fastapi.testclient import TestClient

from app.core import database as db_module
from app.core.database import SessionLocal
from app.models import Base  # noqa: F401  （触发全部模型注册）
from app.core.security import CSRF_HEADER, CSRF_HEADER_VALUE

DEFAULT_PASSWORD = "Wendai@2025"


@pytest.fixture(scope="session", autouse=True)
def _prepare_database():
    db_module.settings.ensure_runtime_dirs()
    Base.metadata.drop_all(bind=db_module.engine)
    Base.metadata.create_all(bind=db_module.engine)
    yield
    Base.metadata.drop_all(bind=db_module.engine)


@pytest.fixture(autouse=True)
def _clean_tables():
    """每个用例前清空所有业务表。"""
    yield
    with db_module.engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            connection.execute(table.delete())


@pytest.fixture
def db_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def app():
    from app.main import create_app

    return create_app()


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        test_client.headers.update({CSRF_HEADER: CSRF_HEADER_VALUE})
        yield test_client


@pytest.fixture
def anon_client(app):
    """不带 CSRF 头的客户端，用于验证 CSRF 防护。"""
    with TestClient(app) as test_client:
        yield test_client


def register(
    client: TestClient,
    *,
    username: str,
    password: str = DEFAULT_PASSWORD,
    display_name: str = "测试用户",
    roles: list[str] | None = None,
    business_name: str | None = None,
):
    return client.post(
        "/api/v1/auth/register",
        json={
            "username": username,
            "password": password,
            "display_name": display_name,
            "roles": roles or ["merchant"],
            "business_name": business_name,
        },
    )


def login(client: TestClient, *, username: str, password: str = DEFAULT_PASSWORD):
    return client.post("/api/v1/auth/login", json={"username": username, "password": password})


@pytest.fixture
def merchant_client(client):
    """已注册并登录的商户客户端。"""
    response = register(client, username="merchant_a", business_name="甲商户")
    assert response.status_code == 201, response.text
    login_response = login(client, username="merchant_a")
    assert login_response.status_code == 200, login_response.text
    return client


@pytest.fixture
def second_merchant_client(app):
    """另一个独立商户客户端（用于越权测试）。"""
    with TestClient(app) as other:
        other.headers.update({CSRF_HEADER: CSRF_HEADER_VALUE})
        response = register(other, username="merchant_b", business_name="乙商户")
        assert response.status_code == 201, response.text
        assert login(other, username="merchant_b").status_code == 200
        yield other
