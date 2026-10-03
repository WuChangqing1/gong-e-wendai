"""AI 智能服务测试。

全部使用测试替身，不依赖任何真实外部 API。重点验证：
* 正常结构化输出
* 非法 JSON / 缺少字段 / HTTP 500 / 超时 / 无 API Key / AI_ENABLED=false
* 未知字段与与输入不符的金额被过滤
* AI 故障绝不影响现金流计算
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.core.config import settings
from app.core.errors import AIServiceError
from app.services.ai_service import (
    AIService,
    ExtractedEvent,
    GLMOpenAICompatibleClient,
    _amount_appears_in_text,
    _find_unknown_amounts,
    _load_json_object,
)
from app.utils.money import to_cents


class FakeClient:
    """可控的对话补全测试替身。"""

    def __init__(self, response: str | Exception = "", *, capture: list | None = None) -> None:
        self.response = response
        self.calls: list[dict] = []
        self._capture = capture

    def complete(
        self,
        *,
        system: str,
        user: str,
        temperature: float = 0.1,
        image: object | None = None,
    ) -> str:
        self.calls.append(
            {"system": system, "user": user, "temperature": temperature, "image": image}
        )
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


GOOD_EXTRACT = json.dumps(
    {
        "title": "商户结算款",
        "direction": "inflow",
        "amount_cents": 235860,
        "scheduled_at": "2025-10-03T09:00:00+08:00",
        "state": "scheduled",
        "source_label": "结算通知 8821",
        "confidence": {"amount_cents": 0.95, "scheduled_at": 0.8},
        "warnings": [],
    },
    ensure_ascii=False,
)


class TestExtractCashEvent:
    def test_normal_output(self):
        service = AIService(FakeClient(GOOD_EXTRACT))
        result = service.extract_cash_event("您尾号8821的商户结算款2358.60元预计10月3日完成结算。")
        assert result.event.title == "商户结算款"
        assert result.event.direction == "inflow"
        assert result.event.amount_cents == 235860
        assert result.event.scheduled_at is not None
        assert result.raw_text.startswith("您尾号8821")

    def test_json_in_markdown_block(self):
        wrapped = f"```json\n{GOOD_EXTRACT}\n```"
        service = AIService(FakeClient(wrapped))
        result = service.extract_cash_event("商户结算款2358.60元预计10月3日完成结算。")
        assert result.event.amount_cents == 235860

    def test_invalid_json_raises(self):
        service = AIService(FakeClient("这不是 JSON"))
        with pytest.raises(AIServiceError):
            service.extract_cash_event("商户结算款2358.60元预计10月3日到账。")

    def test_missing_fields_use_defaults(self):
        payload = json.dumps({"title": "结算款"})
        service = AIService(FakeClient(payload))
        result = service.extract_cash_event("结算款项说明文字")
        assert result.event.direction == "inflow"
        assert result.event.amount_cents is None
        assert result.event.scheduled_at is None
        assert result.event.state == "scheduled"
        assert any("时间" in item for item in result.event.warnings)

    def test_invalid_direction_rejected(self):
        payload = json.dumps({"title": "x", "direction": "sideways", "amount_cents": 100})
        service = AIService(FakeClient(payload))
        with pytest.raises(AIServiceError):
            service.extract_cash_event("方向不正确的内容")

    def test_timeout_maps_to_unavailable(self, monkeypatch):
        """真实客户端把 httpx 超时转换为统一的 AIServiceError。"""
        client = GLMOpenAICompatibleClient(
            base_url="https://api.example.com/v1",
            api_key="sk-x",
            model="m",
            timeout=0.01,
        )

        def boom(*args, **kwargs):  # noqa: ANN002, ANN003
            raise httpx.TimeoutException("timeout")

        monkeypatch.setattr(httpx.Client, "post", boom)
        with pytest.raises(AIServiceError) as excinfo:
            client.complete(system="s", user="u")
        assert "超时" in str(excinfo.value)

        service = AIService(client)
        with pytest.raises(AIServiceError):
            service.extract_cash_event("结算款 100 元")

    def test_http_500_maps_to_unavailable(self, monkeypatch):
        client = GLMOpenAICompatibleClient(
            base_url="https://api.example.com/v1",
            api_key="sk-x",
            model="m",
            timeout=5,
        )

        class FakeResponse:
            status_code = 500
            text = "internal error"

            def json(self):  # noqa: ANN201
                return {}

        monkeypatch.setattr(httpx.Client, "post", lambda *a, **k: FakeResponse())
        with pytest.raises(AIServiceError):
            client.complete(system="s", user="u")

    def test_malformed_payload_maps_to_unavailable(self, monkeypatch):
        client = GLMOpenAICompatibleClient(
            base_url="https://api.example.com/v1",
            api_key="sk-x",
            model="m",
            timeout=5,
        )

        class FakeResponse:
            status_code = 200

            def json(self):  # noqa: ANN201
                return {"unexpected": True}

        monkeypatch.setattr(httpx.Client, "post", lambda *a, **k: FakeResponse())
        with pytest.raises(AIServiceError):
            client.complete(system="s", user="u")

    def test_all_failure_modes_share_one_user_message(self, merchant_client):
        """未配置 / 超时 / 无 Key / 非法 JSON 对外都是同一句降级文案。"""
        response = merchant_client.post(
            "/api/v1/ai/extract-cash-event",
            json={"text": "结算款 2358.60 元预计 10 月 3 日到账"},
        )
        assert response.status_code == 503
        assert response.json()["code"] == "AI_UNAVAILABLE"
        assert response.json()["message"] == "智能服务暂时不可用，你仍可以手动完成当前操作"

        explain = merchant_client.post(
            "/api/v1/ai/explain-analysis", json={"max_withdrawable_cents": 120000}
        )
        assert explain.status_code == 503
        assert explain.json()["message"] == "智能服务暂时不可用，你仍可以手动完成当前操作"

        draft = merchant_client.post(
            "/api/v1/ai/draft-consultation",
            json={"question_type": "other", "question": "测试", "fields": {}},
        )
        assert draft.status_code == 503
        assert draft.json()["message"] == "智能服务暂时不可用，你仍可以手动完成当前操作"

    def test_unknown_amount_is_filtered(self):
        """AI 返回的金额在原文中找不到 -> 视为猜测并清空。"""
        payload = json.dumps(
            {
                "title": "结算款",
                "direction": "inflow",
                "amount_cents": 999999,
                "scheduled_at": "2025-10-03T09:00:00+08:00",
            },
            ensure_ascii=False,
        )
        service = AIService(FakeClient(payload))
        result = service.extract_cash_event("有一笔结算款即将到账，具体金额以后再确认。")
        assert result.event.amount_cents is None
        assert result.filtered_amounts
        assert any("金额" in item for item in result.event.warnings)

    def test_unknown_field_is_ignored(self):
        payload = json.dumps(
            {
                "title": "结算款",
                "direction": "inflow",
                "amount_cents": 10000,
                "scheduled_at": "2025-10-03T09:00:00+08:00",
                "credit_score": 780,
                "loan_advice": "建议申请贷款",
            },
            ensure_ascii=False,
        )
        service = AIService(FakeClient(payload))
        result = service.extract_cash_event("结算款 100.00 元预计 10 月 3 日到账。")
        dumped = result.event.model_dump()
        assert "credit_score" not in dumped
        assert "loan_advice" not in dumped

    def test_amount_in_yuan_is_converted(self):
        payload = json.dumps(
            {"title": "租金", "direction": "outflow", "amount_cents": 3000.5, "scheduled_at": "2025-10-05"},
            ensure_ascii=False,
        )
        service = AIService(FakeClient(payload))
        result = service.extract_cash_event("门店租金 3000.50 元")
        assert result.event.amount_cents == 300050

    def test_short_text_rejected(self):
        service = AIService(FakeClient(GOOD_EXTRACT))
        with pytest.raises(AIServiceError):
            service.extract_cash_event("短")


class TestNotConfigured:
    def test_no_api_key(self, monkeypatch):
        monkeypatch.setattr(settings, "ai_enabled", True)
        monkeypatch.setattr(settings, "ai_api_key", "")
        monkeypatch.setattr(settings, "ai_base_url", "")
        monkeypatch.setattr(settings, "ai_model", "")

        service = AIService()
        assert service.configured is False
        with pytest.raises(AIServiceError):
            service.extract_cash_event("结算款 100 元")

    def test_ai_disabled(self, monkeypatch):
        monkeypatch.setattr(settings, "ai_enabled", False)
        monkeypatch.setattr(settings, "ai_api_key", "sk-test")
        monkeypatch.setattr(settings, "ai_base_url", "https://example.invalid/v1")
        monkeypatch.setattr(settings, "ai_model", "test-model")

        service = AIService()
        assert service.configured is False
        status = service.status()
        assert status["enabled"] is False
        assert status["available"] is False
        with pytest.raises(AIServiceError):
            service.extract_cash_event("结算款 100 元")


class TestExplainAnalysis:
    PAYLOAD = {
        "max_withdrawable_cents": 120000,
        "opening_balance_cents": 60000,
        "buffer_cents": 60000,
        "status": "OK",
        "limiting_timestamp": "2025-10-03T03:00:00+00:00",
        "limiting_balance_cents": 180000,
        "limiting_event_title": "供应商货款",
        "limiting_reason": "最紧时点余额扣除留底后可提用 1200 元。",
        "payment_gap_cents": 0,
        "buffer_gap_cents": 0,
        "window_inflow_cents": 290000,
        "window_outflow_cents": 100000,
        "pending_inflows": [{"title": "平台结算款", "amount_text": "¥700.00"}],
    }

    def test_explanation_with_only_known_amounts(self):
        text = "今天可以提用 1200.00 元。最紧张的时刻出现在供应商货款这一笔，当时余额 1800.00 元，扣除留底 600.00 元后仍有空间。"
        service = AIService(FakeClient(text))
        result = service.explain_analysis(self.PAYLOAD)
        assert "1200.00" in result["explanation"]
        assert result["filtered_amounts"] == []
        assert "max_withdrawable_cents" in result["used_fields"]

    def test_unknown_amount_is_stripped(self):
        text = "今天可以提用 1200.00 元，另外还有 88888.00 元可以随意使用。"
        service = AIService(FakeClient(text))
        result = service.explain_analysis(self.PAYLOAD)
        assert "88888.00" not in result["explanation"]
        assert result["filtered_amounts"] == ["88888.00"]
        assert "以页面计算结果为准" in result["explanation"]

    def test_only_structured_fields_are_sent(self):
        fake = FakeClient("好的。")
        service = AIService(fake)
        service.explain_analysis({**self.PAYLOAD, "all_transactions": [{"x": 1}], "api_key": "sk-secret"})
        sent = fake.calls[0]["user"]
        assert "all_transactions" not in sent
        assert "sk-secret" not in sent

    def test_empty_payload_rejected(self):
        service = AIService(FakeClient("好的。"))
        with pytest.raises(AIServiceError):
            service.explain_analysis({})

    def test_blank_response_rejected(self):
        service = AIService(FakeClient("   "))
        with pytest.raises(AIServiceError):
            service.explain_analysis(self.PAYLOAD)


class TestDraftConsultation:
    def test_draft_uses_whitelist_only(self):
        fake = FakeClient("关于编号 ZX202510010001 的商户结算款 2358.60 元，想确认结算进度。")
        service = AIService(fake)
        result = service.draft_consultation(
            question_type="settlement_time",
            question="这笔钱什么时候到？",
            fields={
                "event_title": "商户结算款",
                "amount_cents": 235860,
                "scheduled_at": "2025-10-03T09:00:00+08:00",
                "opening_balance_cents": 999999,
                "household_comments": "家人说不要提用",
            },
        )
        sent = fake.calls[0]["user"]
        assert "opening_balance_cents" not in sent
        assert "household_comments" not in sent
        assert "event_title" in sent
        assert result["draft"]

    def test_draft_strips_unknown_amount(self):
        fake = FakeClient("这笔结算款 2358.60 元，另外还有 50000.00 元。")
        service = AIService(fake)
        result = service.draft_consultation(
            question_type="amount_mismatch",
            question="金额不对",
            fields={"amount_cents": 235860},
        )
        assert "50000.00" not in result["draft"]


class TestHelpers:
    def test_load_json_object_variants(self):
        assert _load_json_object('{"a": 1}') == {"a": 1}
        assert _load_json_object('```json\n{"a": 1}\n```') == {"a": 1}
        assert _load_json_object('说明文字 {"a": 1} 结束') == {"a": 1}
        with pytest.raises(AIServiceError):
            _load_json_object("没有 JSON")
        with pytest.raises(AIServiceError):
            _load_json_object("")

    @pytest.mark.parametrize(
        ("cents", "text", "expected"),
        [
            (235860, "结算款2358.60元", True),
            (235860, "结算款 2,358.60 元", True),
            (300000, "租金3000元", True),
            (1, "一共 0.01 元", True),
            (235860, "结算款即将到账", False),
            (0, "任何文本", True),
        ],
    )
    def test_amount_appears_in_text(self, cents, text, expected):
        assert _amount_appears_in_text(cents, text) is expected

    def test_find_unknown_amounts_ignores_identifiers(self):
        known = {120000, 60000}
        text = "尾号8821的账户在 2025-10-03 收到 1200.00 元，留底 600.00 元。"
        unknown = _find_unknown_amounts(text, known)
        assert "1200.00" not in unknown
        assert "600.00" not in unknown
        assert "8821" not in unknown
        assert "2025" not in unknown

    def test_extracted_event_coercion(self):
        event = ExtractedEvent.model_validate({"amount_cents": 12345, "direction": "inflow"})
        assert event.amount_cents == 12345
        assert to_cents("2358.60") == 235860

    def test_extracted_event_rejects_english_only_amount_string(self):
        event = ExtractedEvent.model_validate(
            {"amount_cents": "123.45", "direction": "inflow"}
        )
        # 字符串按"元"精确换算为分（Decimal，无浮点误差）
        assert event.amount_cents == 12345

    def test_extracted_event_rejects_invalid_direction(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ExtractedEvent.model_validate({"amount_cents": 100, "direction": "收入"})


class TestGLMOpenAICompatibleClient:
    def test_endpoint_normalisation(self):
        client = GLMOpenAICompatibleClient(
            base_url="https://api.example.com/v1",
            api_key="sk-x",
            model="m",
            timeout=5,
        )
        assert client.endpoint == "https://api.example.com/v1/chat/completions"

        client2 = GLMOpenAICompatibleClient(
            base_url="https://api.example.com/v1/chat/completions",
            api_key="sk-x",
            model="m",
            timeout=5,
        )
        assert client2.endpoint == "https://api.example.com/v1/chat/completions"

    def test_network_error_maps_to_unavailable(self, monkeypatch):
        client = GLMOpenAICompatibleClient(
            base_url="https://example.invalid/v1",
            api_key="sk-x",
            model="m",
            timeout=0.01,
        )

        def boom(*args, **kwargs):  # noqa: ANN002, ANN003
            raise httpx.ConnectError("no route")

        monkeypatch.setattr(httpx.Client, "post", boom)
        with pytest.raises(AIServiceError):
            client.complete(system="s", user="u")


class TestAiFailureDoesNotAffectCore:
    def test_cash_engine_unaffected_by_ai_failure(self, merchant_client):
        """/ai/* 全部失败时，登录、事件、分析、CSV 都照常工作。"""
        from tests import fixtures_api as api_fx

        api_fx.setup_merchant(merchant_client)

        # AI 未配置 -> 503
        response = merchant_client.post(
            "/api/v1/ai/extract-cash-event", json={"text": "结算款 2358.60 元即将到账"}
        )
        assert response.status_code == 503
        assert response.json()["code"] == "AI_UNAVAILABLE"
        assert "智能服务暂时不可用" in response.json()["message"]

        explain = merchant_client.post(
            "/api/v1/ai/explain-analysis", json={"max_withdrawable_cents": 120000}
        )
        assert explain.status_code == 503
        assert explain.json()["code"] == "AI_UNAVAILABLE"

        # 核心功能不受影响
        assert merchant_client.get("/api/v1/me").status_code == 200
        assert merchant_client.get("/api/v1/cash-events").status_code == 200
        analysis = merchant_client.get("/api/v1/analysis/today").json()
        assert analysis["max_withdrawable_cents"] == 1200_00
        assert analysis["status"] == "FEASIBLE"

    def test_ai_status_endpoint(self, merchant_client):
        body = merchant_client.get("/api/v1/ai/status").json()
        assert body["enabled"] is False
        assert body["available"] is False
        assert body["provider"] == "openai-compatible"

    def test_ai_status_requires_auth(self, client):
        client.cookies.clear()
        assert client.get("/api/v1/ai/status").status_code == 401

    def test_ai_endpoints_reject_family_member(self, client):
        from tests.conftest import login, register

        register(client, username="fam_ai", roles=["family_member"])
        client.cookies.clear()
        login(client, username="fam_ai")
        assert client.get("/api/v1/ai/status").status_code == 200  # 状态可读
        assert (
            client.post("/api/v1/ai/extract-cash-event", json={"text": "结算款 100 元"}).status_code
            == 403
        )
