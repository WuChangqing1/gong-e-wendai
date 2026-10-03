"""结算延期压力分析。

统计边界（必须严格遵守）
------------------------
* 只统计**已完成**（``completed``）的结算配对样本。
* ``open`` **单独计数**，绝不能视为 0 天延迟；未完成项存在删失。
* 经验延迟使用已完成样本的 ``q = 0.9`` 分位数（最近秩）。
* 最少已完成样本数 ``12``；不足 12 笔时**不生成**任何历史经验延迟值。
* 只能称「历史已完成结算记录中的经验延迟参考」，**禁止**表述为：

  - 「90% 会到账」
  - 「90% 安全」
  - 「未来概率」
  - 「银行 T+1」
  - 「未来到账保证」

压力情景只描述自然日推移带来的资金压力，不推断银行清算规则或节假日安排。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Sequence

from app.models.enhancement import SETTLEMENT_MIN_SAMPLES
from app.services.forecast_engine import ForecastError, quantile_cents
from app.utils.timeutil import to_utc

#: 手动延期天数上限（自然日）
MAX_MANUAL_DELAY_DAYS = 30
DEFAULT_MANUAL_DELAY_DAYS = 2
DEFAULT_DELAY_QUANTILE = 0.9

SCENARIO_ON_TIME = "on_time"
SCENARIO_MANUAL_DELAY = "manual_delay"
SCENARIO_SAMPLE_DELAY = "sample_delay"

DISCLOSURE = (
    "仅描述已完成结算样本的经验分布，未完成项存在删失；"
    "不能解释为未来到账概率，也不推断银行 T+1 或节假日规则。"
)


@dataclass(frozen=True, slots=True)
class SettlementPair:
    """一笔结算的预计/实际到账配对。"""

    id: str
    channel: str
    scheduled_at: datetime
    known_at: datetime
    status: str
    source_ref: str
    actual_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class SettlementStats:
    """同商户、同渠道的历史完成样本统计。"""

    channel: str
    completed_count: int
    open_count: int
    overdue_open_count: int
    status: str
    quantile: float
    min_samples: int
    empirical_delay_days: int | None
    source_refs: tuple[str, ...] = ()
    open_source_refs: tuple[str, ...] = ()
    disclosure: str = DISCLOSURE

    @property
    def has_sample(self) -> bool:
        return self.empirical_delay_days is not None

    @property
    def headline(self) -> str:
        """面向用户的说明文案：永远不承诺概率。"""
        if not self.has_sample:
            return (
                f"历史结算记录还不够，目前只提供手动延期压力分析"
                f"（该渠道已完成 {self.completed_count} 笔，"
                f"还需要 {max(0, self.min_samples - self.completed_count)} 笔）"
            )
        return (
            f"历史已完成结算记录中的经验延迟参考：{self.empirical_delay_days} 个自然日"
            f"（已完成 {self.completed_count} 笔，"
            f"另有 {self.open_count} 笔未完成，未参与统计）"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "completed_count": self.completed_count,
            "open_count": self.open_count,
            "overdue_open_count": self.overdue_open_count,
            "status": self.status,
            "quantile": self.quantile,
            "min_samples": self.min_samples,
            "empirical_delay_days": self.empirical_delay_days,
            "has_sample": self.has_sample,
            "headline": self.headline,
            "source_refs": list(self.source_refs),
            "open_source_refs": list(self.open_source_refs),
            "disclosure": self.disclosure,
        }


@dataclass(frozen=True, slots=True)
class DelayScenario:
    """一个延期压力情景。"""

    id: str
    label: str
    delay_days: int
    basis: str
    source_refs: tuple[str, ...] = ()
    time_overrides: dict[str, datetime] | None = None
    #: 永远为 None：不编造概率
    probability: None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "delay_days": self.delay_days,
            "basis": self.basis,
            "source_refs": list(self.source_refs),
            "time_overrides": {
                key: value.isoformat() for key, value in (self.time_overrides or {}).items()
            },
            "probability": None,
        }


def settlement_stats(
    pairs: Sequence[SettlementPair],
    *,
    channel: str,
    as_of: datetime,
    quantile: float = DEFAULT_DELAY_QUANTILE,
    min_samples: int = SETTLEMENT_MIN_SAMPLES,
) -> SettlementStats:
    """同渠道已完成样本的延迟统计。

    其他渠道与其他商户的数据不参与，避免污染样本。
    """
    if not channel:
        raise ForecastError("SETTLEMENT_SCOPE", "必须指定结算渠道")
    if not (0 < quantile <= 1):
        raise ForecastError("QUANTILE", "分位数参数不合法")
    if min_samples < 1:
        raise ForecastError("MIN_SAMPLES", "最少样本数不合法")

    reference = to_utc(as_of)
    completed: list[tuple[int, str]] = []
    open_items: list[SettlementPair] = []
    seen: set[str] = set()

    for pair in pairs:
        if pair.channel != channel:
            continue
        if pair.id in seen:
            raise ForecastError("DUPLICATE_SETTLEMENT", "结算记录编号重复")
        seen.add(pair.id)
        if not pair.source_ref:
            raise ForecastError("SETTLEMENT_SOURCE", "结算记录缺少来源")
        scheduled = to_utc(pair.scheduled_at)
        known = to_utc(pair.known_at)
        if known > reference or scheduled > reference:
            # 当时还不可知的记录不进入样本
            continue
        if pair.status == "cancelled":
            continue
        if pair.status not in {"open", "completed"}:
            raise ForecastError("SETTLEMENT_STATUS", "结算状态不合法")
        if pair.status == "completed":
            if pair.actual_at is None:
                raise ForecastError("ACTUAL_AT", "已完成结算缺少实际到账时间")
            actual = to_utc(pair.actual_at)
            if actual <= reference:
                delay_ms = max(0, int((actual - scheduled).total_seconds() * 1000))
                completed.append((delay_ms, pair.source_ref))
                continue
        open_items.append(pair)

    enough = len(completed) >= min_samples
    delay_ms = quantile_cents([item[0] for item in completed], quantile) if enough else None
    return SettlementStats(
        channel=channel,
        completed_count=len(completed),
        open_count=len(open_items),
        overdue_open_count=sum(
            1 for item in open_items if to_utc(item.scheduled_at) < reference
        ),
        status="DESCRIPTIVE_SAMPLE" if enough else "INSUFFICIENT_SAMPLE",
        quantile=quantile,
        min_samples=min_samples,
        empirical_delay_days=(
            None if delay_ms is None else -(-delay_ms // 86_400_000)  # ceil 到自然日
        ),
        source_refs=tuple(item[1] for item in completed),
        open_source_refs=tuple(item.source_ref for item in open_items),
    )


def build_delay_scenarios(
    target_events: Sequence[Any],
    *,
    manual_delay_days: int = DEFAULT_MANUAL_DELAY_DAYS,
    stats: SettlementStats | None = None,
) -> list[DelayScenario]:
    """构造延期压力情景。

    ``target_events`` 必须都是**已确认的、计划中的结算款收入**，
    且属于同一渠道；预测产生的收入永远不能作为延期目标。
    """
    if not target_events:
        raise ForecastError("SELECT_RECEIPTS", "请选择要延期的结算款")
    if manual_delay_days < 0 or manual_delay_days > MAX_MANUAL_DELAY_DAYS:
        raise ForecastError(
            "DELAY_LIMIT", f"延期天数需在 0 到 {MAX_MANUAL_DELAY_DAYS} 个自然日之间"
        )

    identifiers = [item.id for item in target_events]
    if len(set(identifiers)) != len(identifiers):
        raise ForecastError("DUPLICATE_SELECTION", "延期目标不能重复")
    channels = {getattr(item, "channel", None) for item in target_events}
    if len(channels) > 1:
        raise ForecastError("MIXED_COHORT", "延期目标必须属于同一结算渠道")

    def make(scenario_id: str, days: int, basis: str) -> DelayScenario:
        overrides = {
            item.id: to_utc(item.scheduled_at) + timedelta(days=days) for item in target_events
        }
        label = "按当前计划" if days == 0 else f"指定结算延迟 {days} 个自然日"
        return DelayScenario(
            id=scenario_id,
            label=label,
            delay_days=days,
            basis=basis,
            source_refs=(stats.source_refs if stats else ()),
            time_overrides=overrides,
        )

    scenarios = [
        make(SCENARIO_ON_TIME, 0, "当前已确认的预计到账时间"),
        make(
            SCENARIO_MANUAL_DELAY,
            manual_delay_days,
            "用户设定的压力，不是概率预测",
        ),
    ]
    if stats is not None and stats.empirical_delay_days is not None:
        scenarios.append(
            make(
                SCENARIO_SAMPLE_DELAY,
                stats.empirical_delay_days,
                (
                    f"已完成样本经验分位数 q={stats.quantile}，n={stats.completed_count}；"
                    f"存在 {stats.open_count} 笔未完成项"
                ),
            )
        )
    return scenarios


__all__ = [
    "DEFAULT_DELAY_QUANTILE",
    "DEFAULT_MANUAL_DELAY_DAYS",
    "DISCLOSURE",
    "MAX_MANUAL_DELAY_DAYS",
    "SCENARIO_MANUAL_DELAY",
    "SCENARIO_ON_TIME",
    "SCENARIO_SAMPLE_DELAY",
    "DelayScenario",
    "SettlementPair",
    "SettlementStats",
    "build_delay_scenarios",
    "settlement_stats",
]
