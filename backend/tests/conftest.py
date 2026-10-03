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


def provision_user(
    *,
    username: str,
    roles: list[str],
    display_name: str = "内部账户",
    password: str = DEFAULT_PASSWORD,
) -> None:
    """直接写入一个指定角色的账户。

    公开注册只允许 ``merchant`` 与 ``family_member``；咨询人员属于上层系统的
    身份，由安全脚本 ``scripts/provision_consultant.py`` 开通。测试要模拟
    「上层身份已经开通好这个账户」，因此这里直接在数据库层创建。
    """
    from app.core.database import SessionLocal
    from app.core.security import hash_password as _hash
    from app.models.user import User, UserRole

    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.username == username).one_or_none()
        if existing is not None:
            return
        user = User(
            username=username,
            display_name=display_name[:64],
            password_hash=_hash(password),
            status="active",
        )
        user.roles = [UserRole(role=role) for role in roles]
        db.add(user)
        db.commit()
    finally:
        db.close()


@pytest.fixture
def consultant_client(app):
    """已登录的咨询人员客户端。

    咨询人员不能自助注册，因此这里按「上层身份已开通」的方式创建账户。
    """
    provision_user(
        username="consultant_a", roles=["consultant"], display_name="咨询小李"
    )
    with TestClient(app) as client:
        client.headers.update({CSRF_HEADER: CSRF_HEADER_VALUE})
        assert login(client, username="consultant_a").status_code == 200
        yield client
