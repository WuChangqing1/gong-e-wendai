"""资金分析 API 与固定算例端到端测试。

固定算例（与 ``fixtures_cash.py`` 同一口径，时间锚定在“现在”）：

============ ============ ============== ==============
口径          可提用金额    最紧时点余额    付款/留底缺口
按当前计划    1200.00 元    1800.00 元     0 / 0
到账延迟         0.00 元    -400.00 元     400 / 1000
共同约束         0.00 元    取最保守上限
============ ============ ============== ==============
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from tests import fixtures_api as api_fx


@pytest.fixture
def fixture_merchant(merchant_client: TestClient):
    base, event_ids = api_fx.setup_merchant(merchant_client)
    return merchant_client, base, event_ids


class TestTodayAnalysis:
    def test_on_time_withdrawable_is_1200(self, fixture_merchant):
        client, _, _ = fixture_merchant
        body = client.get("/api/v1/analysis/today").json()
        assert body["status"] == "OK"
        assert body["max_withdrawable_cents"] == 1200_00
        assert body["limiting_balance_cents"] == 1800_00
        assert body["opening_balance_cents"] == 600_00
        assert body["buffer_cents"] == 600_00
        assert body["payment_gap_cents"] == 0
        assert body["buffer_gap_cents"] == 0

    def test_limiting_point_explains_the_amount(self, fixture_merchant):
        client, _, event_ids = fixture_merchant
        body = client.get("/api/v1/analysis/today").json()
        assert body["limiting_event_id"] == event_ids["API-PAY-0001"]
        assert body["limiting_event_title"] == "供应商货款"
        assert body["limiting_reason"]
        assert body["limiting_timestamp"]

    def test_pending_inflows_at_limit(self, fixture_merchant):
        client, _, event_ids = fixture_merchant
        body = client.get("/api/v1/analysis/today").json()
        pending = [item["event_id"] for item in body["pending_inflows_at_limit"]]
        assert pending == [event_ids["API-SETTLE-0002"]]

    def test_balance_curve_is_stepped_and_complete(self, fixture_merchant):
        client, _, _ = fixture_merchant
        points = client.get("/api/v1/analysis/today").json()["points"]
        assert len(points) == 4  # 期初 + 3 笔事项
        assert points[0]["is_opening"] is True
        assert points[0]["balance_cents"] == 600_00
        assert [item["balance_cents"] for item in points] == [600_00, 2800_00, 1800_00, 2500_00]

    def test_window_is_seven_days(self, fixture_merchant):
        client, _, _ = fixture_merchant
        body = client.get("/api/v1/analysis/today").json()
        from datetime import datetime

        start = datetime.fromisoformat(body["snapshot_at"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(body["window_end_at"].replace("Z", "+00:00"))
        assert end - start == timedelta(days=7)

    def test_pending_settlement_not_added_to_opening(self, fixture_merchant):
        client, _, _ = fixture_merchant
        body = client.get("/api/v1/analysis/today").json()
        assert body["pending_settlement_cents"] == 2900_00
        assert body["opening_balance_cents"] == 600_00

    def test_today_does_not_persist_by_default(self, fixture_merchant, db_session):
        client, _, _ = fixture_merchant
        from app.models.cash import AnalysisResult

        client.get("/api/v1/analysis/today")
        assert db_session.query(AnalysisResult).count() == 0

    def test_without_events_returns_input_incomplete(self, merchant_client: TestClient):
        merchant_client.post(
            "/api/v1/auth/register",
            json={"username": "no_events", "password": "Wendai2025", "display_name": "x"},
        )
        body = merchant_client.get("/api/v1/analysis/today").json()
        assert body["max_withdrawable_cents"] is None
        assert body["limiting_reason"]


class TestDelayedAnalysis:
    def test_delayed_withdrawable_is_zero(self, fixture_merchant):
        client, _, _ = fixture_merchant
        body = client.post(
            "/api/v1/analysis/run", json={"mode": "delayed", "delay_days": 8}
        ).json()
        assert body["max_withdrawable_cents"] == 0
        assert body["status"] == "PAYMENT_GAP"

    def test_delayed_gaps_are_400_and_1000(self, fixture_merchant):
        client, _, _ = fixture_merchant
        body = client.post(
            "/api/v1/analysis/run", json={"mode": "delayed", "delay_days": 8}
        ).json()
        assert body["payment_gap_cents"] == 400_00
        assert body["buffer_gap_cents"] == 1000_00
        # 800 元已经包含 200 元的口径等价说明：留底缺口 = 付款缺口 + 留底 - 余额修正
        assert body["buffer_gap_cents"] == body["payment_gap_cents"] + 600_00

    def test_delayed_status_is_payment_gap(self, fixture_merchant):
        client, _, _ = fixture_merchant
        body = client.post(
            "/api/v1/analysis/run", json={"mode": "delayed", "delay_days": 8}
        ).json()
        assert body["status_label"] == "存在付款缺口"


class TestJointAnalysis:
    def test_joint_withdrawable_is_zero(self, fixture_merchant):
        client, _, _ = fixture_merchant
        body = client.post(
            "/api/v1/analysis/run", json={"mode": "joint", "delay_days": 8}
        ).json()
        assert body["max_withdrawable_cents"] == 0

    def test_joint_binding_scenario_is_delayed(self, fixture_merchant):
        client, _, _ = fixture_merchant
        body = client.post(
            "/api/v1/analysis/run", json={"mode": "joint", "delay_days": 8}
        ).json()
        assert body["binding_label"] == "到账延迟"

    def test_joint_returns_both_curves(self, fixture_merchant):
        client, _, _ = fixture_merchant
        body = client.post(
            "/api/v1/analysis/run", json={"mode": "joint", "delay_days": 8}
        ).json()
        labels = [item["label"] for item in body["scenarios"]]
        assert labels == ["按当前计划", "到账延迟"]
        per_scenario = [item["max_withdrawable_cents"] for item in body["scenarios"]]
        assert per_scenario == [1200_00, 0]
        assert body["max_withdrawable_cents"] == min(per_scenario)

    def test_joint_takes_most_conservative_limit(self, fixture_merchant):
        client, _, _ = fixture_merchant
        body = client.post(
            "/api/v1/analysis/run", json={"mode": "joint", "delay_days": 1}
        ).json()
        per_scenario = [item["max_withdrawable_cents"] for item in body["scenarios"]]
        assert body["max_withdrawable_cents"] == min(per_scenario)


class TestRecalculation:
    def test_material_change_marks_result_stale(self, fixture_merchant):
        client, _, event_ids = fixture_merchant
        run = client.post("/api/v1/analysis/run", json={"mode": "current_plan"})
        assert run.status_code == 200
        assert client.get("/api/v1/analysis/stale").json()["is_stale"] is False

        client.patch(
            f"/api/v1/cash-events/{event_ids['API-PAY-0001']}",
            json={"amount_cents": 1500_00, "change_reason": "货款上调"},
        )
        stale = client.get("/api/v1/analysis/stale").json()
        assert stale["is_stale"] is True
        assert "变更" in (stale["stale_reason"] or "")

    def test_recalculation_produces_new_result(self, fixture_merchant):
        client, _, event_ids = fixture_merchant
        before = client.post("/api/v1/analysis/run", json={"mode": "current_plan"}).json()
        assert before["max_withdrawable_cents"] == 1200_00

        client.patch(
            f"/api/v1/cash-events/{event_ids['API-PAY-0001']}",
            json={"amount_cents": 1500_00},
        )
        after = client.get("/api/v1/analysis/today").json()
        assert after["max_withdrawable_cents"] == 700_00
        assert after["max_withdrawable_cents"] != before["max_withdrawable_cents"]

    def test_history_contains_both_results(self, fixture_merchant):
        client, _, event_ids = fixture_merchant
        client.post("/api/v1/analysis/run", json={"mode": "current_plan"})
        client.patch(
            f"/api/v1/cash-events/{event_ids['API-PAY-0001']}", json={"amount_cents": 1500_00}
        )
        client.post("/api/v1/analysis/run", json={"mode": "current_plan"})

        history = client.get("/api/v1/analysis/history").json()
        assert len(history) == 2
        assert history[0]["is_stale"] is False
        assert history[1]["is_stale"] is True
        assert history[1]["max_withdrawable_cents"] == 1200_00
        assert history[0]["max_withdrawable_cents"] == 700_00

    def test_cancel_triggers_recalculation(self, fixture_merchant):
        client, _, event_ids = fixture_merchant
        client.post(f"/api/v1/cash-events/{event_ids['API-SETTLE-0001']}/cancel")
        after = client.get("/api/v1/analysis/today").json()
        assert after["status"] == "PAYMENT_GAP"
        assert after["max_withdrawable_cents"] == 0
        assert after["payment_gap_cents"] == 400_00

    def test_state_included_in_opening_excluded_from_future(self, fixture_merchant):
        client, _, event_ids = fixture_merchant
        client.patch(
            f"/api/v1/cash-events/{event_ids['API-SETTLE-0001']}",
            json={"state": "included_in_opening"},
        )
        body = client.get("/api/v1/analysis/today").json()
        assert event_ids["API-SETTLE-0001"] in body["excluded_event_ids"]
        assert body["window_inflow_cents"] == 700_00

    def test_duplicate_cash_key_makes_input_incomplete(self, fixture_merchant):
        """重复事项编号必须被拒绝：数据库层唯一约束 + 引擎层拒绝计算。"""
        client, base, event_ids = fixture_merchant
        from app.core.errors import Conflict
        from app.services.cash_engine import AnalysisStatus, run_engine
        from tests import fixtures_api

        duplicate = {
            "cash_key": "API-PAY-0001",
            "title": "重复事项",
            "direction": "outflow",
            "amount_cents": 100_00,
            "scheduled_at": (base + timedelta(days=1)).isoformat(),
            "event_type": "other_outflow",
        }
        response = client.post("/api/v1/cash-events", json=duplicate)
        assert response.status_code == 409
        assert response.json()["code"] == "DUPLICATE_CASH_KEY"

        # 绕过 API 直接构造引擎输入：重复编号必须导致 INPUT_INCOMPLETE
        engine_input = fixtures_api.engine_input(base=base)
        events = list(engine_input.events) + [
            fixtures_api.engine_events(base=base)[1]  # 同一 cash_key 再来一次
        ]
        from dataclasses import replace

        result = run_engine(replace(engine_input, events=events))
        assert result.status is AnalysisStatus.INPUT_INCOMPLETE
        assert result.max_withdrawable_cents is None
        assert result.validation_errors
        assert Conflict.code == "CONFLICT"


class TestScenarios:
    def test_create_scenario_and_run(self, fixture_merchant):
        client, base, event_ids = fixture_merchant
        created = client.post(
            "/api/v1/scenarios",
            json={
                "name": "结算推迟到第 6 天",
                "kind": "custom",
                "overrides": [
                    {
                        "cash_event_id": event_ids["API-SETTLE-0001"],
                        "scheduled_at": (base + timedelta(days=5)).isoformat(),
                    }
                ],
            },
        )
        assert created.status_code == 201, created.text
        scenario_id = created.json()["id"]

        body = client.post(
            "/api/v1/analysis/run",
            json={"mode": "scenarios", "scenario_ids": [scenario_id]},
        ).json()
        assert body["scenarios"][0]["label"] == "结算推迟到第 6 天"
        assert body["scenarios"][0]["max_withdrawable_cents"] == 0

    def test_scenario_does_not_modify_cash_event(self, fixture_merchant):
        client, base, event_ids = fixture_merchant
        client.post(
            "/api/v1/scenarios",
            json={
                "name": "情景A",
                "overrides": [
                    {
                        "cash_event_id": event_ids["API-SETTLE-0001"],
                        "amount_cents": 999_00,
                    }
                ],
            },
        )
        event = client.get(f"/api/v1/cash-events/{event_ids['API-SETTLE-0001']}").json()
        assert event["amount_cents"] == 2200_00
        assert event["current_version"] == 1

    def test_list_and_delete_scenario(self, fixture_merchant):
        client, _, _ = fixture_merchant
        created = client.post("/api/v1/scenarios", json={"name": "临时情景"}).json()
        assert len(client.get("/api/v1/scenarios").json()) == 1
        assert client.delete(f"/api/v1/scenarios/{created['id']}").status_code == 200
        assert client.get("/api/v1/scenarios").json() == []

    def test_other_merchant_cannot_read_scenario(
        self, fixture_merchant, second_merchant_client: TestClient
    ):
        client, _, _ = fixture_merchant
        created = client.post("/api/v1/scenarios", json={"name": "私有情景"}).json()
        response = second_merchant_client.patch(
            f"/api/v1/scenarios/{created['id']}", json={"name": "改名"}
        )
        assert response.status_code == 404


class TestAnalysisPermissions:
    def test_analysis_requires_merchant_role(self, client: TestClient):
        from tests.conftest import login, register

        register(client, username="consult_only", roles=["consultant"])
        client.clear_cookies = None  # type: ignore[attr-defined]
        client.cookies.clear()
        login(client, username="consult_only")
        assert client.get("/api/v1/analysis/today").status_code == 403
        assert client.post("/api/v1/analysis/run", json={}).status_code == 403

    def test_unauthenticated_cannot_run_analysis(self, client: TestClient):
        client.cookies.clear()
        assert client.get("/api/v1/analysis/today").status_code == 401


class TestWindowSummary:
    """窗口聚合（图表数据源）：与引擎口径必须一致。"""

    def test_totals_match_engine(self, fixture_merchant):
        client, _, _ = fixture_merchant
        summary = client.get("/api/v1/analysis/window-summary").json()
        analysis = client.get("/api/v1/analysis/today").json()

        assert summary["window_days"] == 7
        assert summary["opening_balance_cents"] == analysis["opening_balance_cents"]
        assert summary["scheduled_inflow_cents"] == analysis["window_inflow_cents"]
        assert summary["scheduled_outflow_cents"] == analysis["window_outflow_cents"]
        assert summary["buffer_cents"] == analysis["buffer_cents"]
        assert (
            summary["scheduled_inflow_cents"] - summary["scheduled_outflow_cents"]
            == summary["net_change_cents"]
        )
        # 期末余额 = 期初 + 净变化
        assert (
            summary["closing_balance_cents"]
            == summary["opening_balance_cents"] + summary["net_change_cents"]
        )

    def test_daily_terms_consistent(self, fixture_merchant):
        client, _, _ = fixture_merchant
        summary = client.get("/api/v1/analysis/window-summary").json()
        days = [item["day"] for item in summary["daily_terms"]]
        assert days == sorted(days)
        # 7 天窗口 + 期初当天
        assert len(summary["daily_terms"]) == 8

        for item in summary["daily_terms"]:
            assert item["net_cents"] == item["inflow_cents"] - item["outflow_cents"]

        assert (
            sum(item["inflow_cents"] for item in summary["daily_terms"])
            == summary["scheduled_inflow_cents"]
        )
        assert (
            sum(item["outflow_cents"] for item in summary["daily_terms"])
            == summary["scheduled_outflow_cents"]
        )

    def test_closing_balance_is_running_total(self, fixture_merchant):
        client, _, _ = fixture_merchant
        summary = client.get("/api/v1/analysis/window-summary").json()
        running = summary["opening_balance_cents"]
        for item in summary["daily_terms"]:
            running += item["net_cents"]
            assert item["closing_balance_cents"] == running

    def test_category_terms_share_ratio(self, fixture_merchant):
        client, _, _ = fixture_merchant
        categories = client.get("/api/v1/analysis/window-summary").json()["category_terms"]
        assert categories

        inflow = [item for item in categories if item["direction"] == "inflow"]
        outflow = [item for item in categories if item["direction"] == "outflow"]
        assert (
            sum(item["amount_cents"] for item in inflow)
            == client.get("/api/v1/analysis/window-summary").json()["scheduled_inflow_cents"]
        )
        for item in outflow:
            assert item["label"]
        assert sum(item["share_ratio"] for item in outflow) == pytest.approx(1.0)

    def test_arrival_terms_only_inflows(self, fixture_merchant):
        client, _, _ = fixture_merchant
        summary = client.get("/api/v1/analysis/window-summary").json()
        arrivals = summary["arrival_terms"]
        assert arrivals
        assert (
            sum(item["amount_cents"] for item in arrivals) == summary["scheduled_inflow_cents"]
        )
        days = [item["day"] for item in arrivals]
        assert days == sorted(days)
        for item in arrivals:
            assert item["event_count"] >= 1
            assert item["titles"]

    def test_cancelled_event_is_excluded(self, fixture_merchant):
        client, _, event_ids = fixture_merchant
        before = client.get("/api/v1/analysis/window-summary").json()
        client.post(f"/api/v1/cash-events/{event_ids['API-SETTLE-0001']}/cancel")
        after = client.get("/api/v1/analysis/window-summary").json()
        assert after["scheduled_inflow_cents"] == before["scheduled_inflow_cents"] - 2200_00
        assert after["event_count"] == before["event_count"] - 1

    def test_empty_merchant_returns_zeroes(self, second_merchant_client):
        body = second_merchant_client.get("/api/v1/analysis/window-summary").json()
        assert body["event_count"] == 0
        assert body["opening_balance_cents"] == 0
        assert body["scheduled_inflow_cents"] == 0
        assert len(body["daily_terms"]) == 8

    def test_requires_merchant_role(self, client: TestClient):
        client.cookies.clear()
        assert client.get("/api/v1/analysis/window-summary").status_code == 401

    def test_response_shape(self, fixture_merchant):
        client, _, _ = fixture_merchant
        body = client.get("/api/v1/analysis/window-summary").json()
        for key in (
            "window_start",
            "window_end",
            "window_days",
            "opening_balance_cents",
            "closing_balance_cents",
            "buffer_cents",
            "scheduled_inflow_cents",
            "scheduled_outflow_cents",
            "net_change_cents",
            "daily_terms",
            "category_terms",
            "arrival_terms",
            "event_count",
        ):
            assert key in body, key


class TestOverviewConsistency:
    def test_overview_matches_analysis(self, fixture_merchant):
        client, _, _ = fixture_merchant
        overview = client.get("/api/v1/account/overview").json()
        analysis = client.get("/api/v1/analysis/today").json()
        assert overview["opening_balance_cents"] == analysis["opening_balance_cents"]
        assert overview["window_inflow_cents"] == analysis["window_inflow_cents"]
        assert overview["window_outflow_cents"] == analysis["window_outflow_cents"]
        assert overview["buffer_cents"] == analysis["buffer_cents"]

    def test_overview_keeps_pending_separate(self, fixture_merchant):
        client, _, _ = fixture_merchant
        overview = client.get("/api/v1/account/overview").json()
        assert "pending_settlement_cents" in overview
        assert overview["pending_settlement_cents"] != overview["opening_balance_cents"]
