"""留底确认参数一致性回归。

背景（真实缺陷）：``confirm_reserve`` 曾用**默认参数**重算 overview 再比对
``basis_hash``。只要用户是用非默认参数（例如 ``delay_days=3``）看到建议，
重算得到的 basis 与提交值不同，确认就会稳定返回 409 —— 用户看到了建议，
却永远点不成功。

修复后：服务端从 ``EnhancementRun.parameters_json`` 读回当时的计算参数复算。
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from tests import fixtures_api as api_fx
from tests.test_enhancement import merchant_fixture  # noqa: F401  复用固定算例装置

CONFIRM = "/api/v1/enhancements/reserve/confirm"
OVERVIEW = "/api/v1/enhancements/overview"

_ = (pytest, api_fx)


def _seed_history(client: TestClient, *, count: int = 84) -> None:
    """写入足够的历史经营数据，让留底建议形成真实建议。"""
    base = date(2026, 1, 1)
    rows = [
        {
            "day": (base + timedelta(days=index)).isoformat(),
            "inflow_cents": (index % 7 + 1) * 10_000 + (index % 3) * 2_500,
            "outflow_cents": 5_000 + (index % 5) * 1_200,
            "source_label": f"seed:{index}",
        }
        for index in range(count)
    ]
    preview = client.post("/api/v1/history/import/preview", json={"rows": rows}).json()
    result = client.post(
        "/api/v1/history/import/confirm",
        json={"preview_token": preview["preview_token"], "rows": rows},
    )
    assert result.status_code == 200, result.text


def _confirm_payload(overview: dict) -> dict:
    return {
        "suggested_reserve_cents": overview["reserve_advice"]["suggested_reserve_cents"],
        "basis_hash": overview["basis_hash"],
        "ledger_revision": overview["ledger_revision"],
        "history_revision": overview["history_revision"],
        "run_id": overview.get("run_id"),
    }


class TestReserveParameterConsistency:
    def test_default_parameters_still_confirm(self, merchant_fixture):
        client, _, _ = merchant_fixture
        _seed_history(client)
        overview = client.get(OVERVIEW).json()
        response = client.post(CONFIRM, json=_confirm_payload(overview))
        assert response.status_code == 200, response.text

    def test_delay_days_three_can_be_confirmed(self, merchant_fixture):
        """专项：delay_days=3 得到建议后必须可以立即确认。

        修复前这里会稳定返回 409（默认参数是 2，重算 basis 与提交值不同）。
        """
        client, _, _ = merchant_fixture
        _seed_history(client)
        overview = client.get(OVERVIEW, params={"delay_days": 3}).json()
        assert overview["run_id"], "增强运行必须返回 run_id 才能复算"

        response = client.post(CONFIRM, json=_confirm_payload(overview))
        assert response.status_code == 200, response.text
        assert response.json()["confirmed_reserve_cents"] == (
            overview["reserve_advice"]["suggested_reserve_cents"]
        )

    def test_non_default_quantiles_can_be_confirmed(self, merchant_fixture):
        client, _, _ = merchant_fixture
        _seed_history(client)
        overview = client.get(
            OVERVIEW,
            params={"delay_days": 4, "delay_quantile": 0.75, "reserve_quantile": 0.8},
        ).json()

        response = client.post(CONFIRM, json=_confirm_payload(overview))
        assert response.status_code == 200, response.text

    def test_settlement_channel_can_be_confirmed(self, merchant_fixture):
        client, _, _ = merchant_fixture
        _seed_history(client)
        overview = client.get(OVERVIEW, params={"settlement_channel": "平台结算"}).json()
        response = client.post(CONFIRM, json=_confirm_payload(overview))
        assert response.status_code == 200, response.text

    def test_run_parameters_are_persisted(self, merchant_fixture):
        """EnhancementRun 必须保存完整参数，确认时才有据可依。"""
        from app.core.database import SessionLocal
        from app.models.enhancement import EnhancementRun

        client, _, _ = merchant_fixture
        _seed_history(client)
        overview = client.get(
            OVERVIEW,
            params={"delay_days": 5, "delay_quantile": 0.6, "reserve_quantile": 0.7},
        ).json()

        db = SessionLocal()
        try:
            run = db.get(EnhancementRun, overview["run_id"])
            assert run is not None
            params = run.parameters_json
            for key in (
                "reference_at",
                "delay_days",
                "delay_quantile",
                "reserve_quantile",
                "reserve_rounding_cents",
                "settlement_channel",
            ):
                assert key in params, key
            assert params["delay_days"] == 5
            assert params["delay_quantile"] == 0.6
            assert params["reserve_quantile"] == 0.7
            assert params["reference_at"], "reference_at 必须落库"
        finally:
            db.close()

    def test_real_stale_advice_still_conflicts(self, merchant_fixture):
        """真正的依据变化仍然必须 409，不能因为放宽而失去保护。"""
        client, _, _ = merchant_fixture
        _seed_history(client)
        overview = client.get(OVERVIEW, params={"delay_days": 3}).json()
        payload = _confirm_payload(overview)

        # 改动账本（新增一笔事项）→ 依据变化
        created = client.post(
            "/api/v1/cash-events",
            json={
                "title": "临时采购",
                "direction": "outflow",
                "amount_cents": 10_000,
                "scheduled_at": (date(2026, 3, 1)).isoformat() + "T02:00:00Z",
                "event_type": "supplier_payment",
            },
        )
        assert created.status_code == 201, created.text

        response = client.post(CONFIRM, json=payload)
        assert response.status_code == 409, response.text
        assert response.json()["code"] == "STALE_RESERVE_ADVICE"

    def test_wrong_suggested_amount_conflicts(self, merchant_fixture):
        client, _, _ = merchant_fixture
        _seed_history(client)
        overview = client.get(OVERVIEW, params={"delay_days": 3}).json()
        payload = _confirm_payload(overview)
        payload["suggested_reserve_cents"] = payload["suggested_reserve_cents"] + 1_00

        response = client.post(CONFIRM, json=payload)
        assert response.status_code == 409, response.text
        assert response.json()["code"] == "STALE_RESERVE_ADVICE"

    def test_missing_run_id_is_rejected_rather_than_silently_defaulted(
        self, merchant_fixture
    ):
        """不带 run_id 时不得静默按默认参数放行。

        这条既覆盖「老客户端」的兼容行为，也确认不会误判为可确认：
        参数不同 → basis 不同 → 409，用户会重新查看建议。
        """
        client, _, _ = merchant_fixture
        _seed_history(client)
        overview = client.get(OVERVIEW, params={"delay_days": 3}).json()
        payload = _confirm_payload(overview)
        payload.pop("run_id")

        response = client.post(CONFIRM, json=payload)
        assert response.status_code == 409, response.text
        assert response.json()["code"] == "STALE_RESERVE_ADVICE"
