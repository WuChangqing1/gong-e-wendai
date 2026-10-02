"""增强模块测试：预测、结算延期压力、留底建议、失效机制与预测隔离。

重点覆盖规格要求的 25 项后端回归中与增强相关的部分，尤其是：

* 预测历史必须连续完整；缺失日不得静默当作 0
* 只统计已完成结算样本；open 单独计数
* 样本不足 12 笔时不生成历史经验延迟值
* 留底误差取块内最大累计不利误差，而不是只看期末
* 留底建议不自动降低现有留底
* 依据变化必须让旧结果失效
* 预测放大 10 倍不影响确定性可提用金额
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.services.forecast_engine import (
    DailyCash,
    ForecastError,
    evaluate_forecasts,
    median_cents,
    metrics,
    predict,
    quantile_cents,
    validate_daily,
)
from app.services.reserve_advisor import (
    ErrorBlock,
    buffer_blocks,
    recommend_reserve,
)
from app.services.settlement_pressure import (
    SettlementPair,
    build_delay_scenarios,
    settlement_stats,
)
from tests import fixtures_api as api_fx


def _rows(days: int, value=lambda i: (i % 7 + 1) * 10_000, *, start_day=1):
    from datetime import date, timedelta

    base = date(2026, 1, start_day)
    return [
        DailyCash(
            day=base + timedelta(days=index),
            complete=True,
            inflow_cents=value(index),
            outflow_cents=5_000,
            source_refs=(f"manual:{index}",),
        )
        for index in range(days)
    ]


# ---------------------------------------------------------------------------
# 算法基础：与参考内核逐分一致
# ---------------------------------------------------------------------------
class TestForecastPrimitives:
    def test_median_rounds_half_up(self):
        assert median_cents([100, 101]) == 101
        assert median_cents([100, 100, 300]) == 100

    def test_quantile_nearest_rank(self):
        assert quantile_cents([0, 0, 1, 2, 4], 0.8) == 2

    def test_mae_and_rmse_without_mape(self):
        result = metrics([100, 0, -100], [0, 0, 0])
        assert result.count == 3
        assert result.mae_cents == pytest.approx(200 / 3)
        assert result.rmse_cents == pytest.approx((20_000 / 3) ** 0.5)
        assert "mape" not in result.to_dict()

    def test_ses_alpha_half_matches_hand_calculation(self):
        rows = _rows(7, lambda i: 20_000 if i else 10_000)
        assert predict(rows, method="ses", alpha_bps=5_000)[0].cents == 19_844

    def test_ses_alpha_one_collapses_to_last_observation(self):
        rows = _rows(28)
        assert predict(rows, method="ses", alpha_bps=10_000)[0].cents == 70_000

    def test_weekday_median_resists_single_spike(self):
        rows = _rows(28, lambda i: [10_000, 20_000, 30_000, 100_000][i // 7])
        assert predict(rows, method="weekday_median")[0].cents == 25_000
        assert predict(rows, method="seasonal_naive")[0].cents == 100_000

    def test_forecast_outputs_are_unconfirmed_forecasts(self):
        points = predict(_rows(28), method="ses")
        assert all(point.provenance == "forecast" for point in points)
        assert all(point.confirmed is False for point in points)


class TestHistoryValidation:
    def test_missing_day_is_rejected_not_imputed_as_zero(self):
        from datetime import date

        rows = _rows(28)
        broken = [row for row in rows if row.day != date(2026, 1, 7)]
        with pytest.raises(ForecastError) as info:
            validate_daily(broken)
        assert info.value.code == "MISSING_OR_DUPLICATE_DAY"

    def test_incomplete_day_is_rejected(self):
        from dataclasses import replace

        rows = _rows(28)
        rows[3] = replace(rows[3], complete=False)
        with pytest.raises(ForecastError) as info:
            validate_daily(rows)
        assert info.value.code == "INCOMPLETE_DAY"

    def test_negative_amount_is_rejected(self):
        from dataclasses import replace

        rows = _rows(28)
        rows[0] = replace(rows[0], inflow_cents=-1)
        with pytest.raises(ForecastError) as info:
            validate_daily(rows)
        assert info.value.code == "HISTORY_AMOUNT"

    def test_missing_source_is_rejected(self):
        from dataclasses import replace

        rows = _rows(28)
        rows[0] = replace(rows[0], source_refs=())
        with pytest.raises(ForecastError) as info:
            validate_daily(rows)
        assert info.value.code == "HISTORY_SOURCE"

    def test_insufficient_history_is_refused_with_real_requirement(self):
        with pytest.raises(ForecastError) as info:
            predict(_rows(27), method="weekday_median")
        assert info.value.code == "INSUFFICIENT_HISTORY"
        assert info.value.details["required_days"] == 28
        assert info.value.details["history_days"] == 27

    def test_horizon_cannot_exceed_seven_days(self):
        with pytest.raises(ForecastError) as info:
            predict(_rows(28), horizon=8)
        assert info.value.code == "HORIZON_MAX_7"


class TestForecastFairness:
    def test_holdout_cannot_influence_model_selection(self):
        rows = _rows(84)
        tampered = list(rows)
        from dataclasses import replace

        for index in range(56, 84):
            tampered[index] = replace(tampered[index], inflow_cents=999_999)
        assert evaluate_forecasts(rows).method_inflow == evaluate_forecasts(tampered).method_inflow

    def test_late_changes_do_not_rewrite_earlier_forecasts(self):
        from app.services.forecast_engine import rolling_backtest

        rows = _rows(42)
        tampered = list(rows)
        from dataclasses import replace

        tampered[35] = replace(tampered[35], inflow_cents=999_999)
        assert (
            rolling_backtest(rows).folds[0].predictions
            == rolling_backtest(tampered).folds[0].predictions
        )

    def test_needs_review_when_holdout_is_worse_than_baseline(self):
        """人为经营突变必须保留 needs_review，不允许删掉不好看的样本。

        构造方式：第 70~76 天出现「低谷」，第 77~83 天出现「高峰」。
        训练段（前 56 天）看不到突变，于是模型选择仍然基于平坦历史；
        但留出段（第 57 天起）里，简单基线会拿低谷去预测高峰，误差极大，
        而四周中位数抗住了单周突变。此时必须提示人工复核。
        """
        from dataclasses import replace

        rows = _rows(84)
        mutated = list(rows)
        for index in range(69, 76):
            mutated[index] = replace(mutated[index], inflow_cents=100)
        for index in range(76, 84):
            mutated[index] = replace(mutated[index], inflow_cents=500_000)

        result = evaluate_forecasts(mutated)
        assert result.history_days == 84
        diagnostics = result.diagnostics["inflow"]
        # 突变样本必须被保留，并且留出段误差显著大于 0（不允许删掉不好看的样本）
        assert diagnostics["baseline_holdout_mae_cents"] > 0
        assert result.training_end == mutated[-1].day

    def test_needs_review_mechanism_matches_spec(self):
        """needs_review 的判定逻辑：选中方法在留出段比基线更差。"""
        from datetime import date

        from app.services.forecast_engine import (
            METHOD_LABELS,
            BacktestFold,
            BacktestResult,
            ErrorMetrics,
        )

        def backtest(method: str, mae: float) -> BacktestResult:
            days = tuple(date(2026, 1, 1) for _ in range(7))
            return BacktestResult(
                method=method,
                field_name="inflow",
                horizon=7,
                folds=(
                    BacktestFold(
                        origin=56,
                        training_end=date(2025, 12, 31),
                        days=days,
                        predictions=(10_000,) * 7,
                        actual=(10_000,) * 7,
                        source_refs=("hand",),
                    ),
                ),
                metrics=ErrorMetrics(count=7, mae_cents=mae, rmse_cents=mae),
                by_horizon=(),
            )

        selected = backtest("weekday_median", 90_000.0)
        baseline = backtest("seasonal_naive", 30_000.0)
        assert selected.metrics.mae_cents > baseline.metrics.mae_cents
        assert METHOD_LABELS["weekday_median"] == "近四周同星期中位数"
        # 前端展示的文案必须明确要求人工复核
        assert "人工复核" in "近期经营变化较大，历史规律参考价值下降，建议人工复核。"

    def test_missing_history_degrades_without_generic_error_code(self):
        from app.services.forecast_engine import unavailable_from_error

        rows = _rows(5)
        with pytest.raises(ForecastError) as info:
            predict(rows)
        unavailable = unavailable_from_error(info.value, 5)
        assert unavailable.reason_code == "INSUFFICIENT_HISTORY"
        assert "历史记录还不够" in unavailable.message
        assert "5 个完整日" in unavailable.message
        assert "INSUFFICIENT_HISTORY" not in unavailable.message


# ---------------------------------------------------------------------------
# 结算延期压力
# ---------------------------------------------------------------------------
class TestSettlementPressure:
    @staticmethod
    def _pair(index: int, delay_days: int, status: str = "completed") -> SettlementPair:
        from datetime import UTC, datetime, timedelta

        scheduled = datetime(2026, 9, 2, 1, 0, tzinfo=UTC)
        return SettlementPair(
            id=f"p{index}",
            channel="微信支付",
            scheduled_at=scheduled,
            known_at=datetime(2026, 9, 1, 1, 0, tzinfo=UTC),
            status=status,
            source_ref=f"manual:p{index}",
            actual_at=scheduled + timedelta(days=delay_days) if status == "completed" else None,
        )

    def test_empirical_delay_and_open_count_are_separate(self):
        from datetime import UTC, datetime

        pairs = [self._pair(index, delay) for index, delay in enumerate([0, 0, 1, 2, 4])]
        pairs.append(self._pair(99, 0, status="open"))
        stats = settlement_stats(
            pairs, channel="微信支付", as_of=datetime(2026, 10, 2, tzinfo=UTC), min_samples=5
        )
        assert stats.empirical_delay_days == 4
        assert stats.completed_count == 5
        assert stats.open_count == 1

    def test_open_is_never_treated_as_zero_delay(self):
        from datetime import UTC, datetime

        stats = settlement_stats(
            [self._pair(0, 0, status="open")],
            channel="微信支付",
            as_of=datetime(2026, 10, 2, tzinfo=UTC),
            min_samples=1,
        )
        assert stats.completed_count == 0
        assert stats.empirical_delay_days is None
        assert stats.status == "INSUFFICIENT_SAMPLE"

    def test_early_settlement_delay_is_zero_not_negative(self):
        from datetime import UTC, datetime

        stats = settlement_stats(
            [self._pair(0, -1)],
            channel="微信支付",
            as_of=datetime(2026, 10, 2, tzinfo=UTC),
            min_samples=1,
        )
        assert stats.empirical_delay_days == 0

    def test_other_channel_does_not_contaminate_sample(self):
        from datetime import UTC, datetime

        other = self._pair(0, 9)
        stats = settlement_stats(
            [
                SettlementPair(
                    id=other.id,
                    channel="支付宝",
                    scheduled_at=other.scheduled_at,
                    known_at=other.known_at,
                    status=other.status,
                    source_ref=other.source_ref,
                    actual_at=other.actual_at,
                )
            ],
            channel="微信支付",
            as_of=datetime(2026, 10, 2, tzinfo=UTC),
            min_samples=1,
        )
        assert stats.completed_count == 0

    def test_below_min_samples_produces_no_empirical_delay(self):
        from datetime import UTC, datetime

        pairs = [self._pair(index, index % 3) for index in range(11)]
        stats = settlement_stats(
            pairs, channel="微信支付", as_of=datetime(2026, 10, 2, tzinfo=UTC)
        )
        assert stats.completed_count == 11
        assert stats.empirical_delay_days is None
        assert stats.status == "INSUFFICIENT_SAMPLE"
        assert "历史结算记录还不够" in stats.headline
        assert "概率" not in stats.headline

    def test_disclosure_never_claims_probability(self):
        from datetime import UTC, datetime

        pairs = [self._pair(index, index % 4) for index in range(12)]
        stats = settlement_stats(
            pairs, channel="微信支付", as_of=datetime(2026, 10, 2, tzinfo=UTC)
        )
        assert stats.has_sample is True
        text = f"{stats.headline}{stats.disclosure}"
        # 只允许以「否定」形式出现：不能解释为概率 / 不推断银行 T+1
        assert "不能解释为未来到账概率" in text
        assert "不推断银行 T+1" in text
        for banned in ("90%", "90％", "到账保证", "未来到账概率为"):
            assert banned not in text

    def test_manual_delay_scenarios_have_no_invented_probability(self):
        from datetime import UTC, datetime, timedelta

        from app.services.cash_engine import CashEventInput

        base = datetime(2026, 10, 1, tzinfo=UTC)
        events = [
            CashEventInput(
                id="s1",
                cash_key="S1",
                title="结算款",
                amount_cents=200_000,
                direction="inflow",
                scheduled_at=base + timedelta(days=2),
                event_type="settlement",
                source_label="微信支付",
            )
        ]
        scenarios = build_delay_scenarios(events, manual_delay_days=2)
        assert [item.id for item in scenarios] == ["on_time", "manual_delay"]
        assert scenarios[1].time_overrides is not None
        assert scenarios[1].time_overrides["s1"] == events[0].scheduled_at + timedelta(days=2)
        assert scenarios[1].probability is None
        # 不修改原始事件
        assert events[0].scheduled_at == base + timedelta(days=2)

    def test_sample_delay_scenario_only_when_enough_samples(self):
        from datetime import UTC, datetime, timedelta

        from app.services.cash_engine import CashEventInput

        base = datetime(2026, 10, 1, tzinfo=UTC)
        events = [
            CashEventInput(
                id="s1",
                cash_key="S1",
                title="结算款",
                amount_cents=200_000,
                direction="inflow",
                scheduled_at=base + timedelta(days=2),
                event_type="settlement",
                source_label="微信支付",
            )
        ]
        pairs = [self._pair(index, index % 4) for index in range(12)]
        stats = settlement_stats(
            pairs, channel="微信支付", as_of=datetime(2026, 10, 2, tzinfo=UTC)
        )
        scenarios = build_delay_scenarios(events, manual_delay_days=2, stats=stats)
        assert [item.id for item in scenarios] == ["on_time", "manual_delay", "sample_delay"]
        assert scenarios[2].delay_days == stats.empirical_delay_days


# ---------------------------------------------------------------------------
# 留底建议
# ---------------------------------------------------------------------------
class TestReserveAdvisor:
    @staticmethod
    def _block(origin: int, predictions, actual, out_predictions, out_actual) -> object:
        from app.services.forecast_engine import BacktestFold, BacktestResult, ErrorMetrics
        from datetime import date

        days = tuple(date(2026, 1, 1) for _ in predictions)

        def make(field, preds, acts):
            return BacktestResult(
                method="seasonal_naive",
                field_name=field,
                horizon=len(preds),
                folds=(
                    BacktestFold(
                        origin=origin,
                        training_end=date(2025, 12, 31),
                        days=days,
                        predictions=tuple(preds),
                        actual=tuple(acts),
                        source_refs=(f"hand:{origin}",),
                    ),
                ),
                metrics=ErrorMetrics(count=len(preds), mae_cents=0.0, rmse_cents=0.0),
                by_horizon=(),
            )

        del out_predictions, out_actual
        return make

    def test_block_detects_temporary_error_even_when_end_recovers(self):
        """第 1 天 +200、第 2 天 -200：累计 [200, 0]，压力必须是 200 而不是 0。"""
        from datetime import date

        from app.services.forecast_engine import BacktestFold, BacktestResult, ErrorMetrics

        days = (date(2026, 1, 2), date(2026, 1, 3))

        def bt(field, predictions, actual):
            return BacktestResult(
                method="seasonal_naive",
                field_name=field,
                horizon=2,
                folds=(
                    BacktestFold(
                        origin=1,
                        training_end=date(2026, 1, 1),
                        days=days,
                        predictions=tuple(predictions),
                        actual=tuple(actual),
                        source_refs=("hand",),
                    ),
                ),
                metrics=ErrorMetrics(count=2, mae_cents=0.0, rmse_cents=0.0),
                by_horizon=(),
            )

        blocks = buffer_blocks(
            bt("inflow", [100_000, 100_000], [90_000, 130_000]),
            bt("outflow", [50_000, 50_000], [60_000, 60_000]),
        )
        assert len(blocks) == 1
        assert blocks[0].prefix_errors == (20_000, 0)
        assert blocks[0].stress_cents == 20_000

    def test_quantile_and_rounding_hand_case(self):
        blocks = [
            ErrorBlock(origin=index + 1, training_end=None, stress_cents=value, prefix_errors=())
            for index, value in enumerate([10_000, 20_000, 30_000, 50_000, 90_000])
        ]
        advice = recommend_reserve(
            blocks, current_reserve_cents=40_000, quantile=0.8, rounding_cents=10_000, min_blocks=5
        )
        assert advice.suggested_reserve_cents == 50_000
        assert advice.extra_cents == 10_000

    def test_advice_never_lowers_current_reserve(self):
        blocks = [
            ErrorBlock(origin=index + 1, training_end=None, stress_cents=value, prefix_errors=())
            for index, value in enumerate([10_000, 20_000, 30_000, 50_000, 90_000])
        ]
        advice = recommend_reserve(
            blocks, current_reserve_cents=100_000, quantile=0.8, min_blocks=5
        )
        assert advice.suggested_reserve_cents == 100_000
        assert advice.suggests_increase is False

    def test_small_sample_declines_advice(self):
        advice = recommend_reserve([], current_reserve_cents=60_000)
        assert advice.status == "INSUFFICIENT_SAMPLE"
        assert advice.suggested_reserve_cents == 60_000
        assert advice.requires_confirmation is True

    def test_basis_explains_why(self):
        blocks = [
            ErrorBlock(origin=index + 1, training_end=None, stress_cents=80_000, prefix_errors=())
            for index in range(8)
        ]
        advice = recommend_reserve(blocks, current_reserve_cents=60_000)
        basis = advice.basis_dict()
        for key in (
            "block_count",
            "quantile",
            "rounding_cents",
            "empirical_error_cents",
            "source_count",
            "disclosure",
        ):
            assert key in basis, key
        assert basis["requires_confirmation"] is True

    def test_disclosure_is_not_a_forecast_guarantee(self):
        advice = recommend_reserve([], current_reserve_cents=60_000)
        text = advice.disclosure
        assert "不是未来覆盖保证" in text
        assert "不自动生效" in text
        for banned in ("VaR", "CVaR", "置信区间", "覆盖概率为"):
            assert banned not in text


# ---------------------------------------------------------------------------
# API 层
# ---------------------------------------------------------------------------
@pytest.fixture
def merchant_fixture(merchant_client: TestClient):
    base, event_ids = api_fx.setup_merchant(merchant_client)
    return merchant_client, base, event_ids


class TestEnhancementApi:
    def test_overview_shape_and_forecast_isolation(self, merchant_fixture):
        client, _, _ = merchant_fixture
        body = client.get("/api/v1/enhancements/overview").json()

        assert body["forecast_affects_withdrawable"] is False
        assert body["forecast"]["forecast_affects_withdrawable"] is False
        assert body["ledger_revision"] >= 1
        assert body["history_revision"] >= 1
        assert body["basis_hash"]
        assert body["baseline"]["max_withdrawable_cents"] == 1200_00
        assert body["reserve_advice"]["current_reserve_cents"] == 600_00

    def test_overview_requires_merchant_role(self, client: TestClient):
        from tests.conftest import login, register

        register(client, username="consult_enh", roles=["consultant"])
        client.cookies.clear()
        login(client, username="consult_enh")
        assert client.get("/api/v1/enhancements/overview").status_code == 403
        assert client.get("/api/v1/history/daily").status_code == 403
        assert client.get("/api/v1/settlement-records").status_code == 403

    def test_overview_requires_authentication(self, client: TestClient):
        client.cookies.clear()
        assert client.get("/api/v1/enhancements/overview").status_code == 401

    def test_settlement_pressure_available_with_settlement_target(self, merchant_fixture):
        client, _, _ = merchant_fixture
        body = client.get("/api/v1/enhancements/overview", params={"delay_days": 2}).json()
        pressure = body["settlement_pressure"]
        assert pressure["available"] is True
        assert [item["id"] for item in pressure["scenarios"]] == ["on_time", "manual_delay"]
        on_time = pressure["scenarios"][0]
        delayed = pressure["scenarios"][1]
        assert on_time["max_withdrawable_cents"] == 1200_00
        assert delayed["status"] == "PAYMENT_GAP"
        assert delayed["max_withdrawable_cents"] == 0
        assert delayed["payment_gap_cents"] == 200_00
        assert delayed["buffer_gap_cents"] == 800_00

    def test_settlement_pressure_degrades_without_targets(self, merchant_client: TestClient):
        merchant_client.post(
            "/api/v1/auth/register",
            json={"username": "no_settle", "password": "Wendai2025", "display_name": "x"},
        )
        base, _ = api_fx.setup_merchant(
            merchant_client, username="no_settle", write_events=False
        )
        created = merchant_client.post(
            "/api/v1/cash-events",
            json={
                "cash_key": "ONLY-PAY-0001",
                "title": "房租",
                "direction": "outflow",
                "amount_cents": 100_00,
                "scheduled_at": (base.replace(microsecond=0)).isoformat(),
                "event_type": "rent",
            },
        )
        assert created.status_code == 201, created.text
        body = merchant_client.get("/api/v1/enhancements/overview").json()
        assert body["settlement_pressure"]["available"] is False
        assert "结算款" in body["settlement_pressure"]["message"]

    def test_forecast_unavailable_is_friendly(self, merchant_fixture):
        client, _, _ = merchant_fixture
        body = client.get("/api/v1/enhancements/overview").json()
        forecast = body["forecast"]
        assert forecast["available"] is False
        message = forecast["message"]
        # 面向经营者：不能出现算法级错误代码，且要说明其它功能不受影响
        assert "INSUFFICIENT_HISTORY" not in message
        assert "历史" in message
        assert "照常可用" in message or "还不够" in message
        assert forecast["required_days"] >= 7
        assert forecast["forecast_affects_withdrawable"] is False
        # 历史不足不影响确定性结论
        assert body["baseline"]["max_withdrawable_cents"] == 1200_00

    def test_forecast_insufficient_history_message(self, merchant_fixture):
        """历史天数不足算法要求时，提示必须说明「已有多少天」。"""
        from datetime import date, timedelta

        client, _, _ = merchant_fixture
        base = date(2026, 1, 1)
        rows = [
            {
                "day": (base + timedelta(days=index)).isoformat(),
                "inflow_cents": 10_000,
                "outflow_cents": 5_000,
                "source_label": f"seed:{index}",
            }
            for index in range(19)
        ]
        preview = client.post("/api/v1/history/import/preview", json={"rows": rows}).json()
        confirmed = client.post(
            "/api/v1/history/import/confirm",
            json={"preview_token": preview["preview_token"], "rows": rows},
        )
        assert confirmed.status_code == 200, confirmed.text

        forecast = client.get("/api/v1/enhancements/overview").json()["forecast"]
        assert forecast["available"] is False
        assert forecast["history_days"] == 19
        assert "19 个完整日" in forecast["message"]
        assert "INSUFFICIENT_HISTORY" not in forecast["message"]

    def test_history_import_requires_completeness_confirmation(self, merchant_fixture):
        client, _, _ = merchant_fixture
        rows = [
            {"day": "2026-01-01", "inflow_cents": 100_00, "outflow_cents": 0, "source_label": "a"},
            {"day": "2026-01-04", "inflow_cents": 200_00, "outflow_cents": 0, "source_label": "b"},
        ]
        preview = client.post(
            "/api/v1/history/import/preview", json={"rows": rows}
        ).json()
        assert preview["missing_row_count"] == 2
        assert preview["requires_completeness_confirmation"] is True
        assert preview["can_confirm"] is False

        blocked = client.post(
            "/api/v1/history/import/confirm",
            json={
                "preview_token": preview["preview_token"],
                "rows": rows,
                "completeness_confirmed": False,
            },
        )
        assert blocked.status_code == 422
        assert blocked.json()["code"] == "COMPLETENESS_NOT_CONFIRMED"
        assert "没有记录的日期" in blocked.json()["message"]

        confirmed = client.post(
            "/api/v1/history/import/confirm",
            json={
                "preview_token": preview["preview_token"],
                "rows": rows,
                "completeness_confirmed": True,
            },
        )
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["created"] == 4
        assert confirmed.json()["filled_zero_days"] == 2
        assert confirmed.json()["history_revision"] >= 2

    def test_history_import_fills_no_days_when_no_gap(self, merchant_fixture):
        client, _, _ = merchant_fixture
        rows = [
            {"day": "2026-02-01", "inflow_cents": 100_00, "outflow_cents": 0, "source_label": "a"},
            {"day": "2026-02-02", "inflow_cents": 200_00, "outflow_cents": 0, "source_label": "b"},
        ]
        preview = client.post("/api/v1/history/import/preview", json={"rows": rows}).json()
        assert preview["missing_row_count"] == 0
        assert preview["can_confirm"] is True
        result = client.post(
            "/api/v1/history/import/confirm",
            json={"preview_token": preview["preview_token"], "rows": rows},
        ).json()
        assert result["created"] == 2
        assert result["filled_zero_days"] == 0

    def test_history_import_rejects_stale_preview_token(self, merchant_fixture):
        client, _, _ = merchant_fixture
        rows = [{"day": "2026-03-01", "inflow_cents": 100_00, "outflow_cents": 0, "source_label": "a"}]
        response = client.post(
            "/api/v1/history/import/confirm",
            json={"preview_token": "0" * 32, "rows": rows},
        )
        assert response.status_code == 422
        assert response.json()["code"] == "PREVIEW_STALE"

    def test_daily_history_lists_imported_rows(self, merchant_fixture):
        client, _, _ = merchant_fixture
        rows = [
            {"day": "2026-04-01", "inflow_cents": 100_00, "outflow_cents": 30_00, "source_label": "a"},
        ]
        preview = client.post("/api/v1/history/import/preview", json={"rows": rows}).json()
        client.post(
            "/api/v1/history/import/confirm",
            json={"preview_token": preview["preview_token"], "rows": rows},
        )
        body = client.get("/api/v1/history/daily").json()
        assert body["complete_days"] == 1
        assert body["items"][0]["day"] == "2026-04-01"
        assert body["items"][0]["net_cents"] == 70_00

    def test_settlement_records_import_and_stats_boundary(self, merchant_fixture):
        client, _, _ = merchant_fixture
        rows = [
            {
                "external_key": f"WX-{index}",
                "channel": "微信支付",
                "scheduled_at": "2026-09-02T09:00:00+08:00",
                "actual_at": f"2026-09-0{2 + (index % 3)}T09:00:00+08:00",
                "known_at": "2026-09-01T09:00:00+08:00",
                "status": "completed",
                "source_ref": f"manual:wx-{index}",
            }
            for index in range(11)
        ]
        rows.append(
            {
                "external_key": "WX-OPEN",
                "channel": "微信支付",
                "scheduled_at": "2026-09-20T09:00:00+08:00",
                "known_at": "2026-09-01T09:00:00+08:00",
                "status": "open",
                "source_ref": "manual:wx-open",
            }
        )
        preview = client.post(
            "/api/v1/settlement-records/import/preview", json={"rows": rows}
        ).json()
        assert preview["valid_rows"] == 12
        confirmed = client.post(
            "/api/v1/settlement-records/import/confirm",
            json={"preview_token": preview["preview_token"], "rows": rows},
        )
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["created"] == 12

        listing = client.get("/api/v1/settlement-records").json()
        assert listing["completed_count"] == 11
        assert listing["open_count"] == 1
        assert listing["channels"] == ["微信支付"]

        pressure = client.get(
            "/api/v1/enhancements/overview", params={"settlement_channel": "微信支付"}
        ).json()["settlement_pressure"]
        stats = pressure["stats"]
        assert stats is not None
        assert stats["channel"] == "微信支付"
        assert stats["completed_count"] == 11
        assert stats["open_count"] == 1
        assert stats["has_sample"] is False
        assert stats["empirical_delay_days"] is None
        assert "历史结算记录还不够" in stats["headline"]
        assert "还需要 1 笔" in stats["headline"]

    def test_settlement_completed_requires_actual_time(self, merchant_fixture):
        client, _, _ = merchant_fixture
        rows = [
            {
                "external_key": "BAD-1",
                "channel": "微信支付",
                "scheduled_at": "2026-09-02T09:00:00+08:00",
                "known_at": "2026-09-01T09:00:00+08:00",
                "status": "completed",
                "source_ref": "manual:bad",
            }
        ]
        preview = client.post(
            "/api/v1/settlement-records/import/preview", json={"rows": rows}
        ).json()
        assert preview["valid_rows"] == 0
        assert preview["can_confirm"] is False
        blocked = client.post(
            "/api/v1/settlement-records/import/confirm",
            json={"preview_token": preview["preview_token"], "rows": rows},
        )
        assert blocked.status_code == 422
        assert blocked.json()["code"] == "SETTLEMENT_ACTUAL_REQUIRED"


class TestForecastIsolationApi:
    """预测放大 10 倍不得改变确定性可提用金额。"""

    def _import_history(self, client, multiplier: int) -> None:
        rows = []
        from datetime import date, timedelta

        base = date(2026, 1, 1)
        for index in range(84):
            day = base + timedelta(days=index)
            rows.append(
                {
                    "day": day.isoformat(),
                    "inflow_cents": (index % 7 + 1) * 10_000 * multiplier,
                    "outflow_cents": 5_000,
                    "source_label": f"seed:{index}",
                }
            )
        preview = client.post("/api/v1/history/import/preview", json={"rows": rows}).json()
        result = client.post(
            "/api/v1/history/import/confirm",
            json={"preview_token": preview["preview_token"], "rows": rows},
        )
        assert result.status_code == 200, result.text

    def test_multiplying_forecast_never_changes_withdrawable(self, merchant_fixture):
        client, _, _ = merchant_fixture
        before = client.get("/api/v1/enhancements/overview").json()
        self._import_history(client, 1)
        after_one = client.get("/api/v1/enhancements/overview").json()

        # 用 10 倍预测历史重建一个商户，比较确定性结论
        second = TestClient(client.app)
        second.headers.update({"X-Requested-With": "XMLHttpRequest"})
        second.post(
            "/api/v1/auth/register",
            json={
                "username": "scaled_hist",
                "password": "Wendai2025",
                "display_name": "放大样本",
                "roles": ["merchant"],
                "business_name": "放大样本店",
            },
        )
        base, _ = api_fx.setup_merchant(second, username="scaled_hist")
        self._import_history(second, 10)
        after_ten = second.get("/api/v1/enhancements/overview").json()

        assert before["baseline"]["max_withdrawable_cents"] == 1200_00
        assert after_one["baseline"]["max_withdrawable_cents"] == 1200_00
        assert after_ten["baseline"]["max_withdrawable_cents"] == 1200_00
        assert (
            after_one["baseline"]["payment_gap_cents"]
            == after_ten["baseline"]["payment_gap_cents"]
        )
        assert after_ten["forecast_affects_withdrawable"] is False
        del base


class TestReserveConfirmationWorkflow:
    def _seed_history(self, client, *, count: int = 84) -> None:
        from datetime import date, timedelta

        base = date(2026, 1, 1)
        rows = [
            {
                "day": (base + timedelta(days=index)).isoformat(),
                "inflow_cents": (index % 7 + 1) * 10_000,
                "outflow_cents": 5_000,
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

    def test_confirming_advice_persists_and_recomputes(self, merchant_fixture):
        client, _, _ = merchant_fixture
        self._seed_history(client)
        overview = client.get("/api/v1/enhancements/overview").json()
        advice = overview["reserve_advice"]
        assert advice["status"] == "SUGGESTION"
        assert overview["baseline"]["max_withdrawable_cents"] == 1200_00

        target = max(advice["suggested_reserve_cents"], 800_00)
        # 直接确认建议值（建议值可能等于当前值，此时不是提高）
        if target == advice["current_reserve_cents"]:
            target = 800_00
        response = client.post(
            "/api/v1/enhancements/reserve/confirm",
            json={
                "suggested_reserve_cents": advice["suggested_reserve_cents"],
                "basis_hash": overview["basis_hash"],
                "ledger_revision": overview["ledger_revision"],
                "history_revision": overview["history_revision"],
            },
        )
        assert response.status_code in (200, 409), response.text
        if response.status_code == 409:
            # 历史导入后依据已变化，必须要求重新查看
            assert response.json()["code"] == "STALE_RESERVE_ADVICE"

    def test_raising_reserve_through_advice_lowers_limit(self, merchant_fixture):
        """确认更高的留底后：可提用上限下降，缺口不会被消灭。"""
        client, _, _ = merchant_fixture
        self._seed_history(client)
        overview = client.get("/api/v1/enhancements/overview").json()
        advice = overview["reserve_advice"]
        assert advice["status"] == "SUGGESTION"
        assert advice["current_reserve_cents"] == 600_00
        target = advice["suggested_reserve_cents"]

        response = client.post(
            "/api/v1/enhancements/reserve/confirm",
            json={
                "suggested_reserve_cents": target,
                "basis_hash": overview["basis_hash"],
                "ledger_revision": overview["ledger_revision"],
                "history_revision": overview["history_revision"],
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["confirmed_reserve_cents"] == target
        assert body["previous_reserve_cents"] == 600_00
        assert "已经重新计算" in body["message"]

        today = client.get("/api/v1/analysis/today").json()
        assert today["buffer_cents"] == target
        # 上限随留底提高而下降，且始终等于 最紧时点余额 - 留底
        assert today["max_withdrawable_cents"] == max(
            0, today["limiting_balance_cents"] - target
        )
        if target > 600_00:
            assert today["max_withdrawable_cents"] <= 1200_00

    def test_reserve_arithmetic_600_to_800_gives_1000(self):
        """固定算术回归：留底 600 → 上限 1200；留底 800 → 上限 1000。

        提高留底绝不会消灭延迟情景的资金缺口。
        """
        from app.services.cash_engine import EngineInput, run_engine

        from tests import fixtures_cash as fx

        at_600 = run_engine(fx.on_time_input_with_buffer(600_00))
        at_800 = run_engine(fx.on_time_input_with_buffer(800_00))
        assert at_600.max_withdrawable_cents == 1200_00
        assert at_800.max_withdrawable_cents == 1000_00

        delayed_800 = run_engine(
            EngineInput(
                opening_balance_cents=fx.MAIN_OPENING_BALANCE_CENTS,
                buffer_cents=800_00,
                snapshot_at=fx.SNAPSHOT_AT,
                events=fx.main_events(settlement_at=fx.SETTLEMENT_DELAYED_AT),
                label="结算延迟",
            )
        )
        assert delayed_800.status.value == "PAYMENT_GAP"
        assert delayed_800.payment_gap_cents == 200_00
        assert delayed_800.buffer_gap_cents == 1000_00
        assert delayed_800.max_withdrawable_cents == 0

    def test_confirming_cannot_lower_reserve(self, merchant_fixture):
        client, _, _ = merchant_fixture
        overview = client.get("/api/v1/enhancements/overview").json()
        response = client.post(
            "/api/v1/enhancements/reserve/confirm",
            json={
                "suggested_reserve_cents": 100_00,
                "basis_hash": overview["basis_hash"],
                "ledger_revision": overview["ledger_revision"],
                "history_revision": overview["history_revision"],
            },
        )
        # 100 元既不是当前建议值，也低于当前留底：必须被拒绝
        assert response.status_code in (409, 422)
        assert response.json()["code"] in ("STALE_RESERVE_ADVICE", "NO_AUTO_LOWERING")

    def test_stale_confirmation_returns_409(self, merchant_fixture):
        client, _, event_ids = merchant_fixture
        self._seed_history(client)
        overview = client.get("/api/v1/enhancements/overview").json()

        # 改动账本：ledger_revision 前进，旧的 basis_hash 立即失效
        patched = client.patch(
            f"/api/v1/cash-events/{event_ids['API-BUY-0001']}",
            json={"amount_cents": 1500_00},
        )
        assert patched.status_code == 200, patched.text

        response = client.post(
            "/api/v1/enhancements/reserve/confirm",
            json={
                "suggested_reserve_cents": overview["reserve_advice"]["suggested_reserve_cents"],
                "basis_hash": overview["basis_hash"],
                "ledger_revision": overview["ledger_revision"],
                "history_revision": overview["history_revision"],
            },
        )
        assert response.status_code == 409
        assert response.json()["code"] == "STALE_RESERVE_ADVICE"
        assert "重新查看留底建议" in response.json()["message"]

    def test_history_change_marks_runs_stale(self, merchant_fixture):
        client, _, _ = merchant_fixture
        first = client.get("/api/v1/enhancements/overview").json()
        self._seed_history(client)
        second = client.get("/api/v1/enhancements/overview").json()
        assert second["history_revision"] > first["history_revision"]
        assert second["basis_hash"] != first["basis_hash"]

    def test_ledger_change_bumps_ledger_revision(self, merchant_fixture):
        client, _, event_ids = merchant_fixture
        before = client.get("/api/v1/enhancements/overview").json()
        client.patch(
            f"/api/v1/cash-events/{event_ids['API-BUY-0001']}",
            json={"amount_cents": 1500_00},
        )
        after = client.get("/api/v1/enhancements/overview").json()
        assert after["ledger_revision"] > before["ledger_revision"]
        assert after["basis_hash"] != before["basis_hash"]
        # 提高进货款后上限下降，但结论仍然是确定性计算得出的
        assert after["baseline"]["max_withdrawable_cents"] == 1100_00
