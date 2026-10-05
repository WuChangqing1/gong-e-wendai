"""定稿验收参数：经营留底 1200 元的完整回归。

需求给定的一组验收参数（用于最终录制与验收）：

* 期初 3,600 元、经营留底 **1,200 元**、同一组核心收付款事项；
* 按当前计划：最紧时点余额 1,800 元 → 可提用 **600 元**，状态 ``FEASIBLE``；
* 结算延迟 2 天：最紧时点余额 −200 元 → 状态 ``PAYMENT_GAP``，可提用 0，
  付款缺口 **200 元**、留底缺口 **1,400 元**。

注意两个缺口的含义不同、**不能相加**（1,400 已经把 200 包含在内）。
600 元留底的核心回归仍在 ``test_cash_engine.py`` / ``fixtures_cash.py`` 中，
本文件只是**额外**的一组参数，不替代它。
"""

from __future__ import annotations

import pytest

from app.services.cash_engine import AnalysisStatus, run_engine
from tests import fixtures_cash as fx
from tests import fixtures_api as api_fx

#: 定稿验收使用的经营留底
RECORDING_BUFFER_CENTS = 1200_00
DELAY_DAYS = 2


def _on_time():
    return run_engine(
        fx.EngineInput(
            opening_balance_cents=fx.MAIN_OPENING_BALANCE_CENTS,
            buffer_cents=RECORDING_BUFFER_CENTS,
            snapshot_at=fx.SNAPSHOT_AT,
            events=fx.main_events(),
            label="按当前计划",
        )
    )


def _delayed():
    return run_engine(
        fx.EngineInput(
            opening_balance_cents=fx.MAIN_OPENING_BALANCE_CENTS,
            buffer_cents=RECORDING_BUFFER_CENTS,
            snapshot_at=fx.SNAPSHOT_AT,
            events=fx.main_events(settlement_at=fx.SETTLEMENT_DELAYED_AT),
            label="结算延迟",
        )
    )


class TestRecordingBuffer1200:
    """留底 1,200 元时的四个数字。"""

    def test_on_time_is_feasible_and_withdrawable_is_600(self):
        result = _on_time()
        assert result.status is AnalysisStatus.FEASIBLE
        assert result.limiting_balance_cents == fx.EXPECTED_ON_TIME["limiting_balance_cents"] == 1800_00
        assert result.max_withdrawable_cents == 600_00
        assert result.payment_gap_cents == 0
        assert result.buffer_gap_cents == 0

    def test_delayed_is_payment_gap_with_two_separate_gaps(self):
        result = _delayed()
        assert result.status is AnalysisStatus.PAYMENT_GAP
        assert result.max_withdrawable_cents == 0
        # 最紧时点余额 −200：付款缺口是「低于 0 的部分」
        assert result.limiting_balance_cents == -200_00
        assert result.payment_gap_cents == 200_00
        # 留底缺口是「低于留底 1,200 的部分」，本身已经包含付款缺口
        assert result.buffer_gap_cents == 1400_00
        # 两个缺口不能相加：加起来的 1600 是错误口径
        assert result.payment_gap_cents + result.buffer_gap_cents != result.buffer_gap_cents

    def test_buffer_change_only_moves_the_withdrawable_limit(self):
        """留底只影响可提用上限与留底缺口，不影响收付款与期末余额。"""
        base = run_engine(
            fx.EngineInput(
                opening_balance_cents=fx.MAIN_OPENING_BALANCE_CENTS,
                buffer_cents=fx.MAIN_BUFFER_CENTS,
                snapshot_at=fx.SNAPSHOT_AT,
                events=fx.main_events(),
                label="按当前计划",
            )
        )
        raised = _on_time()
        assert raised.window_inflow_cents == base.window_inflow_cents
        assert raised.window_outflow_cents == base.window_outflow_cents
        assert raised.end_balance_cents == base.end_balance_cents
        assert base.max_withdrawable_cents == 1200_00
        assert raised.max_withdrawable_cents == 600_00


class TestRecordingBufferThroughApi:
    """通过接口参数（不改商户默认留底）也能得到同一组数字。"""

    def test_run_with_buffer_override(self, merchant_client):
        api_fx.setup_merchant(merchant_client)

        on_time = merchant_client.post(
            "/api/v1/analysis/run",
            json={"mode": "current_plan", "buffer_cents": RECORDING_BUFFER_CENTS},
        )
        assert on_time.status_code == 200, on_time.text
        body = on_time.json()
        assert body["status"] == "FEASIBLE"
        assert body["max_withdrawable_cents"] == 600_00
        assert body["buffer_cents"] == RECORDING_BUFFER_CENTS

        delayed = merchant_client.post(
            "/api/v1/analysis/run",
            json={
                "mode": "delayed",
                "delay_days": DELAY_DAYS,
                "buffer_cents": RECORDING_BUFFER_CENTS,
            },
        )
        assert delayed.status_code == 200, delayed.text
        body = delayed.json()
        assert body["status"] == "PAYMENT_GAP"
        assert body["max_withdrawable_cents"] == 0
        assert body["payment_gap_cents"] == 200_00
        assert body["buffer_gap_cents"] == 1400_00

    def test_default_buffer_is_untouched_by_the_override(self, merchant_client):
        """传 buffer_cents 只是本次计算的参数，不得写回商户档案。"""
        api_fx.setup_merchant(merchant_client)
        merchant_client.post(
            "/api/v1/analysis/run",
            json={"mode": "current_plan", "buffer_cents": RECORDING_BUFFER_CENTS},
        )
        profile = merchant_client.get("/api/v1/merchant/profile").json()
        assert profile["default_buffer_amount_cents"] == api_fx.MAIN_BUFFER_CENTS


@pytest.mark.parametrize(
    ("buffer_cents", "expected_withdrawable", "expected_buffer_gap"),
    [
        (600_00, 1200_00, 800_00),
        (1200_00, 600_00, 1400_00),
    ],
)
def test_buffer_parameters_stay_side_by_side(
    buffer_cents: int, expected_withdrawable: int, expected_buffer_gap: int
):
    """两组参数并列保留：600 元是核心回归，1200 元是定稿验收。"""
    on_time = run_engine(
        fx.EngineInput(
            opening_balance_cents=fx.MAIN_OPENING_BALANCE_CENTS,
            buffer_cents=buffer_cents,
            snapshot_at=fx.SNAPSHOT_AT,
            events=fx.main_events(),
            label="按当前计划",
        )
    )
    delayed = run_engine(
        fx.EngineInput(
            opening_balance_cents=fx.MAIN_OPENING_BALANCE_CENTS,
            buffer_cents=buffer_cents,
            snapshot_at=fx.SNAPSHOT_AT,
            events=fx.main_events(settlement_at=fx.SETTLEMENT_DELAYED_AT),
            label="结算延迟",
        )
    )
    assert on_time.max_withdrawable_cents == expected_withdrawable
    assert delayed.payment_gap_cents == 200_00
    assert delayed.buffer_gap_cents == expected_buffer_gap
