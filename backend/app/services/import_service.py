"""CSV 导入服务：解析 → 映射 → 校验 → 预览 → 确认 → 写入。

任何一步失败都不会写入正式数据。写入时统一建立来源记录，保证可追溯。
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.models.cash import CashEvent
from app.models.imports import (
    BATCH_COMMITTED,
    BATCH_PREVIEW,
    FILE_TYPE_PAYMENT_PLAN,
    FILE_TYPE_TRANSACTION,
    ImportBatch,
)
from app.models.merchant import SOURCE_CSV_IMPORT, MerchantProfile
from app.models.user import User
from app.utils.csv_tools import (
    ALLOWED_EXTENSIONS,
    CsvParseError,
    auto_mapping,
    is_blank,
    normalise_direction,
    normalise_state,
    parse_csv,
)
from app.utils.money import MoneyError, format_cny, to_cents
from app.utils.timeutil import iso, parse_datetime, utcnow

DATE_FIELDS = ("event_time", "scheduled_at")

REQUIRED_FIELDS = ("title", "direction", "amount")

FIELD_LABELS = {
    "cash_key": "事项编号",
    "title": "事项名称",
    "direction": "收支方向",
    "amount": "金额",
    "event_time": "交易时间",
    "scheduled_at": "预计时间",
    "state": "状态",
    "source_label": "来源说明",
    "note": "备注",
}


@dataclass
class RowOutcome:
    row_number: int
    data: dict[str, Any] = field(default_factory=dict)
    issues: list[dict[str, Any]] = field(default_factory=list)
    valid: bool = True


def _issue(
    code: str,
    message: str,
    *,
    row_number: int | None = None,
    field_name: str | None = None,
    severity: str = "error",
) -> dict[str, Any]:
    return {
        "row_number": row_number,
        "field": field_name,
        "code": code,
        "message": message,
        "severity": severity,
    }


def _pick(record: dict[str, str], mapping: dict[str, str], field_name: str) -> str | None:
    column = mapping.get(field_name)
    if not column:
        return None
    return record.get(column)


class ImportService:
    def __init__(self, db: Session) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # 上传与解析
    # ------------------------------------------------------------------
    def preview_upload(
        self,
        profile: MerchantProfile,
        *,
        actor: User,
        file_name: str,
        raw: bytes,
        file_type: str,
    ) -> ImportBatch:
        if file_type not in (FILE_TYPE_TRANSACTION, FILE_TYPE_PAYMENT_PLAN):
            raise ValidationFailed("不支持的文件类型", code="UNSUPPORTED_FILE_TYPE")

        suffix = Path(file_name).suffix.lower()
        if suffix not in ALLOWED_EXTENSIONS:
            raise ValidationFailed(
                "只支持 CSV 格式的文件",
                code="UNSUPPORTED_FILE_EXTENSION",
                details={"allowed": list(ALLOWED_EXTENSIONS)},
            )

        max_bytes = settings.max_upload_mb * 1024 * 1024
        if len(raw) > max_bytes:
            raise ValidationFailed(
                f"文件太大，最多 {settings.max_upload_mb} MB",
                code="FILE_TOO_LARGE",
            )

        content_hash = hashlib.sha256(raw).hexdigest()

        try:
            decoded = parse_csv(raw)
        except CsvParseError as exc:
            raise ValidationFailed(str(exc), code="CSV_PARSE_FAILED") from exc

        # 原始文件名只用于展示；磁盘上使用随机文件名，避免路径穿越
        safe_stored_name = f"{secrets.token_hex(16)}{suffix}"
        target = settings.upload_path / safe_stored_name
        settings.upload_path.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)

        mapping = auto_mapping(decoded.columns)

        batch = ImportBatch(
            merchant_id=profile.id,
            file_name=Path(file_name).name[:255],
            stored_file_name=safe_stored_name,
            content_hash=content_hash,
            file_type=file_type,
            encoding=decoded.encoding,
            delimiter=decoded.delimiter,
            columns=decoded.columns,
            mapping=mapping,
            parsed_rows=[{"row_number": number, "raw": record} for number, record in decoded.rows],
            issues=[],
            total_rows=len(decoded.rows),
            status=BATCH_PREVIEW,
            created_by=actor.id,
        )
        self.db.add(batch)
        self.db.flush()

        self.recompute(batch)
        self.db.commit()
        self.db.refresh(batch)
        return batch

    # ------------------------------------------------------------------
    def require_batch(self, batch_id: str, merchant_id: str) -> ImportBatch:
        batch = self.db.get(ImportBatch, batch_id)
        if batch is None or batch.merchant_id != merchant_id:
            raise NotFound("导入批次不存在")
        return batch

    # ------------------------------------------------------------------
    def update_mapping(
        self, batch: ImportBatch, mapping: dict[str, str]
    ) -> ImportBatch:
        cleaned = {
            key: value
            for key, value in mapping.items()
            if key and value and value != "ignore" and value in batch.columns
        }
        batch.mapping = cleaned
        self.recompute(batch)
        self.db.commit()
        self.db.refresh(batch)
        return batch

    # ------------------------------------------------------------------
    def recompute(self, batch: ImportBatch) -> ImportBatch:
        """按当前映射重新校验全部行，并回写统计结果。"""
        mapping: dict[str, str] = dict(batch.mapping or {})
        issues: list[dict[str, Any]] = []

        missing = [item for item in REQUIRED_FIELDS if item not in mapping]
        if not any(item in mapping for item in DATE_FIELDS):
            missing.append("scheduled_at")
        for field_name in missing:
            issues.append(
                _issue(
                    "MISSING_COLUMN",
                    f"缺少必要字段：{FIELD_LABELS.get(field_name, field_name)}",
                    field_name=field_name,
                )
            )

        existing_keys = self._existing_cash_keys(batch.merchant_id)
        seen_in_file: dict[str, int] = {}
        outcomes: list[RowOutcome] = []

        for item in batch.parsed_rows or []:
            row_number = int(item.get("row_number") or 0)
            record: dict[str, str] = dict(item.get("raw") or {})
            outcome = self._validate_row(
                record, mapping, row_number, existing_keys, seen_in_file, batch.file_type
            )
            outcomes.append(outcome)
            for problem in outcome.issues:
                issues.append(problem)

        valid_rows = sum(1 for item in outcomes if item.valid)
        duplicate_rows = sum(
            1 for item in outcomes if any(x["code"] == "DUPLICATE_CASH_KEY" for x in item.issues)
        )

        batch.issues = issues
        batch.valid_rows = valid_rows
        batch.invalid_rows = len(outcomes) - valid_rows
        batch.duplicate_rows = duplicate_rows
        batch.parsed_rows = [
            {
                "row_number": outcome.row_number,
                "raw": dict((batch.parsed_rows or [])[index].get("raw") or {}),
                "data": outcome.data,
                "issues": outcome.issues,
                "valid": outcome.valid,
            }
            for index, outcome in enumerate(outcomes)
        ]
        return batch

    # ------------------------------------------------------------------
    def _existing_cash_keys(self, merchant_id: str) -> set[str]:
        from sqlalchemy import select

        rows = self.db.scalars(
            select(CashEvent.cash_key).where(CashEvent.merchant_id == merchant_id)
        ).all()
        return set(rows)

    def _validate_row(
        self,
        record: dict[str, str],
        mapping: dict[str, str],
        row_number: int,
        existing_keys: set[str],
        seen_in_file: dict[str, int],
        file_type: str,
    ) -> RowOutcome:
        outcome = RowOutcome(row_number=row_number)

        title_raw = _pick(record, mapping, "title")
        title = (title_raw or "").strip() if not is_blank(title_raw) else ""
        if not title:
            outcome.issues.append(
                _issue("MISSING_TITLE", "事项名称为空", row_number=row_number, field_name="title")
            )
        else:
            outcome.data["title"] = title[:128]

        direction_raw = _pick(record, mapping, "direction")
        direction = normalise_direction(direction_raw)
        if direction is None:
            message = (
                "收支方向为空"
                if is_blank(direction_raw)
                else f"无法识别的收支方向：{direction_raw}"
            )
            outcome.issues.append(
                _issue(
                    "INVALID_DIRECTION",
                    message,
                    row_number=row_number,
                    field_name="direction",
                )
            )
        else:
            outcome.data["direction"] = direction

        amount_raw = _pick(record, mapping, "amount")
        if is_blank(amount_raw):
            outcome.issues.append(
                _issue("MISSING_AMOUNT", "金额为空", row_number=row_number, field_name="amount")
            )
        else:
            try:
                cents = to_cents(amount_raw)
            except MoneyError as exc:
                outcome.issues.append(
                    _issue(
                        "INVALID_AMOUNT",
                        f"金额格式不正确：{amount_raw}",
                        row_number=row_number,
                        field_name="amount",
                    )
                )
                cents = None
                _ = exc
            if cents is not None:
                if cents < 0:
                    outcome.issues.append(
                        _issue(
                            "NEGATIVE_AMOUNT",
                            "金额不能为负数，请用收支方向表达正负",
                            row_number=row_number,
                            field_name="amount",
                        )
                    )
                else:
                    outcome.data["amount_cents"] = cents

        scheduled_raw = None
        date_field_used = None
        for candidate in ("scheduled_at", "event_time"):
            value = _pick(record, mapping, candidate)
            if not is_blank(value):
                scheduled_raw = value
                date_field_used = candidate
                break

        if scheduled_raw is None:
            outcome.issues.append(
                _issue(
                    "MISSING_TIME",
                    "时间为空",
                    row_number=row_number,
                    field_name="scheduled_at",
                )
            )
        else:
            try:
                parsed = parse_datetime(scheduled_raw)
            except ValueError:
                outcome.issues.append(
                    _issue(
                        "INVALID_TIME",
                        f"无法识别的时间格式：{scheduled_raw}",
                        row_number=row_number,
                        field_name=date_field_used,
                    )
                )
            else:
                outcome.data["scheduled_at"] = iso(parsed)

        state_raw = _pick(record, mapping, "state")
        state, recognised = normalise_state(state_raw)
        if not recognised:
            outcome.issues.append(
                _issue(
                    "INVALID_STATE",
                    f"无法识别的状态：{state_raw}",
                    row_number=row_number,
                    field_name="state",
                )
            )
        else:
            outcome.data["state"] = state

        cash_key_raw = _pick(record, mapping, "cash_key")
        cash_key = (cash_key_raw or "").strip() if not is_blank(cash_key_raw) else ""
        if cash_key:
            if cash_key in existing_keys:
                outcome.issues.append(
                    _issue(
                        "DUPLICATE_CASH_KEY",
                        f"事项编号 {cash_key} 已存在，重复上传的行将被跳过",
                        row_number=row_number,
                        field_name="cash_key",
                        severity="warning",
                    )
                )
                outcome.data["cash_key"] = cash_key
                outcome.data["duplicate"] = True
            elif cash_key in seen_in_file:
                outcome.issues.append(
                    _issue(
                        "DUPLICATE_IN_FILE",
                        f"事项编号 {cash_key} 在本文件第 {seen_in_file[cash_key]} 行已出现",
                        row_number=row_number,
                        field_name="cash_key",
                    )
                )
            else:
                seen_in_file[cash_key] = row_number
                outcome.data["cash_key"] = cash_key

        source_label = _pick(record, mapping, "source_label")
        if not is_blank(source_label):
            outcome.data["source_label"] = str(source_label).strip()[:128]

        note = _pick(record, mapping, "note")
        if not is_blank(note):
            outcome.data["note"] = str(note).strip()[:2000]

        outcome.data["file_type"] = file_type
        outcome.valid = not any(item["severity"] == "error" for item in outcome.issues)
        return outcome

    # ------------------------------------------------------------------
    def commit_batch(self, batch: ImportBatch, *, actor: User) -> dict[str, Any]:
        if batch.status == BATCH_COMMITTED:
            raise Conflict("该批次已经导入完成", code="BATCH_ALREADY_COMMITTED")

        # 提交前按最新数据重新校验，避免并发期间产生的重复
        self.recompute(batch)
        rows = batch.parsed_rows or []
        if any(
            item.get("issues")
            and any(problem["severity"] == "error" for problem in item["issues"])
            for item in rows
        ):
            self.db.commit()
            raise ValidationFailed(
                "文件中仍存在必须处理的问题，无法导入",
                code="IMPORT_HAS_ERRORS",
            )

        created: list[str] = []
        skipped = 0
        failed = 0
        issues: list[dict[str, Any]] = list(batch.issues or [])

        from app.services.event_service import CashEventService
        from app.services.cash_engine import CashEventInput  # noqa: F401  (文档用途)

        service = CashEventService(self.db)

        for item in rows:
            if not item.get("valid"):
                skipped += 1
                continue
            data = dict(item.get("data") or {})
            if data.get("duplicate"):
                skipped += 1
                continue

            row_number = int(item.get("row_number") or 0)
            try:
                payload = self._to_create_payload(data, row_number, batch)
                source = service.create_source_record(
                    profile=batch.merchant,
                    source_type=SOURCE_CSV_IMPORT,
                    created_by=actor.id,
                    file_name=batch.file_name,
                    stored_file_name=batch.stored_file_name,
                    row_number=row_number,
                    raw_content=self._raw_line(item),
                    import_batch_id=batch.id,
                )
                event = service.create_event(
                    batch.merchant,
                    payload,
                    actor=actor,
                    source_type=SOURCE_CSV_IMPORT,
                    source_record=source,
                    commit=False,
                )
                created.append(event.id)
            except (Conflict, ValidationFailed) as exc:
                failed += 1
                issues.append(
                    _issue(
                        "ROW_REJECTED",
                        f"第 {row_number} 行未写入：{exc.message}",
                        row_number=row_number,
                    )
                )

        batch.status = BATCH_COMMITTED if not failed or created else batch.status
        batch.created_rows = len(created)
        batch.committed_at = utcnow()
        batch.issues = issues
        if created:
            batch.status = BATCH_COMMITTED

        self.db.commit()

        return {
            "batch_id": batch.id,
            "created": len(created),
            "skipped": skipped,
            "failed": failed,
            "issues": issues,
            "created_event_ids": created,
        }

    # ------------------------------------------------------------------
    def _to_create_payload(self, data: dict[str, Any], row_number: int, batch: ImportBatch):
        from app.schemas.cash_event import CashEventCreate

        direction = data.get("direction")
        if direction not in ("inflow", "outflow"):
            raise ValidationFailed("收支方向缺失", code="MISSING_DIRECTION")

        amount = data.get("amount_cents")
        if amount is None:
            raise ValidationFailed("金额缺失", code="MISSING_AMOUNT")

        scheduled = data.get("scheduled_at")
        if not scheduled:
            raise ValidationFailed("时间缺失", code="MISSING_TIME")

        cash_key = data.get("cash_key") or f"CSV-{batch.id[:8]}-{row_number:05d}"
        event_type = "other_inflow" if direction == "inflow" else "other_outflow"

        return CashEventCreate(
            cash_key=cash_key,
            title=data.get("title") or "导入事项",
            direction=direction,
            amount_cents=int(amount),
            scheduled_at=datetime.fromisoformat(str(scheduled).replace("Z", "+00:00")),
            state=data.get("state") or "scheduled",
            event_type=event_type,
            source_label=data.get("source_label"),
            note=data.get("note"),
        )

    @staticmethod
    def _raw_line(item: dict[str, Any]) -> str:
        raw: dict[str, str] = dict(item.get("raw") or {})
        parts = [f"{key}={value}" for key, value in raw.items()]
        return f"CSV 第 {item.get('row_number')} 行 | " + " | ".join(parts)

    # ------------------------------------------------------------------
    def to_preview(self, batch: ImportBatch) -> dict[str, Any]:
        mapping: dict[str, str] = dict(batch.mapping or {})
        unmapped = [
            column for column in batch.columns if column not in set(mapping.values())
        ]
        missing = [item for item in REQUIRED_FIELDS if item not in mapping]
        if not any(item in mapping for item in DATE_FIELDS):
            missing.append("scheduled_at")

        rows = []
        for item in batch.parsed_rows or []:
            data = dict(item.get("data") or {})
            rows.append(
                {
                    "row_number": item.get("row_number"),
                    "cash_key": data.get("cash_key"),
                    "title": data.get("title"),
                    "direction": data.get("direction"),
                    "amount_cents": data.get("amount_cents"),
                    "scheduled_at": data.get("scheduled_at"),
                    "state": data.get("state"),
                    "source_label": data.get("source_label"),
                    "note": data.get("note"),
                    "valid": bool(item.get("valid")),
                    "issues": item.get("issues") or [],
                }
            )

        has_errors = any(
            problem.get("severity") == "error" for problem in (batch.issues or [])
        )
        return {
            "batch_id": batch.id,
            "file_name": batch.file_name,
            "file_type": batch.file_type,
            "encoding": batch.encoding,
            "delimiter": batch.delimiter,
            "total_rows": batch.total_rows,
            "valid_rows": batch.valid_rows,
            "invalid_rows": batch.invalid_rows,
            "duplicate_rows": batch.duplicate_rows,
            "columns": list(batch.columns or []),
            "mapping": mapping,
            "unmapped_columns": unmapped,
            "missing_columns": missing,
            "rows": rows,
            "issues": list(batch.issues or []),
            "can_commit": (not has_errors) and bool(batch.valid_rows or batch.duplicate_rows),
            "created_at": batch.created_at,
        }

    # ------------------------------------------------------------------
    @staticmethod
    def templates() -> dict[str, str]:
        return {
            "transaction": "cash_key,title,direction,amount,event_time,state,source_label,note",
            "payment_plan": "cash_key,title,direction,amount,scheduled_at,state,source_label,note",
        }

    @staticmethod
    def sample_rows() -> dict[str, list[str]]:
        return {
            "transaction": [
                "TX-2025-0001,门店销售收款,inflow,1234.50,2025-10-01 18:30,scheduled,收银系统,当日营业款",
                "TX-2025-0002,供应商货款,outflow,860.00,2025-10-02 10:00,scheduled,采购单 PO-8891,",
            ],
            "payment_plan": [
                "PLAN-0001,门店租金,outflow,4500.00,2025-10-05 09:00,scheduled,租赁合同,季度付款",
                "PLAN-0002,商户结算款,inflow,2358.60,2025-10-03 15:00,scheduled,结算通知 8821,",
            ],
        }


def describe_batch(batch: ImportBatch) -> str:
    stamp: datetime = batch.created_at or utcnow()
    return (
        f"{batch.file_name} · {batch.encoding} · {len(batch.parsed_rows or [])} 行 · "
        f"{stamp.astimezone(UTC).isoformat()} · 可导入 {batch.valid_rows} 行"
    )


def summarize_amounts(rows: list[dict[str, Any]]) -> str:
    total = 0
    for item in rows:
        data = item.get("data") or {}
        if data.get("direction") == "inflow":
            total += int(data.get("amount_cents") or 0)
    return format_cny(total)
