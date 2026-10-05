"""三个业务身份之间的边界回归。

本轮修复的两处「前端隐藏代替服务端权限」问题：

1. ``POST /households/join`` 以前只要求「已登录」，任何身份都能申请加入家庭；
   加入家庭是家庭成员这个身份的动作，咨询人员与经营者都应当被拒绝。
2. ``ConsultationService.close`` 以前写成 ``actor.has_role(CONSULTANT) or case.merchant_id``，
   而 ``case.merchant_id`` 永远是非空字符串 —— 等于任何登录用户都能关闭别人的咨询。
   API 层虽然已经预校验，Service 层仍然必须正确。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.errors import Forbidden
from app.services.consultation_service import ConsultationService
from tests import fixtures_api as api_fx
from tests.conftest import login, register


@pytest.fixture
def household_code(merchant_client: TestClient) -> str:
    response = merchant_client.post("/api/v1/households", json={"name": "王家小院"})
    assert response.status_code == 201, response.text
    return response.json()["invite_code"]


class TestHouseholdJoinRequiresFamilyMember:
    def test_consultant_cannot_join(self, consultant_client: TestClient, household_code: str):
        response = consultant_client.post(
            "/api/v1/households/join", json={"invite_code": household_code}
        )
        assert response.status_code == 403, response.text
        assert response.json()["code"] == "FAMILY_MEMBER_ONLY"

    def test_merchant_cannot_join_without_family_member_role(
        self, second_merchant_client: TestClient, household_code: str
    ):
        """另一个经营者（只有 merchant 身份）也不能用邀请码加入别人的家庭。"""
        response = second_merchant_client.post(
            "/api/v1/households/join", json={"invite_code": household_code}
        )
        assert response.status_code == 403, response.text
        assert response.json()["code"] == "FAMILY_MEMBER_ONLY"

    def test_family_member_still_can_join(self, app, merchant_client: TestClient, household_code: str):
        """正向对照：家庭成员照常可以申请。"""
        with TestClient(app) as member:
            member.headers.update({"X-Requested-With": "XMLHttpRequest"})
            assert (
                register(member, username="family_ok", roles=["family_member"]).status_code == 201
            )
            assert login(member, username="family_ok").status_code == 200
            response = member.post("/api/v1/households/join", json={"invite_code": household_code})
            assert response.status_code == 200, response.text
            assert response.json()["status"] == "pending"

    def test_anonymous_is_rejected(self, anon_client: TestClient, household_code: str):
        response = anon_client.post(
            "/api/v1/households/join", json={"invite_code": household_code}
        )
        # 匿名请求先被 CSRF 中间件挡下（403），带上 CSRF 头则是未登录（401）
        assert response.status_code in (401, 403), response.text


class TestConsultationClosePermission:
    """Service 层：只有咨询人员或该咨询所属经营者本人能完成咨询。"""

    def _case(self, merchant_client: TestClient) -> dict:
        api_fx.setup_merchant(merchant_client)
        events = merchant_client.get("/api/v1/cash-events").json()["items"]
        response = merchant_client.post(
            "/api/v1/consultations",
            json={
                "cash_event_id": events[0]["id"],
                "question_type": "settlement_time",
                "question": "这笔结算款预计什么时候到账？",
            },
        )
        assert response.status_code == 201, response.text
        case = response.json()
        merchant_client.post(f"/api/v1/consultations/{case['id']}/submit")
        return case

    def _case_row(self, db_session, case_id: str):
        from app.models.consultation import ConsultationCase

        row = db_session.get(ConsultationCase, case_id)
        assert row is not None
        return row

    def _owner_of(self, db_session, case_row):
        """按咨询所属经营主体反查账号：不能假设客户端登录的是哪个用户名。"""
        from app.models.merchant import MerchantProfile
        from app.models.user import User

        profile = db_session.get(MerchantProfile, case_row.merchant_id)
        assert profile is not None
        owner = db_session.get(User, profile.user_id)
        assert owner is not None
        return owner

    def test_other_merchant_cannot_close(self, merchant_client, second_merchant_client, db_session):
        case = self._case(merchant_client)
        row = self._case_row(db_session, case["id"])
        # 用「乙商户」的账号去关闭「甲商户」的咨询：必须被拒绝
        from app.models.user import User

        intruder = db_session.query(User).filter(User.username == "merchant_b").one()
        with pytest.raises(Forbidden):
            ConsultationService(db_session).close(row, intruder, "我来关掉")

    def test_owner_merchant_can_close(self, merchant_client, db_session):
        case = self._case(merchant_client)
        row = self._case_row(db_session, case["id"])
        owner = self._owner_of(db_session, row)
        # 先由咨询人员核实，经营者才有可完成的咨询
        from app.services.consultation_service import CASE_VERIFIED

        row.status = CASE_VERIFIED
        db_session.commit()
        closed = ConsultationService(db_session).close(row, owner, "已处理")
        assert closed.status == "closed"

    def test_consultant_can_close(self, merchant_client, consultant_client, db_session):
        case = self._case(merchant_client)
        row = self._case_row(db_session, case["id"])
        from app.models.user import User
        from app.services.consultation_service import CASE_VERIFIED

        row.status = CASE_VERIFIED
        db_session.commit()
        consultant = db_session.query(User).filter(User.username == "consultant_a").one()
        closed = ConsultationService(db_session).close(row, consultant, "已核实完成")
        assert closed.status == "closed"
