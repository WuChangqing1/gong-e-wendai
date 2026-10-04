"""Joint 结果一致性专项回归。

对应产品口径要求：

* 共同约束必须绑定**真正最坏**的情景，而不是只比 ``max_withdrawable_cents``
  （多个不可行情景的上限都会被截断为 0，比最小值等于随机取第一个）。
* 顶层每个字段都必须来自同一个绑定情景，不能拼装。
* 同一份结果要能原样穿过 API → 数据库 → 分享预览 → 家庭成员。

用例编号与需求文档一致：

* Case A：标准 3600 算例（按当前计划 FEASIBLE/1200，延期 PAYMENT_GAP/200+800）
* Case B：单情景不可行时，共同约束必须沿用该情景的缺口（400）
* Case C：两个情景都不可行时，必须绑定缺口更大的那个（2800）
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.services.cash_engine import (
    AnalysisStatus,
    CashEventInput,
    EngineInput,
    resolve_analysis,
    run_engine,
    run_joint,
    scenario_severity_key,
)
from tests import fixtures_cash as fx

BASE = datetime(2025, 10, 1, 0, 0, tzinfo=UTC)
OPENING = 3600_00
BUFFER = 600_00


def _event(
    key: str,
    *,
    direction: str,
    amount_cents: int,
    at: datetime,
    event_type: str = "supplier_payment",
) -> CashEventInput:
    return CashEventInput(
        id=f"evt-{key}",
        cash_key=key,
        title=key,
        direction=direction,
        amount_cents=amount_cents,
        scheduled_at=at,
        state="scheduled",
        event_type=event_type,
        confirmed=True,
    )


def _input(label: str, events: list[CashEventInput], *, opening: int = OPENING) -> EngineInput:
    return EngineInput(
        opening_balance_cents=opening,
        buffer_cents=BUFFER,
        snapshot_at=BASE,
        events=events,
        label=label,
    )


# ---------------------------------------------------------------------------
# Case C 的两组数据：两个情景都不可行，缺口分别为 800 与 2800
# ---------------------------------------------------------------------------
def _double_gap_inputs() -> list[EngineInput]:
    """构造两个都触发 PAYMENT_GAP 的情景，缺口 800 与 2800。

    期初 3600、留底 600。两个情景各有一笔大额支出，且没有任何收入：
    * 情景一：支出 4400 → 最低余额 −800 → 付款缺口 800
    * 情景二：支出 6400 → 最低余额 −2800 → 付款缺口 2800
    """
    mild = _input(
        "计划",
        [
            _event(
                "PAY-MILD",
                direction="outflow",
                amount_cents=4400_00,
                at=BASE + timedelta(days=1, hours=8),
            )
        ],
    )
    severe = _input(
        "延期",
        [
            _event(
                "PAY-SEVERE",
                direction="outflow",
                amount_cents=6400_00,
                at=BASE + timedelta(days=1, hours=8),
            )
        ],
    )
    return [mild, severe]


def test_double_gap_case_gaps_are_as_designed():
    """先确认构造的两个情景缺口确实是 800 与 2800。"""
    mild, severe = _double_gap_inputs()
    mild_result = run_engine(mild)
    severe_result = run_engine(severe)

    assert mild_result.status is AnalysisStatus.PAYMENT_GAP
    assert mild_result.payment_gap_cents == 800_00

    assert severe_result.status is AnalysisStatus.PAYMENT_GAP
    assert severe_result.payment_gap_cents == 2800_00


class TestJointBindsWorstScenario:
    """第七条：JointResult 必须有明确 binding 且字段不得拼装。"""

    def test_two_payment_gaps_bind_the_larger_gap(self):
        """Case C：两个都 PAYMENT_GAP → 绑定缺口 2800 的那个（延期情景）。"""
        joint = run_joint(_double_gap_inputs())

        assert str(joint.status) == "PAYMENT_GAP"
        assert joint.payment_gap_cents_effective == 2800_00
        assert joint.binding_scenario_index == 1
        assert joint.binding_label == "延期"
        assert joint.binding is not None
        assert joint.binding.payment_gap_cents == 2800_00
        # 上限一律为 0（两个情景都不可行）
        assert joint.max_withdrawable_cents == 0

    def test_gap_comparison_is_not_truncated_by_zero_withdrawable(self):
        """回归要点：只比 max_withdrawable 会两个都是 0，无法区分严重程度。"""
        results = [run_engine(item) for item in _double_gap_inputs()]
        # 两者的可提用上限完全相同，都不能用来区分
        assert [item.max_withdrawable_cents for item in results] == [0, 0]
        # 真正的严重程度差异在缺口上
        assert results[0].payment_gap_cents != results[1].payment_gap_cents
        # 严重度键直接把更严重的情景排在前
        keys = [scenario_severity_key(item) for item in results]
        assert keys[1] < keys[0]

    def test_input_order_does_not_change_binding(self):
        """结果相同或顺序变化时，绑定必须仍然指向真正最坏的情景。"""
        mild, severe = _double_gap_inputs()
        reversed_joint = run_joint([severe, mild])

        assert reversed_joint.binding_scenario_index == 0
        assert reversed_joint.binding_label == "延期"
        assert reversed_joint.payment_gap_cents_effective == 2800_00

    def test_tie_keeps_input_order(self):
        """严重度完全相同时保持输入顺序（稳定）。"""
        a = _input(
            "A",
            [
                _event(
                    "PAY-A",
                    direction="outflow",
                    amount_cents=4400_00,
                    at=BASE + timedelta(days=1, hours=8),
                )
            ],
        )
        b = _input(
            "B",
            [
                _event(
                    "PAY-B",
                    direction="outflow",
                    amount_cents=4400_00,
                    at=BASE + timedelta(days=1, hours=8),
                )
            ],
        )
        joint = run_joint([a, b])
        assert joint.binding_scenario_index == 0
        assert joint.binding_label == "A"


class TestResolvedAnalysisConsistency:
    """第八条：唯一 ResolvedAnalysis，所有字段来自同一绑定情景。"""

    def test_double_gap_resolved_binds_same_scenario(self):
        inputs = _double_gap_inputs()
        joint = run_joint(inputs)
        resolved = resolve_analysis(
            mode="joint", engine_results=joint.scenario_results, joint=joint
        )

        assert str(resolved.status) == "PAYMENT_GAP"
        assert resolved.max_withdrawable_cents == 0
        assert resolved.payment_gap_cents == 2800_00
        assert resolved.binding_scenario_index == 1
        assert resolved.binding_label == "延期"

        # 关键：每个标量字段都能在绑定情景上找到同一个值
        binding = joint.scenario_results[1]
        assert resolved.payment_gap_cents == binding.payment_gap_cents
        assert resolved.buffer_gap_cents == binding.buffer_gap_cents
        assert resolved.limiting_timestamp == binding.limiting_timestamp
        assert resolved.limiting_balance_cents == binding.limiting_balance_cents
        assert resolved.limiting_event_id == binding.limiting_event_id
        assert resolved.end_balance_cents == binding.end_balance_cents
        assert resolved.minimum_balance_cents == binding.minimum_balance_cents
        assert resolved.pending_inflows_at_limit == binding.pending_inflows_at_limit
        assert resolved.points == binding.points

    def test_no_field_comes_from_the_other_scenario(self):
        """反向断言：不能出现「状态来自一个、限制时点来自另一个」的拼装。"""
        inputs = _double_gap_inputs()
        joint = run_joint(inputs)
        resolved = resolve_analysis(
            mode="joint", engine_results=joint.scenario_results, joint=joint
        )
        other = joint.scenario_results[0]

        assert resolved.payment_gap_cents != other.payment_gap_cents
        assert resolved.limiting_event_id != other.limiting_event_id
        assert resolved.points != other.points

    def test_joint_reason_explains_shared_constraint(self):
        inputs = _double_gap_inputs()
        joint = run_joint(inputs)
        resolved = resolve_analysis(
            mode="joint", engine_results=joint.scenario_results, joint=joint
        )
        assert "同时考虑这些情况后" in resolved.limiting_reason
        assert "不代表资金安排可行" in resolved.limiting_reason

    def test_single_scenario_mode_uses_that_scenario(self):
        result = run_engine(fx.delayed_input())
        resolved = resolve_analysis(mode="delayed", engine_results=[result])

        assert resolved.binding_scenario_index == 0
        assert str(resolved.status) == "PAYMENT_GAP"
        assert resolved.payment_gap_cents == 200_00
        assert resolved.buffer_gap_cents == 800_00
        assert resolved.limiting_timestamp == result.limiting_timestamp
        # 单情景沿用引擎自己的文案，不套用共同约束措辞
        assert "同时考虑这些情况后" not in resolved.limiting_reason


class TestStandardCases:
    """Case A / Case B：标准算例与单情景不可行的共同约束。"""

    def test_case_a_standard_regression(self):
        on_time = run_engine(fx.on_time_input())
        delayed = run_engine(fx.delayed_input())

        assert on_time.max_withdrawable_cents == 1200_00
        assert str(on_time.status) == "FEASIBLE"

        assert str(delayed.status) == "PAYMENT_GAP"
        assert delayed.max_withdrawable_cents == 0
        assert delayed.payment_gap_cents == 200_00
        assert delayed.buffer_gap_cents == 800_00

    def test_case_a_joint_binds_delayed_scenario(self):
        inputs = fx.joint_inputs()
        joint = run_joint(inputs)
        resolved = resolve_analysis(
            mode="joint", engine_results=joint.scenario_results, joint=joint
        )

        assert joint.binding_scenario_index == 1
        assert str(resolved.status) == "PAYMENT_GAP"
        assert resolved.max_withdrawable_cents == 0
        assert resolved.payment_gap_cents == 200_00
        assert resolved.buffer_gap_cents == 800_00

    def test_case_b_single_infeasible_scenario_gap_is_propagated(self):
        """Case B：一个情景不可行时，共同约束必须完整沿用它的缺口。

        期初 3000、留底 600；D1 先付 4400 货款 → 余额 −1400（付款缺口 1400），
        D3 才收到 5000 结算款。共同约束下用户看到的状态、缺口、限制时点
        必须全部来自这个情景，而不是被截断成 0 或来自另一个情景。
        """
        events = [
            _event(
                "PAY-BIG",
                direction="outflow",
                amount_cents=4400_00,
                at=BASE + timedelta(days=1, hours=8),
            ),
            _event(
                "SETTLE-LATE",
                direction="inflow",
                amount_cents=5000_00,
                at=BASE + timedelta(days=3, hours=10),
                event_type="settlement",
            ),
        ]
        inputs = [
            _input("按当前计划", events, opening=3000_00),
            _input("结算延迟", events, opening=3000_00),
        ]
        joint = run_joint(inputs)
        resolved = resolve_analysis(
            mode="joint", engine_results=joint.scenario_results, joint=joint
        )

        assert str(resolved.status) == "PAYMENT_GAP"
        assert resolved.payment_gap_cents == 1400_00
        assert resolved.buffer_gap_cents == 2000_00
        assert resolved.max_withdrawable_cents == 0
        # 缺口与限制点必须与绑定情景完全一致
        binding = joint.binding
        assert binding is not None
        assert resolved.payment_gap_cents == binding.payment_gap_cents
        assert resolved.buffer_gap_cents == binding.buffer_gap_cents
        assert resolved.limiting_timestamp == binding.limiting_timestamp
        assert resolved.limiting_event_id == binding.limiting_event_id


class TestSeverityOrdering:
    """第六条：状态严重度必须按 INPUT_INCOMPLETE > PAYMENT_GAP > BELOW_BUFFER > FEASIBLE。"""

    def test_status_severity_order(self):
        incomplete = run_engine(
            EngineInput(
                opening_balance_cents=OPENING,
                buffer_cents=BUFFER,
                snapshot_at=BASE,
                events=[
                    CashEventInput(
                        id="bad",
                        cash_key="bad",
                        title="缺金额",
                        direction="outflow",
                        amount_cents=None,  # type: ignore[arg-type]
                        scheduled_at=BASE + timedelta(days=1),
                        state="scheduled",
                        event_type="supplier_payment",
                        confirmed=True,
                    )
                ],
                label="资料不全",
            )
        )
        payment_gap = run_engine(_double_gap_inputs()[0])
        # 期初低于留底但非负：能付款，只是跌破留底
        below_buffer = run_engine(_input("低于留底", [], opening=500_00))
        feasible = run_engine(fx.on_time_input())

        assert below_buffer.status is AnalysisStatus.BELOW_BUFFER
        assert str(payment_gap.status) == "PAYMENT_GAP"

        keys = [
            scenario_severity_key(item)
            for item in (incomplete, payment_gap, below_buffer, feasible)
        ]
        # 排序键越小越严重，因此顺序应为 资料不全 → 付款缺口 → 低于留底 → 可行
        assert keys == sorted(keys)

        joint = run_joint(
            [
                _input("可行", [], opening=OPENING),
                _input(
                    "低于留底",
                    [],
                    opening=BUFFER - 100_00,
                ),
            ]
        )
        assert str(joint.status) == "BELOW_BUFFER"
        assert joint.binding_scenario_index == 1

    def test_below_buffer_prefers_larger_buffer_gap(self):
        # 期初必须仍为非负，否则状态会升级为 PAYMENT_GAP
        shallow = run_engine(_input("浅", [], opening=550_00))
        deep = run_engine(_input("深", [], opening=510_00))
        assert shallow.status is AnalysisStatus.BELOW_BUFFER
        assert deep.status is AnalysisStatus.BELOW_BUFFER
        assert shallow.buffer_gap_cents == 50_00
        assert deep.buffer_gap_cents == 90_00

        joint = run_joint(
            [
                _input("浅", [], opening=550_00),
                _input("深", [], opening=510_00),
            ]
        )
        assert str(joint.status) == "BELOW_BUFFER"
        assert joint.binding_scenario_index == 1
        assert joint.binding is not None
        assert joint.binding.buffer_gap_cents == deep.buffer_gap_cents
        assert joint.binding.buffer_gap_cents > shallow.buffer_gap_cents

    def test_feasible_prefers_smaller_withdrawable(self):
        joint = run_joint(
            [
                _input("宽松", [], opening=OPENING),
                _input("更紧", [], opening=1000_00),
            ]
        )
        assert str(joint.status) == "FEASIBLE"
        assert joint.binding_scenario_index == 1
        assert joint.max_withdrawable_cents == 400_00
