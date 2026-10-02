"""GLM 接入测试：密钥保密、视觉校验、降级与金额一致性。"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.errors import AIServiceError
from app.services.ai_service import (
    ALLOWED_IMAGE_TYPES,
    AIService,
    ImageInput,
    _data_url,
)


class _RecordingClient:
    """记录调用参数的测试替身。"""

    def __init__(self, response: str = "{}") -> None:
        self.response = response
        self.calls: list[dict] = []

    def complete(
        self,
        *,
        system: str,
        user: str,
        temperature: float = 0.1,
        image: ImageInput | None = None,
    ) -> str:
        self.calls.append(
            {
                "system": system,
                "user": user,
                "temperature": temperature,
                "image": image,
            }
        )
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


# ---------------------------------------------------------------------------
# 密钥保密
# ---------------------------------------------------------------------------
class TestSecretHygiene:
    def test_status_never_contains_the_secret(self, merchant_client: TestClient, monkeypatch):
        # 用一个可识别的哨兵值，确认它绝不出现
        sentinel = "SENTINEL-GLM-KEY-DO-NOT-LEAK-1234567890"
        monkeypatch.setattr(settings, "ai_enabled", True)
        monkeypatch.setattr(settings, "glm_api_key", sentinel)

        body = merchant_client.get("/api/v1/ai/status")
        assert body.status_code == 200
        text = body.text
        assert sentinel not in text
        # 也不允许出现前缀、后缀或长度信息
        assert sentinel[:8] not in text
        assert sentinel[-8:] not in text
        assert str(len(sentinel)) not in text
        payload = body.json()
        assert set(payload) == {
            "enabled",
            "configured",
            "available",
            "provider",
            "text_model",
            "vision_model",
        }

    def test_secret_is_a_secretstr(self, monkeypatch):
        from pydantic import SecretStr

        monkeypatch.setattr(settings, "glm_api_key", SecretStr("sk-abc"))
        # repr 必须脱敏
        assert "sk-abc" not in repr(settings.glm_api_key)
        assert settings.resolved_ai_api_key == "sk-abc"

    def test_health_endpoint_does_not_leak(self, client: TestClient, monkeypatch):
        sentinel = "SENTINEL-HEALTH-KEY-0987654321"
        monkeypatch.setattr(settings, "ai_enabled", True)
        monkeypatch.setattr(settings, "glm_api_key", sentinel)
        assert sentinel not in client.get("/api/v1/health").text

    def test_status_includes_both_models(self, merchant_client: TestClient):
        payload = merchant_client.get("/api/v1/ai/status").json()
        assert payload["text_model"] == settings.text_model
        assert payload["vision_model"] == settings.vision_model
        assert payload["provider"] in ("zhipu-glm", "openai-compatible")
        assert payload["text_model"] == "glm-4.5-air"
        assert payload["vision_model"] == "glm-4.6v"


# ---------------------------------------------------------------------------
# 视觉输入校验
# ---------------------------------------------------------------------------
class TestVisionExtraction:
    def test_image_extraction_marks_modality(self, monkeypatch):
        payload = {
            "title": "微信结算款",
            "direction": "inflow",
            "amount_cents": 128800,
            "scheduled_at": "2026-10-05T09:00:00+08:00",
            "state": "scheduled",
            "event_type": "settlement",
            "channel": "微信支付",
            "confidence": {"amount_cents": 0.9},
            "warnings": [],
        }
        fake = _RecordingClient(json.dumps(payload, ensure_ascii=False))
        result = AIService(fake).extract_cash_event_from_image(
            b"\x89PNG\r\n\x1a\n" + b"0" * 64, media_type="image/png"
        )
        assert result.used_fields == ["image"]
        assert result.event.event_type == "settlement"
        assert result.event.channel == "微信支付"
        # 视觉任务必须走 vision 分支
        assert fake.calls[0]["image"] is not None
        assert fake.calls[0]["temperature"] == 0.1

    @pytest.mark.parametrize("media_type", ["image/gif", "image/bmp", "application/pdf", ""])
    def test_unsupported_image_type_rejected(self, media_type):
        with pytest.raises(AIServiceError):
            AIService(_RecordingClient()).extract_cash_event_from_image(
                b"data", media_type=media_type
            )

    def test_oversized_image_rejected(self, monkeypatch):
        monkeypatch.setattr(settings, "ai_vision_max_bytes", 1024)
        with pytest.raises(AIServiceError) as info:
            AIService(_RecordingClient()).extract_cash_event_from_image(
                b"x" * 2048, media_type="image/png"
            )
        assert "MB" in str(info.value)

    def test_empty_image_rejected(self):
        with pytest.raises(AIServiceError):
            AIService(_RecordingClient()).extract_cash_event_from_image(
                b"", media_type="image/png"
            )

    def test_allowed_types_are_png_jpeg_webp(self):
        assert set(ALLOWED_IMAGE_TYPES) == {"image/png", "image/jpeg", "image/webp"}

    def test_data_url_is_well_formed(self):
        url = _data_url(ImageInput(data=b"\x01\x02", media_type="image/png"))
        assert url.startswith("data:image/png;base64,")
        assert len(url.split(",", 1)[1]) > 0

    def test_vision_requires_vision_model(self, monkeypatch):
        monkeypatch.setattr(settings, "ai_enabled", True)
        monkeypatch.setattr(settings, "glm_api_key", "sk-x")
        monkeypatch.setattr(settings, "glm_vision_model", "")
        with pytest.raises(AIServiceError):
            AIService().extract_cash_event_from_image(b"x", media_type="image/png")


# ---------------------------------------------------------------------------
# 图片上传接口
# ---------------------------------------------------------------------------
class TestVisionEndpoint:
    def test_endpoint_returns_structured_event(self, merchant_client: TestClient, monkeypatch):
        payload = {
            "title": "支付宝结算",
            "direction": "inflow",
            "amount_cents": 50000,
            "scheduled_at": "2026-10-06T09:00:00+08:00",
            "state": "scheduled",
            "event_type": "settlement",
            "channel": "支付宝",
            "warnings": ["到账日期需要核对"],
        }
        monkeypatch.setattr(settings, "ai_enabled", True)
        monkeypatch.setattr(settings, "glm_api_key", "sk-test")
        monkeypatch.setattr(
            "app.api.v1.ai.AIService",
            lambda: AIService(_RecordingClient(json.dumps(payload, ensure_ascii=False))),
        )
        response = merchant_client.post(
            "/api/v1/ai/extract-cash-event-from-image",
            files={"file": ("receipt.png", b"\x89PNG\r\n\x1a\n" + b"0" * 32, "image/png")},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["event"]["amount_cents"] == 50000
        assert body["event"]["channel"] == "支付宝"
        assert "核对" in body["note"]

    def test_endpoint_rejects_unsupported_type(self, merchant_client: TestClient):
        response = merchant_client.post(
            "/api/v1/ai/extract-cash-event-from-image",
            files={"file": ("x.gif", b"GIF89a", "image/gif")},
        )
        assert response.status_code == 422
        assert response.json()["code"] == "UNSUPPORTED_IMAGE_TYPE"

    def test_endpoint_rejects_oversize_image(self, merchant_client: TestClient, monkeypatch):
        monkeypatch.setattr(settings, "ai_vision_max_bytes", 512)
        response = merchant_client.post(
            "/api/v1/ai/extract-cash-event-from-image",
            files={"file": ("big.png", b"0" * 4096, "image/png")},
        )
        assert response.status_code == 422
        assert response.json()["code"] == "IMAGE_TOO_LARGE"

    def test_endpoint_requires_merchant(self, consultant_client: TestClient):
        response = consultant_client.post(
            "/api/v1/ai/extract-cash-event-from-image",
            files={"file": ("x.png", b"0" * 8, "image/png")},
        )
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# 降级：AI 失败不得影响核心功能
# ---------------------------------------------------------------------------
class TestDegradation:
    def test_unknown_amount_from_vision_is_filtered(self, monkeypatch):
        """图片来源无法逐字核对：金额保留为候选值，但必须要求用户核对。"""
        payload = {
            "title": "结算款",
            "direction": "inflow",
            "amount_cents": 999900,
            "scheduled_at": None,
            "event_type": "settlement",
        }
        result = AIService(_RecordingClient(json.dumps(payload))).extract_cash_event_from_image(
            b"\x89PNG\r\n\x1a\n" + b"0" * 32, media_type="image/png"
        )
        assert result.event.amount_cents == 999900
        assert result.filtered_amounts == []
        assert any("核对" in item for item in result.event.warnings)
        assert any("时间" in item for item in result.event.warnings)

    def test_text_extraction_filters_guessed_amount(self):
        payload = {
            "title": "结算款",
            "direction": "inflow",
            "amount_cents": 888800,
            "scheduled_at": "2026-10-05T09:00:00+08:00",
        }
        result = AIService(_RecordingClient(json.dumps(payload))).extract_cash_event_from_text(
            "平台通知我明天有一笔结算款要入账，具体金额回头再看。"
        )
        assert result.event.amount_cents is None
        assert result.filtered_amounts == ["8888.00"]

    def test_invalid_json_degrades_to_service_error(self):
        with pytest.raises(AIServiceError):
            AIService(_RecordingClient("这不是 JSON")).extract_cash_event_from_text(
                "结算款 2000 元明天到账"
            )

    def test_provider_exception_degrades(self):
        with pytest.raises(AIServiceError):
            AIService(_RecordingClient(RuntimeError("boom"))).extract_cash_event_from_text(
                "结算款 2000 元明天到账"
            )

    def test_unavailable_message_is_user_facing(self, merchant_client: TestClient, monkeypatch):
        from app.api.v1.ai import AI_UNAVAILABLE_MESSAGE

        monkeypatch.setattr(settings, "ai_enabled", False)
        response = merchant_client.post(
            "/api/v1/ai/extract-cash-event", json={"text": "结算款 2000 元明天到账"}
        )
        assert response.status_code == 503
        body = response.json()
        assert body["code"] == "AI_UNAVAILABLE"
        assert body["message"] == AI_UNAVAILABLE_MESSAGE
        # 不暴露内部异常细节
        for banned in ("Traceback", "Exception", "httpx", "JSONDecode"):
            assert banned not in response.text

    def test_core_functions_unaffected_by_ai_outage(self, merchant_client: TestClient, monkeypatch):
        from tests import fixtures_api as api_fx

        api_fx.setup_merchant(merchant_client)
        monkeypatch.setattr(settings, "ai_enabled", False)
        assert merchant_client.get("/api/v1/me").status_code == 200
        assert merchant_client.get("/api/v1/cash-events").status_code == 200
        analysis = merchant_client.get("/api/v1/analysis/today").json()
        assert analysis["max_withdrawable_cents"] == 1200_00
        assert analysis["status"] == "FEASIBLE"
        assert merchant_client.get("/api/v1/enhancements/overview").status_code == 200


# ---------------------------------------------------------------------------
# 说明与咨询：只发送结构化白名单字段
# ---------------------------------------------------------------------------
class TestExplainAndDraftFields:
    def test_explain_sends_only_allowed_fields(self):
        fake = _RecordingClient("好的。")
        AIService(fake).explain_analysis(
            {
                "max_withdrawable_cents": 120000,
                "status": "FEASIBLE",
                "all_transactions": [{"x": 1}],
                "household_info": {"member": "小王"},
                "full_balance_curve": [1, 2, 3],
            }
        )
        sent = fake.calls[0]["user"]
        for forbidden in ("all_transactions", "household_info", "full_balance_curve"):
            assert forbidden not in sent

    def test_draft_sends_only_whitelist(self):
        fake = _RecordingClient("整理后的描述。")
        AIService(fake).draft_consultation(
            question_type="settlement_time",
            question="什么时候到账？",
            fields={
                "event_title": "结算款",
                "amount_cents": 200000,
                "opening_balance_cents": 360000,
                "buffer_cents": 60000,
                "max_withdrawable_cents": 120000,
                "household_comments": "家人说不要提用",
            },
        )
        sent = fake.calls[0]["user"]
        for forbidden in (
            "opening_balance_cents",
            "buffer_cents",
            "max_withdrawable_cents",
            "household_comments",
        ):
            assert forbidden not in sent
        assert "event_title" in sent
