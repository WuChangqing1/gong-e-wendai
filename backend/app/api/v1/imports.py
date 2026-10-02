"""CSV 导入接口。"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import client_ip, get_current_user, get_merchant_profile
from app.core.database import get_db
from app.core.errors import ValidationFailed
from app.models.merchant import MerchantProfile
from app.models.user import User
from app.repositories.user_repo import AuditService
from app.services.import_service import ImportService

router = APIRouter(prefix="/imports", tags=["CSV 导入"])


class ImportIssueOut(BaseModel):
    row_number: int | None = None
    field: str | None = None
    code: str
    message: str
    severity: str


class ImportPreviewRowOut(BaseModel):
    row_number: int
    cash_key: str | None = None
    title: str | None = None
    direction: str | None = None
    amount_cents: int | None = None
    scheduled_at: str | None = None
    state: str | None = None
    source_label: str | None = None
    note: str | None = None
    valid: bool
    issues: list[ImportIssueOut] = Field(default_factory=list)


class ImportPreviewOut(BaseModel):
    batch_id: str
    file_name: str
    file_type: str
    encoding: str
    delimiter: str
    total_rows: int
    valid_rows: int
    invalid_rows: int
    duplicate_rows: int
    columns: list[str]
    mapping: dict[str, str]
    unmapped_columns: list[str]
    missing_columns: list[str]
    rows: list[ImportPreviewRowOut]
    issues: list[ImportIssueOut]
    can_commit: bool
    created_at: datetime


class RemapRequest(BaseModel):
    batch_id: str
    mapping: dict[str, str]


class CommitRequest(BaseModel):
    batch_id: str


class CommitResultOut(BaseModel):
    batch_id: str
    created: int
    skipped: int
    failed: int
    issues: list[ImportIssueOut]
    created_event_ids: list[str]


@router.post(
    "/csv/preview",
    response_model=ImportPreviewOut,
    summary="上传并解析 CSV（不入库）",
)
async def upload_preview(
    request: Request,
    file: UploadFile = File(...),
    file_type: str = Form(default="transaction"),
    profile: MerchantProfile = Depends(get_merchant_profile),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ImportPreviewOut:
    """上传 → 解析 → 字段映射 → 校验 → 问题提示 → 预览。

    上传后**不会**直接写入收付款事项，必须由用户确认。
    """
    raw = await file.read()
    if not raw:
        raise ValidationFailed("上传的文件为空", code="EMPTY_FILE")

    service = ImportService(db)
    batch = service.preview_upload(
        profile,
        actor=user,
        file_name=file.filename or "upload.csv",
        raw=raw,
        file_type=file_type,
    )
    AuditService(db).record(
        "import.previewed",
        actor=user,
        resource_type="import_batch",
        resource_id=batch.id,
        merchant_id=profile.id,
        metadata={
            "file_name": batch.file_name,
            "file_type": batch.file_type,
            "total_rows": batch.total_rows,
            "valid_rows": batch.valid_rows,
        },
        ip_address=client_ip(request),
    )
    db.commit()
    return ImportPreviewOut.model_validate(service.to_preview(batch))


@router.post("/csv/remap", response_model=ImportPreviewOut, summary="调整字段映射并重新校验")
def remap(
    payload: RemapRequest,
    profile: MerchantProfile = Depends(get_merchant_profile),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ImportPreviewOut:
    service = ImportService(db)
    batch = service.require_batch(payload.batch_id, profile.id)
    updated = service.update_mapping(batch, payload.mapping)
    AuditService(db).record(
        "import.remapped",
        actor=user,
        resource_type="import_batch",
        resource_id=batch.id,
        merchant_id=profile.id,
        metadata={"mapping": updated.mapping},
    )
    db.commit()
    return ImportPreviewOut.model_validate(service.to_preview(updated))


@router.post("/csv/commit", response_model=CommitResultOut, summary="确认导入并写入事项")
def commit(
    payload: CommitRequest,
    request: Request,
    profile: MerchantProfile = Depends(get_merchant_profile),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CommitResultOut:
    service = ImportService(db)
    batch = service.require_batch(payload.batch_id, profile.id)
    result = service.commit_batch(batch, actor=user)
    AuditService(db).record(
        "import.committed",
        actor=user,
        resource_type="import_batch",
        resource_id=batch.id,
        merchant_id=profile.id,
        metadata={
            "file_name": batch.file_name,
            "created": result["created"],
            "skipped": result["skipped"],
            "failed": result["failed"],
        },
        ip_address=client_ip(request),
    )
    db.commit()
    return CommitResultOut.model_validate(result)


@router.get("/csv/templates", summary="CSV 模板")
def templates() -> dict[str, object]:
    service = ImportService
    return {
        **service.templates(),
        "samples": service.sample_rows(),
        "encodings": ["UTF-8", "UTF-8 BOM", "GB18030"],
    }


@router.get("/batches", summary="导入批次列表")
def list_batches(
    limit: int = 20,
    profile: MerchantProfile = Depends(get_merchant_profile),
    db: Session = Depends(get_db),
) -> list[dict]:
    from sqlalchemy import select

    from app.models.imports import ImportBatch

    rows = db.scalars(
        select(ImportBatch)
        .where(ImportBatch.merchant_id == profile.id)
        .order_by(ImportBatch.created_at.desc())
        .limit(min(max(limit, 1), 100))
    ).all()
    return [
        {
            "id": row.id,
            "file_name": row.file_name,
            "file_type": row.file_type,
            "status": row.status,
            "encoding": row.encoding,
            "total_rows": row.total_rows,
            "valid_rows": row.valid_rows,
            "invalid_rows": row.invalid_rows,
            "created_rows": row.created_rows,
            "created_at": row.created_at.isoformat(),
            "committed_at": row.committed_at.isoformat() if row.committed_at else None,
        }
        for row in rows
    ]
