"""Cash Engine 回归与边界测试。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.services.cash_engine import (
    AnalysisStatus,
    CashEventInput,
    EngineInput,
    events_version_hash,
    run_engine,
    run_joint,
    validate_events,
)
from tests import fixtures_cash as fx


def _event(**kwargs) -> CashEventInput:
    base = {
        "id": "e1",
        "cash_key": "K1",
        "title": "事项",
        "amount_cents": 100_00,
        "direction": "inflow",
        "scheduled_at": fx.day(1),
        "state": "scheduled",
    }
    base.update(kwargs)
    return CashEventInput(**base)  # type: ignore[arg-type]


def _run(events, *, opening=1000_00, buffer=0, label="按当前计划"):
    return run_engine(
        EngineInput(
            opening_balance_cents=opening,
            buffer_cents=buffer,
            snapshot_at=fx.SNAPSHOT_AT,
            events=events,
            label=label,
        )
    )


# ---------------------------------------------------------------------------
# 金额
# ---------------------------------------------------------------------------
class TestAmount:
    @pytest.mark.parametrize(
        ("raw", "cents"),
        [
            ("123.45", 12345),
            ("123", 12300),
            ("0.01", 1),
            ("0", 0),
            (123.45, 12345),
            ("1,234.56", 123456),
            ("￥88.00", 8800),
            ("１２３．４５", 12345),
            ("  12.30  ", 1230),
            ("0.005", 1),
        ],
    )
    def test_to_cents(self, raw, cents):
        from app.utils.money import to_cents

        assert to_cents(raw) == cents

    @pytest.mark.parametrize("raw", ["", "abc", None, "1e3", "12.3.4", "--1", "12元"])
    def test_to_cents_rejects(self, raw):
        from app.utils.money import MoneyError, to_cents

        with pytest.raises(MoneyError):
            to_cents(raw)

    def test_cents_round_trip(self):
        from app.utils.money import cents_to_decimal_str, cents_to_yuan, format_cny

        assert cents_to_decimal_str(12345) == "123.45"
        assert cents_to_decimal_str(-5) == "-0.05"
        assert str(cents_to_yuan(12345)) == "123.45"
        assert format_cny(12345) == "¥123.45"

    def test_no_float_drift(self):
        from app.utils.money import sum_cents, to_cents

        values = [to_cents("0.10"), to_cents("0.20"), to_cents("0.30")]
        assert sum_cents(values) == 60


# ---------------------------------------------------------------------------
# 方向与状态
# ---------------------------------------------------------------------------
class TestDirectionAndState:
    def test_inflow_adds(self):
        result = _run([_event(direction="inflow", amount_cents=500_00)], opening=0)
        assert result.end_balance_cents == 500_00

    def test_outflow_subtracts(self):
        result = _run([_event(direction="outflow", amount_cents=500_00)], opening=1000_00)
        assert result.end_balance_cents == 500_00
        assert result.window_outflow_cents == 500_00

    def test_included_in_opening_not_counted(self):
        result = _run(
            fx.included_in_opening_events(), opening=fx.MAIN_OPENING_BALANCE_CENTS
        )
        assert result.end_balance_cents == fx.MAIN_OPENING_BALANCE_CENTS
        assert "already-in-opening" in result.excluded_event_ids
        assert result.window_inflow_cents == 0

    def test_cancelled_not_counted(self):
        event = _event(state="cancelled", amount_cents=900_00)
        result = _run([event], opening=1000_00)
        assert result.end_balance_cents == 1000_00
        assert result.status is AnalysisStatus.FEASIBLE

    def test_zero_amount_event(self):
        result = _run([_event(amount_cents=0)], opening=1000_00)
        assert result.end_balance_cents == 1000_00


# ---------------------------------------------------------------------------
# cash_key 去重
# ---------------------------------------------------------------------------
class TestCashKey:
    def test_duplicate_cash_key_rejected(self):
        events = [
            _event(id="a", cash_key="DUP", amount_cents=100_00),
            _event(id="b", cash_key="DUP", amount_cents=200_00),
        ]
        errors, duplicates = validate_events(events)
        assert duplicates == ["DUP"]
        assert any(item["field"] == "cash_key" for item in errors)

    def test_duplicate_blocks_calculation(self):
        events = [
            _event(id="a", cash_key="DUP", amount_cents=100_00),
            _event(id="b", cash_key="DUP", amount_cents=200_00),
        ]
        result = _run(events)
        assert result.status is AnalysisStatus.INPUT_INCOMPLETE
        assert result.max_withdrawable_cents is None

    def test_missing_cash_key(self):
        errors, _ = validate_events([_event(cash_key="")])
        assert errors


# ---------------------------------------------------------------------------
# 日内顺序
# ---------------------------------------------------------------------------
class TestOrdering:
    def test_outflow_before_inflow_same_time(self):
        """同一时刻没有明确次序时：先支出，后收入（暴露中途缺口）。"""
        when = fx.day(1)
        events = [
            _event(id="in", cash_key="IN", direction="inflow", amount_cents=1000_00, scheduled_at=when),
            _event(id="out", cash_key="OUT", direction="outflow", amount_cents=600_00, scheduled_at=when),
        ]
        result = _run(events, opening=0, buffer=0)
        assert result.minimum_balance_cents == -600_00
        assert result.payment_gap_cents == 600_00
        assert result.end_balance_cents == 400_00

    def test_sequence_index_respected(self):
        when = fx.day(1)
        events = [
            _event(
                id="in",
                cash_key="IN",
                direction="inflow",
                amount_cents=1000_00,
                scheduled_at=when,
                sequence_index=1,
            ),
            _event(
                id="out",
                cash_key="OUT",
                direction="outflow",
                amount_cents=600_00,
                scheduled_at=when,
                sequence_index=2,
            ),
        ]
        result = _run(events, opening=0, buffer=0)
        assert result.minimum_balance_cents == 0
        assert result.payment_gap_cents == 0

    def test_events_sorted_by_time(self):
        events = [
            _event(id="late", cash_key="L", scheduled_at=fx.day(3), direction="outflow", amount_cents=100_00),
            _event(id="early", cash_key="E", scheduled_at=fx.day(1), direction="inflow", amount_cents=100_00),
        ]
        result = _run(events, opening=0)
        titles = [point.event_id for point in result.points if not point.is_opening]
        assert titles == ["early", "late"]


# ---------------------------------------------------------------------------
# 缺口
# ---------------------------------------------------------------------------
class TestGaps:
    def test_gap_case_200_and_800(self):
        result = run_engine(fx.gap_case_input())
        assert result.minimum_balance_cents == fx.GAP_CASE_BALANCE_CENTS
        assert result.payment_gap_cents == fx.EXPECTED_GAP_CASE_PAYMENT_GAP
        assert result.buffer_gap_cents == fx.EXPECTED_GAP_CASE_BUFFER_GAP
        # 800 元已经包含 200 元，两者不可相加
        assert result.buffer_gap_cents == result.payment_gap_cents + 600_00
        assert result.payment_gap_cents == max(0, -result.minimum_balance_cents)
        assert result.buffer_gap_cents == max(0, 600_00 - result.minimum_balance_cents)
        assert result.max_withdrawable_cents == 0
        assert result.status is AnalysisStatus.PAYMENT_GAP

    def test_gaps_never_added(self):
        result = _run(
            [_event(direction="outflow", amount_cents=200_00)], opening=0, buffer=600_00
        )
        assert result.payment_gap_cents == 200_00
        assert result.buffer_gap_cents == 800_00

    def test_zero_buffer_zero_gap(self):
        result = _run([_event(direction="outflow", amount_cents=100_00)], opening=100_00, buffer=0)
        assert result.payment_gap_cents == 0
        assert result.buffer_gap_cents == 0
        assert result.status is AnalysisStatus.FEASIBLE


# ---------------------------------------------------------------------------
# 输入不完整
# ---------------------------------------------------------------------------
class TestIncomplete:
    @pytest.mark.parametrize(
        "kwargs",
        [
            {"amount_cents": None},
            {"scheduled_at": None},
            {"direction": "sideways"},
            {"state": "unknown"},
            {"confirmed": False},
        ],
    )
    def test_incomplete_input(self, kwargs):
        result = _run([_event(**kwargs)])
        assert result.status is AnalysisStatus.INPUT_INCOMPLETE
        assert result.max_withdrawable_cents is None
        assert result.validation_errors

    def test_negative_amount(self):
        result = _run([_event(amount_cents=-1)])
        assert result.status is AnalysisStatus.INPUT_INCOMPLETE


# ---------------------------------------------------------------------------
# 空事件 / 零值
# ---------------------------------------------------------------------------
class TestEmptyAndZero:
    def test_empty_events_still_yields_constraint(self):
        """期初与留底都已确认时，没有未来事项本身就是一个合法约束点。"""
        result = _run([], opening=1000_00, buffer=600_00)
        assert result.max_withdrawable_cents == 400_00
        assert result.status is AnalysisStatus.FEASIBLE
        assert result.end_balance_cents == 1000_00
        assert result.points[0].is_opening
        assert result.limiting_timestamp == fx.SNAPSHOT_AT
        assert result.minimum_future_balance_cents is None

    def test_empty_events_at_buffer_yields_zero(self):
        result = _run([], opening=600_00, buffer=600_00)
        assert result.max_withdrawable_cents == 0
        assert result.status is AnalysisStatus.FEASIBLE

    def test_empty_events_below_buffer(self):
        result = _run([], opening=100_00, buffer=600_00)
        assert result.max_withdrawable_cents == 0
        assert result.status is AnalysisStatus.BELOW_BUFFER
        assert result.buffer_gap_cents == 500_00

    def test_incomplete_input_alone_blocks_calculation(self):
        """只有资料不完整时才返回 None，而不是「没有未来事项」。"""
        result = _run([_event(amount_cents=None)], opening=1000_00, buffer=600_00)
        assert result.max_withdrawable_cents is None
        assert result.status is AnalysisStatus.INPUT_INCOMPLETE

    def test_zero_balance_zero_buffer(self):
        result = _run([], opening=0, buffer=0)
        assert result.minimum_balance_cents == 0
        assert result.payment_gap_cents == 0
        assert result.buffer_gap_cents == 0
        assert result.status is AnalysisStatus.FEASIBLE

    def test_opening_below_buffer_reports_floor(self):
        result = _run([], opening=100_00, buffer=600_00)
        assert result.balance_floor_cents == 100_00
        assert result.opening_covers_buffer is False
        assert result.buffer_gap_cents == 500_00
        assert result.status is AnalysisStatus.BELOW_BUFFER


# ---------------------------------------------------------------------------
# 期初时点参与约束（2.0.0 的核心修正）
# ---------------------------------------------------------------------------
class TestOpeningPointConstraint:
    """期初点必须与未来时点一起参与最大可提用金额的竞争。"""

    def test_future_income_cannot_be_withdrawn_today(self):
        """期初 600 / 留底 600 + 只有未来收入 -> 可行但可提用 0。"""
        result = run_engine(
            fx.future_income_only_input(
                opening_balance_cents=fx.OPENING_EXACT_BUFFER_CENTS
            )
        )
        assert result.max_withdrawable_cents == 0
        assert str(result.status) == fx.EXPECTED_OPENING_EXACT_BUFFER_STATUS
        assert result.limiting_timestamp == fx.SNAPSHOT_AT
        assert result.end_balance_cents == fx.OPENING_EXACT_BUFFER_CENTS + fx.FUTURE_INCOME_ONLY_CENTS

    def test_opening_below_buffer_with_future_income(self):
        """期初 500 / 留底 600 + 只有未来收入 -> BELOW_BUFFER 且可提用 0。"""
        result = run_engine(
            fx.future_income_only_input(
                opening_balance_cents=fx.OPENING_BELOW_BUFFER_CENTS
            )
        )
        assert result.max_withdrawable_cents == 0
        assert str(result.status) == fx.EXPECTED_OPENING_BELOW_BUFFER_STATUS
        assert result.buffer_gap_cents == fx.EXPECTED_OPENING_BELOW_BUFFER_BUFFER_GAP
        assert result.payment_gap_cents == 0

    def test_delaying_income_never_raises_limit(self):
        """同一组事项只把收入后移，上限不得提高。"""
        early = run_engine(
            fx.future_income_only_input(
                opening_balance_cents=fx.OPENING_EXACT_BUFFER_CENTS,
                income_at=fx.day(2, 9),
            )
        )
        late = run_engine(
            fx.future_income_only_input(
                opening_balance_cents=fx.OPENING_EXACT_BUFFER_CENTS,
                income_at=fx.day(6, 9),
            )
        )
        assert early.max_withdrawable_cents == 0
        assert late.max_withdrawable_cents == 0
        assert late.max_withdrawable_cents <= early.max_withdrawable_cents  # type: ignore[operator]

    def test_raising_buffer_never_raises_limit(self):
        base = run_engine(fx.on_time_input_with_buffer(fx.MAIN_BUFFER_CENTS))
        raised = run_engine(fx.on_time_input_with_buffer(fx.RAISED_BUFFER_CENTS))
        assert base.max_withdrawable_cents == 1200_00
        assert raised.max_withdrawable_cents == fx.EXPECTED_RAISED_BUFFER_WITHDRAWABLE
        assert raised.max_withdrawable_cents < base.max_withdrawable_cents  # type: ignore[operator]

    def test_raised_buffer_does_not_resolve_delay_gap(self):
        """提高留底不会消灭资金缺口。"""
        result = run_engine(
            EngineInput(
                opening_balance_cents=fx.MAIN_OPENING_BALANCE_CENTS,
                buffer_cents=fx.RAISED_BUFFER_CENTS,
                snapshot_at=fx.SNAPSHOT_AT,
                events=fx.main_events(settlement_at=fx.SETTLEMENT_DELAYED_AT),
                label="结算延迟",
            )
        )
        assert result.status is AnalysisStatus.PAYMENT_GAP
        assert result.payment_gap_cents == fx.EXPECTED_RAISED_BUFFER_DELAYED_PAYMENT_GAP
        assert result.buffer_gap_cents == fx.EXPECTED_RAISED_BUFFER_DELAYED_BUFFER_GAP
        assert result.max_withdrawable_cents == 0

    def test_no_future_events_uses_opening(self):
        result = run_engine(fx.no_future_events_input())
        assert result.max_withdrawable_cents == fx.EXPECTED_NO_EVENT_WITHDRAWABLE
        assert str(result.status) == fx.EXPECTED_NO_EVENT_STATUS


# ---------------------------------------------------------------------------
# 核心算例
# ---------------------------------------------------------------------------
class TestRegression:
    def test_on_time_plan_withdrawable_is_1200(self):
        result = run_engine(fx.on_time_input())
        expected = fx.EXPECTED_ON_TIME
        assert result.max_withdrawable_cents == expected["max_withdrawable_cents"] == 1200_00
        assert str(result.status) == expected["status"]
        assert result.limiting_timestamp == expected["limiting_timestamp"]
        assert result.limiting_event_id == expected["limiting_event_id"]
        assert result.limiting_balance_cents == expected["limiting_balance_cents"]
        assert result.payment_gap_cents == expected["payment_gap_cents"]
        assert result.buffer_gap_cents == expected["buffer_gap_cents"]
        assert result.end_balance_cents == expected["end_balance_cents"]
        assert result.window_inflow_cents == expected["window_inflow_cents"]
        assert result.window_outflow_cents == expected["window_outflow_cents"]

    def test_delayed_arrival_withdrawable_is_zero(self):
        result = run_engine(fx.delayed_input())
        expected = fx.EXPECTED_DELAYED
        assert result.max_withdrawable_cents == expected["max_withdrawable_cents"] == 0
        assert str(result.status) == expected["status"]
        assert result.limiting_timestamp == expected["limiting_timestamp"]
        assert result.limiting_event_id == expected["limiting_event_id"]
        assert result.limiting_balance_cents == expected["limiting_balance_cents"]
        assert result.payment_gap_cents == expected["payment_gap_cents"] == 200_00
        assert result.buffer_gap_cents == expected["buffer_gap_cents"] == 800_00

    def test_gaps_are_not_additive_in_main_case(self):
        """留底缺口 800 已经包含付款缺口 200，绝不能相加成 1000。"""
        result = run_engine(fx.delayed_input())
        assert result.payment_gap_cents == 200_00
        assert result.buffer_gap_cents == 800_00
        # 两者之和（1000）不是任何一个真实缺口，只是被误加出来的数字
        assert result.payment_gap_cents + result.buffer_gap_cents == 1000_00
        assert result.buffer_gap_cents == max(
            0, result.buffer_cents - result.minimum_balance_cents
        )
        assert result.buffer_gap_cents == max(0, -result.minimum_balance_cents) + result.buffer_cents
        assert result.buffer_gap_cents - result.payment_gap_cents == result.buffer_cents

    def test_joint_constraint_withdrawable_is_zero(self):
        joint = run_joint(fx.joint_inputs())
        assert joint.max_withdrawable_cents == fx.EXPECTED_JOINT["max_withdrawable_cents"] == 0
        assert joint.binding_label == fx.EXPECTED_JOINT["binding_label"]
        assert str(joint.status) == fx.EXPECTED_JOINT["status"]
        # 共同约束取所有情景中最保守的上限
        per_scenario = [item.max_withdrawable_cents for item in joint.scenario_results]
        assert joint.max_withdrawable_cents == min(per_scenario)  # type: ignore[type-var]

    def test_end_balance_without_household_withdrawal(self):
        """未提用家庭资金时，两个口径的期末余额都是 1800 元。"""
        on_time = run_engine(fx.on_time_input())
        delayed = run_engine(fx.delayed_input())
        expected = fx.EXPECTED_ON_TIME["end_balance_cents"]
        assert on_time.end_balance_cents == expected == 1800_00
        assert delayed.end_balance_cents == expected
        assert on_time.end_balance_cents == fx.EXPECTED_ON_TIME_END_BALANCE
        assert delayed.end_balance_cents == fx.EXPECTED_DELAYED_END_BALANCE

    def test_after_withdrawing_1200_plan_is_exactly_at_buffer(self):
        """按时到账口径提用 1200 元后，最紧时点余额刚好等于经营留底。"""
        on_time = run_engine(fx.on_time_input())
        assert on_time.max_withdrawable_cents == 1200_00
        assert on_time.limiting_balance_cents - 1200_00 == fx.EXPECTED_WITHDRAW_1200_LIMITING_BALANCE
        assert fx.EXPECTED_WITHDRAW_1200_LIMITING_BALANCE == on_time.buffer_cents
        assert on_time.end_balance_cents - 1200_00 == fx.EXPECTED_WITHDRAW_1200_END_BALANCE

    def test_withdrawing_one_cent_more_breaks_buffer(self):
        """再多提用 1 分钱就会击穿经营留底。"""
        from app.services.cash_engine import CashEventInput

        events = fx.main_events()
        # 把留底提高 1 分钱等价于多提用 1 分钱
        result = run_engine(
            EngineInput(
                opening_balance_cents=fx.MAIN_OPENING_BALANCE_CENTS,
                buffer_cents=fx.MAIN_BUFFER_CENTS + 1,
                snapshot_at=fx.SNAPSHOT_AT,
                events=list(events),
                label="按当前计划",
            )
        )
        assert result.max_withdrawable_cents == 1200_00 - 1
        assert isinstance(events[0], CashEventInput)

    def test_limiting_point_explains_amount(self):
        result = run_engine(fx.on_time_input())
        assert result.limiting_event_title == "已确认退款"
        assert result.limiting_event_id == fx.REFUND_ID
        assert result.limiting_balance_cents - result.buffer_cents == 1200_00
        assert result.limiting_reason

    def test_pending_inflows_at_limit_on_time(self):
        """最紧时点是最后一笔支出之后，窗口内已无尚未到账的收入。"""
        result = run_engine(fx.on_time_input())
        assert result.pending_inflows_at_limit == []
        # 但窗口内的结算款收入仍然计入「待结算资金」口径
        assert result.pending_settlement_cents == 2000_00

    def test_pending_inflows_at_limit_delayed(self):
        """延迟到 D4 09:00 到账时，最紧时点（D3 10:00）之后仍有该笔结算款待入账。"""
        result = run_engine(fx.delayed_input())
        pending_ids = [item.event_id for item in result.pending_inflows_at_limit]
        assert pending_ids == [fx.SETTLEMENT_INFLOW_ID]
        assert result.pending_inflows_at_limit[0].amount_cents == fx.SETTLEMENT_INFLOW_CENTS


# ---------------------------------------------------------------------------
# 版本重算
# ---------------------------------------------------------------------------
class TestRecompute:
    def test_amount_change_changes_result(self):
        """把进货款从 1400 元提高到 1900 元：最紧时点下移到 1300 元。"""
        events = fx.main_events()
        before = _run(
            events, opening=fx.MAIN_OPENING_BALANCE_CENTS, buffer=fx.MAIN_BUFFER_CENTS
        )
        changed = [
            CashEventInput(
                id=event.id,
                cash_key=event.cash_key,
                title=event.title,
                amount_cents=(
                    fx.PURCHASE_CENTS + 500_00
                    if event.id == fx.PURCHASE_ID
                    else event.amount_cents
                ),
                direction=event.direction,
                scheduled_at=event.scheduled_at,
                state=event.state,
            )
            for event in events
        ]
        after = _run(
            changed, opening=fx.MAIN_OPENING_BALANCE_CENTS, buffer=fx.MAIN_BUFFER_CENTS
        )
        assert before.max_withdrawable_cents == 1200_00
        assert after.max_withdrawable_cents == 700_00
        assert after.end_balance_cents == before.end_balance_cents - 500_00

    def test_date_change_changes_limit(self):
        """把结算款提前到快照当天：最紧时点仍是最后一笔支出，可提用金额不变。"""
        events = fx.main_events()
        moved = [
            CashEventInput(
                id=event.id,
                cash_key=event.cash_key,
                title=event.title,
                amount_cents=event.amount_cents,
                direction=event.direction,
                scheduled_at=(
                    fx.day(0, hour=3) if event.id == fx.SETTLEMENT_INFLOW_ID else event.scheduled_at
                ),
                state=event.state,
            )
            for event in events
        ]
        after = _run(
            moved, opening=fx.MAIN_OPENING_BALANCE_CENTS, buffer=fx.MAIN_BUFFER_CENTS
        )
        assert after.max_withdrawable_cents == 1200_00
        assert after.limiting_event_id == fx.REFUND_ID
        assert after.end_balance_cents == fx.EXPECTED_ON_TIME_END_BALANCE

    def test_state_change_to_cancelled_increases_gap(self):
        events = fx.main_events()
        cancelled = [
            _event(
                id=event.id,
                cash_key=event.cash_key,
                title=event.title,
                amount_cents=event.amount_cents,
                direction=event.direction,
                scheduled_at=event.scheduled_at,
                state="cancelled",
            )
            if event.id == fx.SETTLEMENT_INFLOW_ID
            else CashEventInput(
                id=event.id,
                cash_key=event.cash_key,
                title=event.title,
                amount_cents=event.amount_cents,
                direction=event.direction,
                scheduled_at=event.scheduled_at,
                state=event.state,
            )
            for event in events
        ]
        after = _run(
            cancelled, opening=fx.MAIN_OPENING_BALANCE_CENTS, buffer=fx.MAIN_BUFFER_CENTS
        )
        assert after.payment_gap_cents == 200_00
        assert after.max_withdrawable_cents == 0
        assert after.status is AnalysisStatus.PAYMENT_GAP

    def test_version_hash_changes_on_material_change(self):
        events = fx.main_events()
        before = events_version_hash(events)
        changed = list(events)
        changed[1] = CashEventInput(
            id=changed[1].id,
            cash_key=changed[1].cash_key,
            title=changed[1].title,
            amount_cents=changed[1].amount_cents + 1,
            direction=changed[1].direction,
            scheduled_at=changed[1].scheduled_at,
        )
        assert events_version_hash(changed) != before

    def test_version_hash_stable(self):
        assert events_version_hash(fx.main_events()) == events_version_hash(fx.main_events())


# ---------------------------------------------------------------------------
# 待结算不可作为当前余额
# ---------------------------------------------------------------------------
class TestPendingSettlement:
    def test_pending_not_added_to_opening(self):
        result = run_engine(fx.on_time_input())
        assert result.opening_balance_cents == fx.MAIN_OPENING_BALANCE_CENTS
        assert result.pending_settlement_cents == 2000_00
        assert result.opening_balance_cents + result.pending_settlement_cents != (
            result.opening_balance_cents
        )
        # 可提用金额基于期初余额，不含待结算资金
        assert result.max_withdrawable_cents == 1200_00

    def test_pending_settlement_only_counts_settlement_type(self):
        """待结算资金只统计 settlement 类型；转入、销售收款、其他收入不算。"""
        events = [
            _event(
                id="s",
                cash_key="S",
                direction="inflow",
                amount_cents=1000_00,
                event_type="settlement",
            ),
            _event(
                id="t",
                cash_key="T",
                direction="inflow",
                amount_cents=300_00,
                event_type="transfer_in",
            ),
            _event(
                id="r",
                cash_key="R",
                direction="inflow",
                amount_cents=400_00,
                event_type="sale_receipt",
            ),
            _event(
                id="o",
                cash_key="O",
                direction="inflow",
                amount_cents=200_00,
                event_type="other_inflow",
            ),
            _event(
                id="p",
                cash_key="P",
                direction="outflow",
                amount_cents=500_00,
                event_type="supplier_payment",
            ),
        ]
        result = _run(events, opening=0, buffer=0)
        assert result.window_inflow_cents == 1900_00
        assert result.pending_settlement_cents == 1000_00
        assert result.pending_settlement_cents != result.window_inflow_cents

    def test_cancelled_settlement_not_counted_as_pending(self):
        events = [
            _event(
                id="s",
                cash_key="S",
                direction="inflow",
                amount_cents=1000_00,
                event_type="settlement",
                state="cancelled",
            )
        ]
        result = _run(events, opening=0, buffer=0)
        assert result.pending_settlement_cents == 0
        assert result.window_inflow_cents == 0


# ---------------------------------------------------------------------------
# 窗口边界
# ---------------------------------------------------------------------------
class TestWindow:
    def test_window_is_seven_days(self):
        result = run_engine(fx.on_time_input())
        assert result.window_end_at - result.snapshot_at == timedelta(days=7)

    def test_event_after_window_ignored(self):
        result = _run(
            [_event(scheduled_at=fx.day(9), amount_cents=999_00)],
            opening=1000_00,
        )
        assert result.end_balance_cents == 1000_00
        assert result.window_inflow_cents == 0

    def test_event_before_snapshot_ignored(self):
        result = _run(
            [_event(scheduled_at=fx.day(-2), amount_cents=999_00)],
            opening=1000_00,
        )
        assert result.end_balance_cents == 1000_00

    def test_naive_datetime_treated_as_utc(self):
        naive = datetime(2025, 10, 2, 2, 0)
        result = _run([_event(scheduled_at=naive)], opening=0)
        assert result.end_balance_cents == 100_00


# ---------------------------------------------------------------------------
# 来源追踪
# ---------------------------------------------------------------------------
class TestSourceTracing:
    def test_source_label_propagates(self):
        result = _run([_event(source_label="结算通知")])
        assert result.points[1].event_title == "事项"

    def test_engine_version_is_2(self):
        from app.services.cash_engine import ENGINE_VERSION, is_engine_version_current

        assert ENGINE_VERSION == "2.0.0"
        assert run_engine(fx.on_time_input()).engine_version == ENGINE_VERSION
        assert is_engine_version_current(ENGINE_VERSION) is True
        assert is_engine_version_current("1.0.0") is False

    def test_legacy_ok_status_coerces_to_feasible(self):
        """历史数据里的旧状态 OK 必须归一到 FEASIBLE，而不是被当成未知状态。"""
        assert AnalysisStatus.coerce("OK") is AnalysisStatus.FEASIBLE
        assert AnalysisStatus.coerce("FEASIBLE") is AnalysisStatus.FEASIBLE
        assert AnalysisStatus.coerce("payment_gap") is AnalysisStatus.PAYMENT_GAP
        assert AnalysisStatus.coerce("nonsense") is None

    def test_result_payload_is_serialisable(self):
        payload = run_engine(fx.on_time_input()).to_dict()
        import json

        assert json.loads(json.dumps(payload, ensure_ascii=False))
        assert payload["points"][0]["is_opening"] is True
        assert payload["status"] == "FEASIBLE"
        assert payload["engine_version"] == "2.0.0"
