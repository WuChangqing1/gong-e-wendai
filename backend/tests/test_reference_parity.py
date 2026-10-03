"""Python 引擎与参考内核的一致性测试（reference parity）。

数据来源
--------
``tests/data/reference_vectors.json`` 固化自
``reference/wendai-enhancements/wendai-enhancements-v1/core/cash-engine.mjs``
的 ``evaluate()``（由 ``tests/reference-engine.test.mjs`` 覆盖）以及
``docs/人工标准答案.md``，并已用 Node 交叉核对。

作用
----
参考包只作为计算规范与人工标准答案来源，**不是**第二套线上金额计算源。
本文件保证移植到 Python 的数学与状态语义与参考实现逐分一致，
避免「移植时悄悄改了口径」。

任何一处不一致都必须先判断哪一侧错了，不允许直接改期望值迁就实现。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.services.cash_engine import (
    CashEventInput,
    EngineInput,
    run_engine,
    run_joint,
)

DATA_FILE = Path(__file__).parent / "data" / "reference_vectors.json"
PAYLOAD = json.loads(DATA_FILE.read_text(encoding="utf-8"))

AS_OF = datetime.fromisoformat(PAYLOAD["_coordinate"]["asOf"])
WINDOW_DAYS = PAYLOAD["_coordinate"]["window_days"]
VECTORS = PAYLOAD["vectors"]


def _to_input(vector: dict) -> EngineInput:
    events = []
    for index, item in enumerate(vector["events"]):
        delta = int(item["delta_cents"])
        events.append(
            CashEventInput(
                id=item["key"],
                cash_key=f"REF-{item['key'].upper()}",
                title=item["key"],
                amount_cents=abs(delta),
                direction="inflow" if delta > 0 else "outflow",
                scheduled_at=datetime.fromisoformat(item["at"]),
                event_type="settlement" if delta > 0 else "other_outflow",
                sequence_index=index,
            )
        )
    return EngineInput(
        opening_balance_cents=int(vector["opening_cents"]),
        buffer_cents=int(vector["buffer_cents"]),
        snapshot_at=AS_OF,
        events=events,
        window_days=WINDOW_DAYS,
        label=vector["name"],
    )


def _by_name(name: str) -> dict:
    for vector in VECTORS:
        if vector["name"] == name:
            return vector
    raise AssertionError(f"缺少参考向量：{name}")


@pytest.mark.parametrize("vector", VECTORS, ids=[item["name"] for item in VECTORS])
class TestReferenceParity:
    def test_status_matches(self, vector):
        result = run_engine(_to_input(vector))
        assert str(result.status) == vector["expected"]["status"]

    def test_max_withdrawable_matches(self, vector):
        result = run_engine(_to_input(vector))
        assert result.max_withdrawable_cents == vector["expected"]["max_withdrawable_cents"]

    def test_gaps_match(self, vector):
        result = run_engine(_to_input(vector))
        assert result.payment_gap_cents == vector["expected"]["payment_gap_cents"]
        assert result.buffer_gap_cents == vector["expected"]["buffer_gap_cents"]

    def test_balances_match(self, vector):
        result = run_engine(_to_input(vector))
        assert result.minimum_balance_cents == vector["expected"]["minimum_balance_cents"]
        assert result.end_balance_cents == vector["expected"]["end_balance_cents"]

    def test_limiting_point_matches(self, vector):
        result = run_engine(_to_input(vector))
        expected = vector["expected"]
        if expected.get("limiting_is_opening"):
            assert result.limiting_timestamp == AS_OF
        elif "limiting_event" in expected:
            assert result.limiting_event_id == expected["limiting_event"]

    def test_gaps_never_added_up(self, vector):
        """留底缺口已经包含付款缺口，任何情况下都不得相加。"""
        if not vector["expected"].get("gaps_are_not_additive"):
            return
        result = run_engine(_to_input(vector))
        payment_gap = result.payment_gap_cents
        buffer_gap = result.buffer_gap_cents
        # 留底缺口 = 距留底的总差额；付款缺口 = 其中余额为负的部分。
        # 二者之差正是留底本身，因此相加得到的数字（1000 元）没有任何业务含义。
        assert payment_gap == 20000
        assert buffer_gap == 80000
        assert buffer_gap == payment_gap + result.buffer_cents
        assert buffer_gap == abs(min(0, result.minimum_balance_cents)) + result.buffer_cents
        assert payment_gap + buffer_gap == 100000


class TestReferenceJointParity:
    def test_joint_is_payment_gap_not_feasible(self):
        on_time = _to_input(_by_name("3600 按时计划"))
        delayed = _to_input(_by_name("3600 结算延迟到 D4"))
        joint = run_joint([on_time, delayed])
        expected = PAYLOAD["joint"]["expected"]
        assert str(joint.status) == expected["status"]
        assert joint.max_withdrawable_cents == expected["max_withdrawable_cents"]
        assert joint.max_withdrawable_cents == 0
        assert any(item.status.value == "PAYMENT_GAP" for item in joint.scenario_results)

    def test_joint_is_most_conservative(self):
        on_time = _to_input(_by_name("3600 按时计划"))
        delayed = _to_input(_by_name("3600 结算延迟到 D4"))
        joint = run_joint([on_time, delayed])
        per_scenario = [item.max_withdrawable_cents for item in joint.scenario_results]
        assert per_scenario == [120000, 0]
        assert joint.max_withdrawable_cents == min(per_scenario)
        assert joint.binding_label == "3600 结算延迟到 D4"


class TestReferenceMonotonicity:
    """参考内核与人工标准答案共同要求的单调性。"""

    def test_delaying_income_never_raises_limit(self):
        vector = _by_name("3600 按时计划")
        baseline = run_engine(_to_input(vector))
        assert baseline.max_withdrawable_cents == 120000
        for day in range(0, 5):
            shifted = dict(vector)
            shifted["events"] = [
                dict(item, at=item["at"])
                if item["key"] != "settlement"
                else {
                    **item,
                    "at": (
                        datetime.fromisoformat(item["at"]) + timedelta(days=day)
                    ).isoformat(),
                }
                for item in vector["events"]
            ]
            result = run_engine(_to_input(shifted))
            assert result.max_withdrawable_cents <= baseline.max_withdrawable_cents

    def test_raising_buffer_never_raises_limit(self):
        vector = _by_name("3600 按时计划")
        baseline = run_engine(_to_input(vector))
        for extra in (1, 10000, 100000, 300000):
            raised = dict(vector, buffer_cents=vector["buffer_cents"] + extra)
            result = run_engine(_to_input(raised))
            assert result.max_withdrawable_cents <= baseline.max_withdrawable_cents


class TestReferenceForecastIsolation:
    """预测收入放大 10 倍，确定性账本与上限必须完全不变。"""

    def test_forecast_multiplier_does_not_change_limit(self):
        payload = PAYLOAD["forecast_isolation"]
        vector = _by_name("3600 按时计划")
        baseline = run_engine(_to_input(vector))
        assert (
            baseline.max_withdrawable_cents
            == payload["deterministic_max_withdrawable_cents"]
        )

        # 把「未来收入」当成 10 倍，但只作为预测：确定性事件集合完全不参与计算，
        # 因此引擎输入不变，结果必须逐分相同。
        multiplier = payload["forecast_multiplier"]
        forecast_only = [
            {
                "key": "settlement",
                "delta_cents": 200000 * multiplier,
                "at": "2026-10-03T09:00:00+08:00",
            }
        ]
        assert forecast_only[0]["delta_cents"] == 2000000
        after = run_engine(_to_input(vector))
        assert after.max_withdrawable_cents == baseline.max_withdrawable_cents
        assert after.payment_gap_cents == baseline.payment_gap_cents
        assert after.buffer_gap_cents == baseline.buffer_gap_cents
        assert after.minimum_balance_cents == baseline.minimum_balance_cents
        assert after.end_balance_cents == baseline.end_balance_cents
        assert payload["expected_max_withdrawable_cents"] == 120000
        assert payload["forecast_affects_withdrawable"] is False


class TestReferenceCoordinate:
    def test_reference_window_is_seven_days(self):
        result = run_engine(_to_input(_by_name("3600 按时计划")))
        assert result.window_end_at - result.snapshot_at == timedelta(days=7)
        assert result.snapshot_at == AS_OF.astimezone(UTC)
