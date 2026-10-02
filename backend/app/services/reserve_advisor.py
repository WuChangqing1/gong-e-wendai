"""经营留底建议（reserve advisor）。

算法要点
--------
根据滚动预测误差，计算**每个 7 天窗口内最大累计不利误差**，而不是只看第 7 天
最终误差。例：

    第 1 天不利误差 +200，第 2 天 -200
    累计误差序列 = [200, 0]
    风险压力 = 200（只看期末误差 0 会漏掉中途风险）

命名边界
--------
这是**透明的经验启发式**，不是：

* Miller–Orr 最优控制界限
* 置信区间
* VaR / CVaR
* 未来覆盖概率保证

建议取值
--------
``suggested_reserve = max(当前留底, 经验 q=0.9 向上取整到 100 元)``

**不得自动降低当前经营留底**；建议不自动生效，必须由用户明确确认。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from app.models.enhancement import RESERVE_MIN_BLOCKS
from app.services.forecast_engine import (
    BacktestFold,
    BacktestResult,
    ForecastError,
    quantile_cents,
)

DEFAULT_RESERVE_QUANTILE = 0.9
DEFAULT_ROUNDING_CENTS = 10_000  # 100 元

DISCLOSURE = (
    "滚动误差窗口相互重叠，样本经验分位数不是未来覆盖保证。"
    "建议不自动生效，不自动降低既有留底，也不额外叠加第二份同类误差缓冲。"
)


@dataclass(frozen=True, slots=True)
class ErrorBlock:
    """一个 7 天误差窗口内的风险压力。"""

    origin: int
    training_end: Any
    stress_cents: int
    prefix_errors: tuple[int, ...]
    source_refs: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "origin": self.origin,
            "training_end": self.training_end.isoformat()
            if hasattr(self.training_end, "isoformat")
            else str(self.training_end),
            "stress_cents": self.stress_cents,
            "prefix_errors": list(self.prefix_errors),
            "source_refs": list(self.source_refs),
        }


@dataclass(frozen=True, slots=True)
class ReserveAdvice:
    """留底建议。``requires_confirmation`` 恒为 True。"""

    status: str
    current_reserve_cents: int
    suggested_reserve_cents: int
    extra_cents: int
    empirical_error_cents: int | None
    block_count: int
    quantile: float
    rounding_cents: int
    min_blocks: int
    basis_hash: str = ""
    source_refs: tuple[str, ...] = ()
    disclosure: str = DISCLOSURE
    requires_confirmation: bool = True

    @property
    def has_suggestion(self) -> bool:
        return self.status == "SUGGESTION"

    @property
    def suggests_increase(self) -> bool:
        return self.suggested_reserve_cents > self.current_reserve_cents

    @property
    def headline(self) -> str:
        if not self.has_suggestion:
            return (
                f"历史经营数据还不够形成留底建议"
                f"（已有 {self.block_count} 个完整评估窗口，至少需要 {self.min_blocks} 个）"
            )
        if not self.suggests_increase:
            return "当前经营留底已经足够覆盖历史经验误差，建议保持不变"
        return (
            f"建议把经营留底从 {_yuan(self.current_reserve_cents)} "
            f"提高到 {_yuan(self.suggested_reserve_cents)}"
        )

    def basis_dict(self) -> dict[str, Any]:
        """「为什么建议这个金额」所需的依据摘要。"""
        return {
            "status": self.status,
            "current_reserve_cents": self.current_reserve_cents,
            "suggested_reserve_cents": self.suggested_reserve_cents,
            "extra_cents": self.extra_cents,
            "empirical_error_cents": self.empirical_error_cents,
            "empirical_error_text": (
                None
                if self.empirical_error_cents is None
                else _yuan(self.empirical_error_cents)
            ),
            "block_count": self.block_count,
            "min_blocks": self.min_blocks,
            "quantile": self.quantile,
            "rounding_cents": self.rounding_cents,
            "rounding_text": _yuan(self.rounding_cents),
            "source_count": len(self.source_refs),
            "source_refs": list(self.source_refs),
            "disclosure": self.disclosure,
            "requires_confirmation": True,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "current_reserve_cents": self.current_reserve_cents,
            "suggested_reserve_cents": self.suggested_reserve_cents,
            "extra_cents": self.extra_cents,
            "empirical_error_cents": self.empirical_error_cents,
            "block_count": self.block_count,
            "quantile": self.quantile,
            "rounding_cents": self.rounding_cents,
            "min_blocks": self.min_blocks,
            "basis_hash": self.basis_hash,
            "source_refs": list(self.source_refs),
            "disclosure": self.disclosure,
            "requires_confirmation": True,
            "has_suggestion": self.has_suggestion,
            "suggests_increase": self.suggests_increase,
            "headline": self.headline,
        }


def _yuan(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    value = abs(int(cents))
    return f"{sign}¥{value // 100}.{value % 100:02d}"


def buffer_blocks(
    income_backtest: BacktestResult, expense_backtest: BacktestResult
) -> list[ErrorBlock]:
    """计算每个窗口内的最大累计不利误差。

    单日不利误差定义（偏乐观方向的误差）：

        预测到账偏高  或  预测采购偏低

        e(h) = inflow_pred(h) - inflow_actual(h)
             + outflow_actual(h) - outflow_pred(h)

    然后取块内累计误差的最大值作为风险压力。只看期末误差会漏掉中途缺口。
    """
    if income_backtest.field_name != "inflow" or expense_backtest.field_name != "outflow":
        raise ForecastError("FOLD_PAIRING", "预测误差窗口字段不匹配")
    income_folds: Sequence[BacktestFold] = income_backtest.folds
    expense_folds: Sequence[BacktestFold] = expense_backtest.folds
    if not income_folds or len(income_folds) != len(expense_folds):
        raise ForecastError("FOLD_PAIRING", "预测误差窗口数量不一致")

    blocks: list[ErrorBlock] = []
    for income_fold, expense_fold in zip(income_folds, expense_folds, strict=True):
        if income_fold.origin != expense_fold.origin:
            raise ForecastError("FOLD_ALIGNMENT", "预测误差窗口起点不一致")
        if income_fold.days != expense_fold.days:
            raise ForecastError("FOLD_ALIGNMENT", "预测误差窗口日期不一致")
        length = len(income_fold.actual)
        if not (
            length == len(income_fold.predictions)
            == len(expense_fold.actual)
            == len(expense_fold.predictions)
            == len(income_fold.days)
        ):
            raise ForecastError("FOLD_LENGTH", "预测误差窗口长度不一致")

        cumulative = 0
        maximum = 0
        prefix: list[int] = []
        for index in range(length):
            error = (
                income_fold.predictions[index]
                - income_fold.actual[index]
                + expense_fold.actual[index]
                - expense_fold.predictions[index]
            )
            cumulative += error
            maximum = max(maximum, cumulative)
            prefix.append(cumulative)
        blocks.append(
            ErrorBlock(
                origin=income_fold.origin,
                training_end=income_fold.training_end,
                stress_cents=maximum,
                prefix_errors=tuple(prefix),
                source_refs=tuple(
                    dict.fromkeys([*income_fold.source_refs, *expense_fold.source_refs])
                ),
            )
        )
    return blocks


def recommend_reserve(
    blocks: Sequence[ErrorBlock],
    *,
    current_reserve_cents: int,
    quantile: float = DEFAULT_RESERVE_QUANTILE,
    rounding_cents: int = DEFAULT_ROUNDING_CENTS,
    min_blocks: int = RESERVE_MIN_BLOCKS,
    basis_hash: str = "",
) -> ReserveAdvice:
    """给出留底建议。**绝不**自动降低既有留底。"""
    if current_reserve_cents < 0:
        raise ForecastError("RESERVE", "经营留底不能为负")
    if not (0 < quantile <= 1):
        raise ForecastError("QUANTILE", "分位数参数不合法")
    if rounding_cents < 1:
        raise ForecastError("ROUNDING", "取整单位不合法")
    if min_blocks < 1:
        raise ForecastError("MIN_BLOCKS", "最少窗口数不合法")
    for block in blocks:
        if block.stress_cents < 0:
            raise ForecastError("STRESS_AMOUNT", "风险压力不能为负")

    enough = len(blocks) >= min_blocks
    raw = quantile_cents([block.stress_cents for block in blocks], quantile) if enough else None
    rounded = None if raw is None else -(-raw // rounding_cents) * rounding_cents
    suggested = current_reserve_cents if rounded is None else max(current_reserve_cents, rounded)

    refs: list[str] = []
    for block in blocks:
        refs.extend(block.source_refs)

    return ReserveAdvice(
        status="SUGGESTION" if enough else "INSUFFICIENT_SAMPLE",
        current_reserve_cents=current_reserve_cents,
        suggested_reserve_cents=suggested,
        extra_cents=suggested - current_reserve_cents,
        empirical_error_cents=raw,
        block_count=len(blocks),
        quantile=quantile,
        rounding_cents=rounding_cents,
        min_blocks=min_blocks,
        basis_hash=basis_hash,
        source_refs=tuple(dict.fromkeys(refs)),
    )


__all__ = [
    "DEFAULT_RESERVE_QUANTILE",
    "DEFAULT_ROUNDING_CENTS",
    "DISCLOSURE",
    "ErrorBlock",
    "ReserveAdvice",
    "buffer_blocks",
    "recommend_reserve",
]
