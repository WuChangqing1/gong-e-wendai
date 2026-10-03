"""增强模块数据模型：历史现金记录、结算配对、增强运行与留底确认。

设计要点
--------
* 只做增量迁移，不删除任何既有表或数据。
* 金额一律为整数分（``int`` cents），绝不用浮点。
* 日期一律用 UTC ``datetime``；自然日口径按 Asia/Shanghai 折算。
* ``revision`` 用于失效判定：账本（CashEvent）或历史数据任一变化都必须让
  既有的 ``EnhancementRun`` 与留底建议失效。

关于「缺失日」的核心约定
------------------------
``DailyCashHistory`` 只保存**已确认完整**的自然日。没有记录的日期**不代表金额为 0**，
而是「不清楚」。只有商户在导入时明确确认「该日期范围内数据完整」后，
缺失交易的完整日期才允许聚合为 0。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UTCDateTime
from app.models.merchant import MerchantProfile
from app.models.user import new_id

# ---------------------------------------------------------------------------
# 结算记录状态
# ---------------------------------------------------------------------------
SETTLEMENT_OPEN = "open"
SETTLEMENT_COMPLETED = "completed"
SETTLEMENT_CANCELLED = "cancelled"
SETTLEMENT_STATUSES = (SETTLEMENT_OPEN, SETTLEMENT_COMPLETED, SETTLEMENT_CANCELLED)

# ---------------------------------------------------------------------------
# 历史数据类型
# ---------------------------------------------------------------------------
HISTORY_KIND_INFLOW = "inflow"
HISTORY_KIND_OUTFLOW = "outflow"
HISTORY_KINDS = (HISTORY_KIND_INFLOW, HISTORY_KIND_OUTFLOW)

#: 历史收付款来源种类：只用于日常到账与日常采购。
#: 房租、税款、明确退款等固定义务继续由正式 CashEvent 进入确定性账本，避免重复计入。
HISTORY_SOURCE_SETTLEMENT = "settlement"
HISTORY_SOURCE_PURCHASE = "purchase"
HISTORY_SOURCES = (HISTORY_SOURCE_SETTLEMENT, HISTORY_SOURCE_PURCHASE)

#: 只允许对历史完整日做预测的最小样本要求（来自真实算法实现，不是假标准）：
#:   seasonal_naive 需要 7 个连续完整日
#:   weekday_median 需要 28 个连续完整日（近四周同星期）
FORECAST_MIN_DAYS_SEASONAL = 7
FORECAST_MIN_DAYS_WEEKDAY_MEDIAN = 28
#: 留底建议需要的最少误差窗口数
RESERVE_MIN_BLOCKS = 8
#: 结算延迟经验值需要的最少已完成样本
SETTLEMENT_MIN_SAMPLES = 12


class MerchantAnalysisState(Base, TimestampMixin):
    """商户分析状态版本号。

    * ``ledger_revision``：任何正式 ``CashEvent`` 的金额/日期/状态/方向变化 +1
    * ``history_revision``：任何历史数据或结算记录增删改 +1

    两个版本号任一变化，既有的增强结果与留底建议都必须失效。
    """

    __tablename__ = "merchant_analysis_states"

    merchant_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("merchant_profiles.id", ondelete="CASCADE"),
        primary_key=True,
    )
    ledger_revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    history_revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    merchant: Mapped[MerchantProfile] = relationship()


class DailyCashHistory(Base, TimestampMixin):
    """一笔已确认完整的自然日历史收付。

    * 唯一约束 ``merchant_id + day``
    * ``complete`` 必须为真才可参与预测
    * ``source_refs`` 保留来源引用，供来源抽屉追溯
    """

    __tablename__ = "daily_cash_history"
    __table_args__ = (
        UniqueConstraint("merchant_id", "day", name="uq_daily_cash_history_merchant_day"),
        Index("ix_daily_cash_history_merchant_day", "merchant_id", "day"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    merchant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("merchant_profiles.id", ondelete="CASCADE"), nullable=False
    )
    #: 北京时间自然日，格式 ``YYYY-MM-DD``
    day: Mapped[str] = mapped_column(String(10), nullable=False)
    complete: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    inflow_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    outflow_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    source_refs: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    #: 导入来源标识，用于批量回滚与来源追溯
    import_batch_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True, index=True
    )
    #: 该日数据是否由「数据完整确认」推断而来（缺失交易但当日确认无收付）
    completeness_confirmed: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)

    merchant: Mapped[MerchantProfile] = relationship()


class SettlementRecord(Base, TimestampMixin):
    """结算到账配对记录：预计时间 vs 实际到账时间。

    历史统计**只使用 completed**；``open`` 单独计数，不得视为 0 天延迟。
    """

    __tablename__ = "settlement_records"
    __table_args__ = (
        UniqueConstraint(
            "merchant_id", "external_key", name="uq_settlement_records_merchant_external_key"
        ),
        Index("ix_settlement_records_merchant_channel", "merchant_id", "channel"),
        Index("ix_settlement_records_scheduled", "merchant_id", "scheduled_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    merchant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("merchant_profiles.id", ondelete="CASCADE"), nullable=False
    )
    external_key: Mapped[str] = mapped_column(String(128), nullable=False)
    channel: Mapped[str] = mapped_column(String(64), nullable=False)
    scheduled_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    actual_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    known_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default=SETTLEMENT_OPEN, nullable=False)
    source_record_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    source_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)

    merchant: Mapped[MerchantProfile] = relationship()


class EnhancementRun(Base, TimestampMixin):
    """一次增强计算的完整留档：参数、依据版本与结果。

    任一条依据变化都必须把既有 run 标为 stale，旧 run 不得继续用于确认留底。
    """

    __tablename__ = "enhancement_runs"
    __table_args__ = (
        Index("ix_enhancement_runs_merchant_created", "merchant_id", "created_at"),
        Index("ix_enhancement_runs_merchant_stale", "merchant_id", "is_stale"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    merchant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("merchant_profiles.id", ondelete="CASCADE"), nullable=False
    )
    ledger_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    history_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    basis_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    parameters_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    result_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    is_stale: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    stale_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)

    merchant: Mapped[MerchantProfile] = relationship()


class ReserveAdviceConfirmation(Base, TimestampMixin):
    """留底建议的确认记录（真实持久化，只由用户点击事件触发）。"""

    __tablename__ = "reserve_advice_confirmations"
    __table_args__ = (
        Index("ix_reserve_advice_merchant_created", "merchant_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    merchant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("merchant_profiles.id", ondelete="CASCADE"), nullable=False
    )
    previous_reserve_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    suggested_reserve_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    confirmed_reserve_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    basis_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    ledger_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    history_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    #: 依据摘要，用于事后解释「为什么建议这个金额」
    basis_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    confirmed_by: Mapped[str] = mapped_column(String(36), nullable=False)
    confirmed_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    #: 确认发生后被重新运行的分析结果
    analysis_result_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    merchant: Mapped[MerchantProfile] = relationship()


__all__ = [
    "FORECAST_MIN_DAYS_SEASONAL",
    "FORECAST_MIN_DAYS_WEEKDAY_MEDIAN",
    "HISTORY_KINDS",
    "HISTORY_KIND_INFLOW",
    "HISTORY_KIND_OUTFLOW",
    "HISTORY_SOURCES",
    "HISTORY_SOURCE_PURCHASE",
    "HISTORY_SOURCE_SETTLEMENT",
    "RESERVE_MIN_BLOCKS",
    "SETTLEMENT_COMPLETED",
    "SETTLEMENT_CANCELLED",
    "SETTLEMENT_MIN_SAMPLES",
    "SETTLEMENT_OPEN",
    "SETTLEMENT_STATUSES",
    "DailyCashHistory",
    "EnhancementRun",
    "MerchantAnalysisState",
    "ReserveAdviceConfirmation",
    "SettlementRecord",
]
