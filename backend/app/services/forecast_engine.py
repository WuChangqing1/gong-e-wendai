"""日常现金收付预测（forecast engine）。

作用范围（强制边界）
--------------------
预测**只**处理两类历史事实：

* 历史已到账的结算收入
* 历史已发生的日常采购

房租、税款、明确退款、确定付款等**固定义务继续由正式 CashEvent 进入确定性账本**，
不得重复计入预测。

预测结果永远不能进入确定性 Cash Engine
--------------------------------------
``ForecastResult.provenance`` 恒为 ``forecast``：

* 不能转换为已确认的 ``CashEvent``
* 不能加入 ``opening_balance``
* 不能加入计划中收入
* 不能提高 ``max_withdrawable``
* 不能修复 ``PAYMENT_GAP``

三种方法
--------
* ``seasonal_naive``  上周同一天
* ``weekday_median``  近四周同星期中位数（需要 28 个连续完整日）
* ``ses``             简单指数平滑（默认 alpha = 0.3）

金额内部一律为整数分；SES 每次更新都用整数运算四舍五入到分，
以便与参考内核逐分一致。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Literal, Sequence

from app.models.enhancement import FORECAST_MIN_DAYS_SEASONAL, FORECAST_MIN_DAYS_WEEKDAY_MEDIAN
from app.utils.timeutil import APP_TIMEZONE, to_utc

METHOD_SEASONAL_NAIVE = "seasonal_naive"
METHOD_WEEKDAY_MEDIAN = "weekday_median"
METHOD_SES = "ses"

METHODS: tuple[str, ...] = (METHOD_SEASONAL_NAIVE, METHOD_WEEKDAY_MEDIAN, METHOD_SES)

METHOD_LABELS: dict[str, str] = {
    METHOD_SEASONAL_NAIVE: "上周同一天",
    METHOD_WEEKDAY_MEDIAN: "近四周同星期中位数",
    METHOD_SES: "简单指数平滑",
}

FIELD_INFLOW = "inflow"
FIELD_OUTFLOW = "outflow"
FIELDS: tuple[str, ...] = (FIELD_INFLOW, FIELD_OUTFLOW)

FIELD_LABELS: dict[str, str] = {
    FIELD_INFLOW: "日常预计到账",
    FIELD_OUTFLOW: "日常预计采购",
}

MAX_HORIZON_DAYS = 7
DEFAULT_ALPHA_BPS = 3000
#: 方法选择只用前 56 个完整日；其余日用于留出检验
DEFAULT_SELECTION_DAYS = 56
#: 预测阶段最少需要多少完整日才能给出「可复核」的结果（三种方法里最省的一种）
WARMUP_MIN_DAYS = FORECAST_MIN_DAYS_SEASONAL

ForecastField = Literal["inflow", "outflow"]


class ForecastError(Exception):
    """预测输入不满足算法要求时抛出。

    调用方必须把它转换成面向用户的「历史记录还不够」提示，
    绝不能把 ``INSUFFICIENT_HISTORY`` 之类的代码直接展示给经营者。
    """

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


# ---------------------------------------------------------------------------
# 基础数学（整数分，与参考内核一致）
# ---------------------------------------------------------------------------
def median_cents(values: Sequence[int]) -> int:
    """中位数，偶数个时向上取整到分（不做浮点除法）。"""
    if not values:
        raise ForecastError("EMPTY_MEDIAN", "缺少可用的历史样本")
    ordered = sorted(int(item) for item in values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle] + 1) // 2


def quantile_cents(values: Sequence[int], q: float) -> int:
    """最近秩（nearest-rank）经验分位数。

    q 是设计参数，**不是**未来覆盖概率。
    """
    if not values:
        raise ForecastError("QUANTILE", "缺少可用的分位数样本")
    if not (0 < q <= 1):
        raise ForecastError("QUANTILE", "分位数参数不合法")
    ordered = sorted(int(item) for item in values)
    rank = max(1, min(math.ceil(q * len(ordered)), len(ordered)))
    return ordered[rank - 1]


def _rounded_ratio(numerator: int, denominator: int) -> int:
    """整数四舍五入（half-up），与参考内核 BigInt 版本等价。"""
    if denominator <= 0:
        raise ForecastError("RATIO", "比例分母不合法")
    if numerator < 0:
        return -((-numerator + denominator // 2) // denominator)
    return (numerator + denominator // 2) // denominator


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class DailyCash:
    """一个已确认完整的自然日历史收付。"""

    day: date
    complete: bool
    inflow_cents: int
    outflow_cents: int
    source_refs: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "day": self.day.isoformat(),
            "complete": self.complete,
            "inflow_cents": self.inflow_cents,
            "outflow_cents": self.outflow_cents,
            "net_cents": self.inflow_cents - self.outflow_cents,
            "source_refs": list(self.source_refs),
        }


@dataclass(frozen=True, slots=True)
class ForecastPoint:
    day: date
    cents: int
    method: str
    field_name: str
    training_end: date
    source_refs: tuple[str, ...] = ()
    provenance: str = "forecast"
    confirmed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "day": self.day.isoformat(),
            "cents": self.cents,
            "amount_text": _yuan(self.cents),
            "method": self.method,
            "method_label": METHOD_LABELS.get(self.method, self.method),
            "field": self.field_name,
            "training_end": self.training_end.isoformat(),
            "source_refs": list(self.source_refs),
            "provenance": self.provenance,
            "confirmed": self.confirmed,
        }


@dataclass(frozen=True, slots=True)
class ErrorMetrics:
    """MAE / RMSE。属于诊断指标，默认不直接展示给普通经营者。"""

    count: int
    mae_cents: float
    rmse_cents: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "count": self.count,
            "mae_cents": self.mae_cents,
            "mae_text": _yuan(round(self.mae_cents)),
            "rmse_cents": self.rmse_cents,
            "rmse_text": _yuan(round(self.rmse_cents)),
        }


@dataclass(frozen=True, slots=True)
class BacktestFold:
    origin: int
    training_end: date
    days: tuple[date, ...]
    predictions: tuple[int, ...]
    actual: tuple[int, ...]
    source_refs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class BacktestResult:
    method: str
    field_name: str
    horizon: int
    folds: tuple[BacktestFold, ...]
    metrics: ErrorMetrics
    by_horizon: tuple[ErrorMetrics, ...]
    disclosure: str = (
        "滚动窗口相互重叠，误差不是独立样本；不报告未来覆盖概率。"
    )


@dataclass(frozen=True, slots=True)
class DailyForecast:
    """一个自然日的日常收付参考（到账 / 采购 / 净变化）。"""

    day: date
    inflow_cents: int
    outflow_cents: int
    inflow_method: str
    outflow_method: str
    training_end: date
    source_refs: tuple[str, ...] = ()
    provenance: str = "forecast"
    confirmed: bool = False

    @property
    def net_cents(self) -> int:
        return self.inflow_cents - self.outflow_cents

    def to_dict(self) -> dict[str, Any]:
        return {
            "day": self.day.isoformat(),
            "inflow_cents": self.inflow_cents,
            "outflow_cents": self.outflow_cents,
            "net_cents": self.net_cents,
            "inflow_text": _yuan(self.inflow_cents),
            "outflow_text": _yuan(self.outflow_cents),
            "net_text": _yuan(self.net_cents),
            "inflow_method": self.inflow_method,
            "inflow_method_label": METHOD_LABELS.get(self.inflow_method, self.inflow_method),
            "outflow_method": self.outflow_method,
            "outflow_method_label": METHOD_LABELS.get(self.outflow_method, self.outflow_method),
            "training_end": self.training_end.isoformat(),
            "source_refs": list(self.source_refs),
            "provenance": self.provenance,
            "confirmed": self.confirmed,
        }


@dataclass(frozen=True, slots=True)
class ForecastResult:
    """预测结果。**永远**不能进入确定性 Cash Engine。"""

    method_inflow: str
    method_outflow: str
    daily: tuple[DailyForecast, ...]
    history_days: int
    training_end: date
    needs_review: bool
    needs_review_reason: str | None
    diagnostics: dict[str, Any]
    summary: dict[str, int] = field(default_factory=dict)
    source_refs: tuple[str, ...] = ()
    disclosure: str = (
        "基于过往经营记录形成，仅供安排参考，不计入今天可提用金额。"
    )
    #: 显式声明：预测对可提用金额没有任何影响
    affects_withdrawable: bool = False
    provenance: str = "forecast"

    def to_dict(self) -> dict[str, Any]:
        return {
            "method_inflow": self.method_inflow,
            "method_inflow_label": METHOD_LABELS.get(self.method_inflow, self.method_inflow),
            "method_outflow": self.method_outflow,
            "method_outflow_label": METHOD_LABELS.get(self.method_outflow, self.method_outflow),
            "daily": [item.to_dict() for item in self.daily],
            "summary": self.summary,
            "history_days": self.history_days,
            "training_end": self.training_end.isoformat(),
            "needs_review": self.needs_review,
            "needs_review_reason": self.needs_review_reason,
            "diagnostics": self.diagnostics,
            "source_refs": list(self.source_refs),
            "disclosure": self.disclosure,
            "affects_withdrawable": self.affects_withdrawable,
            "provenance": self.provenance,
        }


@dataclass(frozen=True, slots=True)
class ForecastUnavailable:
    """历史不足时的友好降级结果。"""

    reason_code: str
    message: str
    history_days: int
    required_days: int
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "reason_code": self.reason_code,
            "message": self.message,
            "history_days": self.history_days,
            "required_days": self.required_days,
            "details": self.details,
            "affects_withdrawable": False,
        }


def _yuan(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    value = abs(int(cents))
    return f"{sign}¥{value // 100}.{value % 100:02d}"


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------
def validate_daily(rows: Sequence[DailyCash]) -> None:
    """校验历史序列：同一商户、连续自然日、完整、非负整数分、有来源。"""
    if not rows:
        raise ForecastError(
            "HISTORY_REQUIRED",
            "还没有可用于参考的历史经营数据。",
            {"required_days": WARMUP_MIN_DAYS, "history_days": 0},
        )
    for index, row in enumerate(rows):
        if not row.complete:
            raise ForecastError(
                "INCOMPLETE_DAY",
                "存在尚未确认完整的日期，请先确认该日期数据完整。",
                {"day": row.day.isoformat()},
            )
        for value in (row.inflow_cents, row.outflow_cents):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ForecastError(
                    "HISTORY_AMOUNT",
                    "历史金额必须是非负整数分。",
                    {"day": row.day.isoformat()},
                )
        if not row.source_refs:
            raise ForecastError(
                "HISTORY_SOURCE",
                "历史数据缺少来源记录。",
                {"day": row.day.isoformat()},
            )
        if index:
            previous = rows[index - 1].day
            if (row.day - previous).days != 1:
                raise ForecastError(
                    "MISSING_OR_DUPLICATE_DAY",
                    "历史日期不连续或存在重复，缺失的日期不能当作 0 处理。",
                    {
                        "expected": (previous + timedelta(days=1)).isoformat(),
                        "actual": row.day.isoformat(),
                    },
                )


def required_days_for(method: str) -> int:
    """算法真实需要的最少完整日数（不是假标准）。"""
    if method == METHOD_WEEKDAY_MEDIAN:
        return FORECAST_MIN_DAYS_WEEKDAY_MEDIAN
    return FORECAST_MIN_DAYS_SEASONAL


# ---------------------------------------------------------------------------
# 预测
# ---------------------------------------------------------------------------
def predict(
    rows: Sequence[DailyCash],
    *,
    method: str = METHOD_SEASONAL_NAIVE,
    field_name: ForecastField = "inflow",
    horizon: int = 7,
    alpha_bps: int = DEFAULT_ALPHA_BPS,
) -> list[ForecastPoint]:
    """预测未来 1~7 个自然日。"""
    validate_daily(rows)
    if method not in METHODS:
        raise ForecastError("FORECAST_METHOD", "不支持的历史参考方法")
    if field_name not in FIELDS:
        raise ForecastError("FORECAST_FIELD", "不支持的预测字段")
    if not 1 <= int(horizon) <= MAX_HORIZON_DAYS:
        raise ForecastError("HORIZON_MAX_7", "最多只能参考未来 7 个自然日")
    if not 1 <= int(alpha_bps) <= 10000:
        raise ForecastError("ALPHA", "平滑系数不合法")

    needed = required_days_for(method)
    if len(rows) < needed:
        raise ForecastError(
            "INSUFFICIENT_HISTORY",
            "历史记录还不够，暂时无法形成稳定的 7 天日常收付参考。",
            {
                "history_days": len(rows),
                "required_days": needed,
                "method": method,
            },
        )

    attr = "inflow_cents" if field_name == FIELD_INFLOW else "outflow_cents"
    last = rows[-1]
    by_day = {row.day: row for row in rows}

    level = getattr(rows[0], attr)
    if method == METHOD_SES:
        for row in rows[1:]:
            value = getattr(row, attr)
            level = _rounded_ratio(
                alpha_bps * value + (10000 - alpha_bps) * level, 10000
            )

    points: list[ForecastPoint] = []
    for index in range(int(horizon)):
        target = last.day + timedelta(days=index + 1)
        if method == METHOD_SES:
            cents = level
            refs: list[str] = []
            for row in rows[-7:]:
                refs.extend(row.source_refs)
        else:
            lookback = 4 if method == METHOD_WEEKDAY_MEDIAN else 1
            references = [by_day.get(target - timedelta(days=7 * (j + 1))) for j in range(lookback)]
            if any(item is None for item in references):
                raise ForecastError(
                    "MISSING_WEEKDAY",
                    "历史记录覆盖不到所需的同星期日期。",
                    {"day": target.isoformat()},
                )
            resolved = [item for item in references if item is not None]
            if method == METHOD_WEEKDAY_MEDIAN:
                cents = median_cents([getattr(item, attr) for item in resolved])
            else:
                cents = getattr(resolved[0], attr)
            refs = []
            for item in resolved:
                refs.extend(item.source_refs)

        points.append(
            ForecastPoint(
                day=target,
                cents=int(cents),
                method=method,
                field_name=field_name,
                training_end=last.day,
                source_refs=tuple(dict.fromkeys(refs)),
            )
        )
    return points


def metrics(actual: Sequence[int], predicted: Sequence[int]) -> ErrorMetrics:
    """MAE / RMSE。

    净现金流会跨零，因此不计算 MAPE（0 分母）。
    MAE / RMSE 可能不是整数分，属于诊断指标，不入账。
    """
    if not actual or len(actual) != len(predicted):
        raise ForecastError("METRIC_LENGTH", "实际值与预测值长度不一致")
    errors = [int(a) - int(p) for a, p in zip(actual, predicted, strict=True)]
    count = len(errors)
    total_abs = sum(abs(item) for item in errors)
    scale = max(abs(item) for item in errors)
    if scale == 0:
        rmse = 0.0
    else:
        rmse = scale * (sum((item / scale) ** 2 for item in errors) / count) ** 0.5
    return ErrorMetrics(count=count, mae_cents=total_abs / count, rmse_cents=rmse)


def rolling_backtest(
    rows: Sequence[DailyCash],
    *,
    method: str = METHOD_SEASONAL_NAIVE,
    field_name: ForecastField = "inflow",
    start_origin: int = FORECAST_MIN_DAYS_WEEKDAY_MEDIAN,
    stop_origin: int | None = None,
    horizon: int = 7,
    stride: int = 1,
    alpha_bps: int = DEFAULT_ALPHA_BPS,
) -> BacktestResult:
    """滚动留出检验。

    ``origin`` 表示「当时可见的历史天数」；在该起点一次性预测未来
    ``horizon`` 天。每个起点只使用起点之前的数据，因此后面的数据无法
    影响之前已经发出的预测。
    """
    validate_daily(rows)
    if start_origin < FORECAST_MIN_DAYS_SEASONAL:
        raise ForecastError("START_ORIGIN", "起点早于算法可用的最小历史天数")
    upper = len(rows) - horizon if stop_origin is None else stop_origin
    if upper < start_origin:
        raise ForecastError("BACKTEST_RANGE", "历史长度不足以做滚动检验")
    if stride < 1:
        raise ForecastError("STRIDE", "步长不合法")

    folds: list[BacktestFold] = []
    origin = start_origin
    while origin <= upper:
        forecast = predict(
            rows[:origin],
            method=method,
            field_name=field_name,
            horizon=horizon,
            alpha_bps=alpha_bps,
        )
        actual_rows = rows[origin : origin + horizon]
        attr = "inflow_cents" if field_name == FIELD_INFLOW else "outflow_cents"
        refs: list[str] = []
        for row in rows[: origin + horizon]:
            refs.extend(row.source_refs)
        folds.append(
            BacktestFold(
                origin=origin,
                training_end=rows[origin - 1].day,
                days=tuple(item.day for item in forecast),
                predictions=tuple(item.cents for item in forecast),
                actual=tuple(getattr(row, attr) for row in actual_rows),
                source_refs=tuple(dict.fromkeys(refs)),
            )
        )
        origin += stride

    if not folds:
        raise ForecastError("NO_FOLDS", "没有可用的检验窗口")

    flat_actual = [value for fold in folds for value in fold.actual]
    flat_predicted = [value for fold in folds for value in fold.predictions]
    by_horizon = tuple(
        metrics(
            [fold.actual[h] for fold in folds],
            [fold.predictions[h] for fold in folds],
        )
        for h in range(horizon)
    )
    return BacktestResult(
        method=method,
        field_name=field_name,
        horizon=horizon,
        folds=tuple(folds),
        metrics=metrics(flat_actual, flat_predicted),
        by_horizon=by_horizon,
    )


def evaluate_forecasts(
    rows: Sequence[DailyCash],
    *,
    selection_days: int = DEFAULT_SELECTION_DAYS,
    horizon: int = 7,
    alpha_bps: int = DEFAULT_ALPHA_BPS,
) -> ForecastResult:
    """训练段选方法，留出段只用于检验。

    规则（与参考算法一致，不得违反）：

    * 只用前 ``selection_days`` 天选择方法；
    * 选择时并列则保留更简单的基线（``seasonal_naive``）；
    * 留出段结果**绝不**用于重新选择方法；
    * 如果选中方法在留出段比简单基线更差，``needs_review = True``，
      如实展示，不删除「不好看」的样本。
    """
    validate_daily(rows)
    if selection_days < FORECAST_MIN_DAYS_WEEKDAY_MEDIAN + horizon:
        raise ForecastError("SELECTION_DAYS", "训练段长度不足")
    if len(rows) < selection_days + horizon:
        raise ForecastError(
            "INSUFFICIENT_HOLDOUT",
            "历史记录还不够，暂时无法完成留出检验。",
            {
                "history_days": len(rows),
                "required_days": selection_days + horizon,
            },
        )

    selected: dict[str, str] = {}
    diagnostics: dict[str, Any] = {}
    for field_name in FIELDS:
        candidates: list[tuple[str, BacktestResult, BacktestResult]] = []
        for method in METHODS:
            validation = rolling_backtest(
                rows[:selection_days],
                method=method,
                field_name=field_name,
                horizon=horizon,
                alpha_bps=alpha_bps,
            )
            holdout = rolling_backtest(
                rows,
                method=method,
                field_name=field_name,
                horizon=horizon,
                start_origin=selection_days,
                alpha_bps=alpha_bps,
            )
            candidates.append((method, validation, holdout))
        # 并列（不严格更优）时保留更简单的基线
        winner = candidates[0]
        for candidate in candidates[1:]:
            if candidate[1].metrics.mae_cents < winner[1].metrics.mae_cents:
                winner = candidate
        selected[field_name] = winner[0]
        baseline = next(item for item in candidates if item[0] == METHOD_SEASONAL_NAIVE)
        needs_review = winner[2].metrics.mae_cents > baseline[2].metrics.mae_cents
        diagnostics[field_name] = {
            "method": winner[0],
            "method_label": METHOD_LABELS[winner[0]],
            "selected_validation_mae_cents": round(winner[1].metrics.mae_cents, 4),
            "selected_holdout_mae_cents": round(winner[2].metrics.mae_cents, 4),
            "baseline_holdout_mae_cents": round(baseline[2].metrics.mae_cents, 4),
            "needs_review": needs_review,
            "explanation": (
                "留出段变差时提示复核；不会用同一检验段偷偷重选并宣称提升。"
                "这不是显著性检验，也不是漂移检验。"
            ),
        }

    income = predict(
        rows,
        method=selected[FIELD_INFLOW],
        field_name="inflow",
        horizon=horizon,
        alpha_bps=alpha_bps,
    )
    expense = predict(
        rows,
        method=selected[FIELD_OUTFLOW],
        field_name="outflow",
        horizon=horizon,
        alpha_bps=alpha_bps,
    )

    daily: list[DailyForecast] = []
    for index, income_point in enumerate(income):
        expense_point = expense[index]
        refs = tuple(dict.fromkeys([*income_point.source_refs, *expense_point.source_refs]))
        daily.append(
            DailyForecast(
                day=income_point.day,
                inflow_cents=income_point.cents,
                outflow_cents=expense_point.cents,
                inflow_method=income_point.method,
                outflow_method=expense_point.method,
                training_end=income_point.training_end,
                source_refs=refs,
            )
        )

    needs_review = any(item["needs_review"] for item in diagnostics.values())
    reason = (
        "近期经营变化较大，历史规律参考价值下降，建议人工复核。"
        if needs_review
        else None
    )
    all_refs: list[str] = []
    for row in rows:
        all_refs.extend(row.source_refs)

    summary = {
        "horizon_days": len(daily),
        "inflow_cents": sum(item.inflow_cents for item in daily),
        "outflow_cents": sum(item.outflow_cents for item in daily),
        "net_cents": sum(item.net_cents for item in daily),
    }

    return ForecastResult(
        method_inflow=selected[FIELD_INFLOW],
        method_outflow=selected[FIELD_OUTFLOW],
        daily=tuple(daily),
        history_days=len(rows),
        training_end=rows[-1].day,
        needs_review=needs_review,
        needs_review_reason=reason,
        diagnostics=diagnostics,
        summary=summary,
        source_refs=tuple(dict.fromkeys(all_refs)),
    )


def unavailable_from_error(error: ForecastError, history_days: int) -> ForecastUnavailable:
    """把算法级错误翻译成普通经营者能看懂的说明。

    绝不把 ``INSUFFICIENT_HISTORY`` 之类的代码直接展示给用户。
    """
    required = int(error.details.get("required_days") or WARMUP_MIN_DAYS)
    if error.code in {"HISTORY_REQUIRED", "INSUFFICIENT_HISTORY"}:
        message = (
            f"历史记录还不够。目前已有 {history_days} 个完整日，"
            f"需要更多连续记录后才能形成稳定的 7 天日常收付参考。"
        )
    elif error.code == "INCOMPLETE_DAY":
        message = "存在尚未确认完整的日期，请先在「历史经营数据」里确认该日期数据完整。"
    elif error.code == "MISSING_OR_DUPLICATE_DAY":
        message = "历史日期不连续：缺失的日期不能当作没有收付，请先补齐或明确确认。"
    elif error.code in {"INSUFFICIENT_HOLDOUT", "SELECTION_DAYS"}:
        message = (
            f"历史记录还不够完成留出检验。目前已有 {history_days} 个完整日，"
            f"还需要约 {required + MAX_HORIZON_DAYS} 个连续完整日。"
        )
    else:
        message = "历史经营数据暂时无法形成参考，请检查数据完整性后再试。"
    return ForecastUnavailable(
        reason_code=error.code,
        message=message,
        history_days=history_days,
        required_days=required,
        details=error.details,
    )


# ---------------------------------------------------------------------------
# 与数据库的衔接
# ---------------------------------------------------------------------------
def rows_from_records(records: Sequence[Any]) -> list[DailyCash]:
    """把 ``DailyCashHistory`` 记录转换为按日期升序的 ``DailyCash``。"""
    rows = [
        DailyCash(
            day=date.fromisoformat(record.day),
            complete=bool(record.complete),
            inflow_cents=int(record.inflow_cents),
            outflow_cents=int(record.outflow_cents),
            source_refs=tuple(record.source_refs or ()),
        )
        for record in records
    ]
    rows.sort(key=lambda item: item.day)
    return rows


def beijing_day_of(moment: datetime) -> date:
    """把 UTC 时点折算为北京时间自然日。"""
    return to_utc(moment).astimezone(APP_TIMEZONE).date()


__all__ = [
    "DEFAULT_ALPHA_BPS",
    "DEFAULT_SELECTION_DAYS",
    "FIELDS",
    "FIELD_INFLOW",
    "FIELD_LABELS",
    "FIELD_OUTFLOW",
    "ForecastError",
    "ForecastPoint",
    "ForecastResult",
    "ForecastUnavailable",
    "METHODS",
    "METHOD_LABELS",
    "METHOD_SEASONAL_NAIVE",
    "METHOD_SES",
    "METHOD_WEEKDAY_MEDIAN",
    "MAX_HORIZON_DAYS",
    "WARMUP_MIN_DAYS",
    "DailyCash",
    "DailyForecast",
    "ErrorMetrics",
    "beijing_day_of",
    "evaluate_forecasts",
    "median_cents",
    "metrics",
    "predict",
    "quantile_cents",
    "required_days_for",
    "rolling_backtest",
    "rows_from_records",
    "unavailable_from_error",
    "validate_daily",
]
