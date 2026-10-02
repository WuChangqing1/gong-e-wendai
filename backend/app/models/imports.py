"""CSV 导入批次模型。

上传的解析结果先落库为"待确认批次"，用户确认后才写入正式现金事件。
未确认的数据一律不进入正式计算。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UTCDateTime
from app.models.merchant import MerchantProfile
from app.models.user import new_id

BATCH_PREVIEW = "preview"
BATCH_COMMITTED = "committed"
BATCH_DISCARDED = "discarded"

FILE_TYPE_TRANSACTION = "transaction"
FILE_TYPE_PAYMENT_PLAN = "payment_plan"
FILE_TYPES = (FILE_TYPE_TRANSACTION, FILE_TYPE_PAYMENT_PLAN)


class ImportBatch(Base, TimestampMixin):
    __tablename__ = "import_batches"
    __table_args__ = (
        Index("ix_import_batches_merchant_created", "merchant_id", "created_at"),
        Index("ix_import_batches_status", "merchant_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    merchant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("merchant_profiles.id", ondelete="CASCADE"), nullable=False
    )
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_file_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    file_type: Mapped[str] = mapped_column(String(32), nullable=False)
    encoding: Mapped[str] = mapped_column(String(32), nullable=False)
    delimiter: Mapped[str] = mapped_column(String(4), default=",", nullable=False)
    columns: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    mapping: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    parsed_rows: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    issues: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    total_rows: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    valid_rows: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    invalid_rows: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duplicate_rows: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_rows: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default=BATCH_PREVIEW, nullable=False)
    committed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)

    merchant: Mapped[MerchantProfile] = relationship()
