"""现金事件服务：规范化、来源追溯、去重与状态变更。"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime

from sqlalchemy.orm import Session

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.models.cash import (
    CashEvent,
    CashEventRevision,
    SourceRecord,
    STATE_CANCELLED,
    STATE_INCLUDED_IN_OPENING,
    STATE_SCHEDULED,
)
from app.models.merchant import SOURCE_MANUAL, MerchantProfile
from app.models.user import User
from app.repositories.cash_event_repo import (
    CashEventRepository,
    RevisionRepository,
    SourceRecordRepository,
)
from app.schemas.cash_event import (
    CashEventCreate,
    CashEventDetail,
    CashEventOut,
    CashEventUpdate,
    RevisionDiff,
    SourceRecordOut,
    SourceSummary,
)
from app.services.cash_engine import MATERIAL_FIELDS
from app.utils.money import format_cny
from app.utils.timeutil import to_utc, utcnow

ACTION_EVENT_CREATED = "cash_event.created"
ACTION_EVENT_UPDATED = "cash_event.updated"
ACTION_EVENT_CANCELLED = "cash_event.cancelled"

#: 参与计算的字段快照（用于版本比对与结果失效判断）
REVISION_FIELDS = (
    "title",
    "cash_key",
    "event_type",
    "amount_cents",
    "direction",
    "scheduled_at",
    "state",
    "source_label",
    "note",
    "sequence_index_optional",
)

FIELD_LABELS = {
    "title": "事项名称",
    "cash_key": "事项编号",
    "event_type": "事项类型",
    "amount_cents": "金额",
    "direction": "收支方向",
    "scheduled_at": "预计时间",
    "state": "状态",
    "source_label": "来源说明",
    "note": "备注",
    "sequence_index_optional": "同刻次序",
}

DIRECTION_LABELS = {"inflow": "收入", "outflow": "支出"}
STATE_LABELS = {
    STATE_SCHEDULED: "计划中",
    STATE_INCLUDED_IN_OPENING: "已计入期初",
    STATE_CANCELLED: "已取消",
}


def generate_cash_key(prefix: str = "EV") -> str:
    """生成商户范围内唯一的事项编号。"""
    return f"{prefix}-{utcnow().strftime('%Y%m%d')}-{secrets.token_hex(4).upper()}"


def snapshot_event(event: CashEvent) -> dict:
    """可序列化的事件快照（用于版本比对，时间转 ISO 字符串）。"""
    payload: dict = {}
    for field in REVISION_FIELDS:
        value = getattr(event, field, None)
        if isinstance(value, datetime):
            payload[field] = to_utc(value).isoformat()
        else:
            payload[field] = value
    return payload


def diff_snapshots(before: dict | None, after: dict | None) -> list[dict]:
    """返回逐字段差异（含中文标签与可读值）。"""
    before = before or {}
    after = after or {}
    changes: list[dict] = []
    for field in REVISION_FIELDS:
        old = before.get(field)
        new = after.get(field)
        if old == new:
            continue
        changes.append(
            {
                "field": field,
                "label": FIELD_LABELS.get(field, field),
                "before": old,
                "after": new,
                "before_text": render_value(field, old),
                "after_text": render_value(field, new),
                "material": field in MATERIAL_FIELDS,
            }
        )
    return changes


def render_value(field: str, value) -> str | None:  # noqa: ANN001
    if value is None:
        return None
    if field == "amount_cents":
        return format_cny(int(value))
    if field == "direction":
        return DIRECTION_LABELS.get(str(value), str(value))
    if field == "state":
        return STATE_LABELS.get(str(value), str(value))
    if field == "scheduled_at":
        try:
            from app.utils.timeutil import as_utc, to_local

            parsed = as_utc(datetime.fromisoformat(str(value).replace("Z", "+00:00")))
            local = to_local(parsed)
            return local.strftime("%Y-%m-%d %H:%M") if local else str(value)
        except (TypeError, ValueError):
            return str(value)
    return str(value)


class CashEventService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.events = CashEventRepository(db)
        self.revisions = RevisionRepository(db)
        self.sources = SourceRecordRepository(db)

    # ------------------------------------------------------------------
    # 读取
    # ------------------------------------------------------------------
    def require_event(self, event_id: str, merchant_id: str) -> CashEvent:
        event = self.events.get_for_merchant(event_id, merchant_id)
        if event is None:
            raise NotFound("收付款事项不存在")
        return event

    def to_out(self, event: CashEvent) -> CashEventOut:
        return self.build_out(event)

    def build_out(self, event: CashEvent) -> CashEventOut:
        source = None
        record: SourceRecord | None = event.source_record
        source = SourceSummary(
            source_type=event.source_type,
            source_label=event.source_label,
            file_name=record.file_name if record else None,
            row_number=record.row_number if record else None,
            created_at=record.created_at if record else event.created_at,
        )
        return CashEventOut(
            id=event.id,
            merchant_id=event.merchant_id,
            cash_key=event.cash_key,
            event_type=event.event_type,
            title=event.title,
            amount_cents=event.amount_cents,
            direction=event.direction,
            scheduled_at=event.scheduled_at,
            state=event.state,
            source_type=event.source_type,
            source_label=event.source_label,
            source_record_id=event.source_record_id,
            note=event.note,
            sequence_index_optional=event.sequence_index_optional,
            confirmed=event.confirmed,
            current_version=event.current_version,
            created_by=event.created_by,
            created_at=event.created_at,
            updated_at=event.updated_at,
            source=source,
        )

    def build_detail(self, event: CashEvent) -> CashEventDetail:
        base = self.build_out(event)
        record = event.source_record
        return CashEventDetail(
            **base.model_dump(),
            source_record=SourceRecordOut.model_validate(record) if record else None,
        )

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------
    def create_event(
        self,
        profile: MerchantProfile,
        payload: CashEventCreate,
        *,
        actor: User,
        source_type: str = SOURCE_MANUAL,
        source_record: SourceRecord | None = None,
        commit: bool = True,
    ) -> CashEvent:
        cash_key = (payload.cash_key or "").strip() or generate_cash_key(
            "IN" if payload.direction == "inflow" else "OUT"
        )
        if self.events.get_by_cash_key(profile.id, cash_key) is not None:
            raise Conflict(
                "该收付款事项已经存在",
                code="DUPLICATE_CASH_KEY",
                details={"field": "cash_key", "cash_key": cash_key},
            )

        event = self.events.create(
            merchant_id=profile.id,
            cash_key=cash_key,
            event_type=payload.event_type,
            title=payload.title.strip(),
            amount_cents=int(payload.amount_cents),
            direction=payload.direction,
            scheduled_at=to_utc(payload.scheduled_at),
            state=payload.state,
            source_type=source_type,
            source_label=payload.source_label,
            source_record_id=source_record.id if source_record else None,
            note=payload.note,
            sequence_index_optional=payload.sequence_index_optional,
            confirmed=True,
            current_version=1,
            created_by=actor.id,
        )

        # 每个事项都必须可追溯到来源：即使手工录入也建立一条来源记录。
        if source_record is None:
            source_record = self.create_source_record(
                profile,
                source_type=source_type,
                created_by=actor.id,
                file_name=None,
                row_number=None,
                raw_content=self._manual_raw_content(payload, actor, cash_key),
                import_batch_id=None,
            )
            event.source_record_id = source_record.id
            self.db.flush()

        self.revisions.create(
            cash_event_id=event.id,
            version=1,
            before=None,
            after=snapshot_event(event),
            changed_fields=list(REVISION_FIELDS),
            material=True,
            change_reason="创建事项",
            changed_by=actor.id,
            changed_by_name=actor.display_name,
            changed_at=utcnow(),
        )

        from app.services.analysis_service import AnalysisService
        from app.services.enhancement_service import EnhancementService

        AnalysisService(self.db).mark_stale(profile.id, reason="新增收付款事项")
        # 账本口径变化：增强结果的 ledger_revision 必须前进
        enhancement = EnhancementService(self.db)
        enhancement.bump_ledger_revision(profile.id, commit=False)
        enhancement.mark_runs_stale(profile.id, "收付款事项已变化")

        if commit:
            self.db.commit()
            self.db.refresh(event)
        return event

    # ------------------------------------------------------------------
    def update_event(
        self,
        event: CashEvent,
        payload: CashEventUpdate,
        *,
        actor: User,
        commit: bool = True,
    ) -> tuple[CashEvent, CashEventRevision | None]:
        data = payload.model_dump(exclude_unset=True)
        reason = data.pop("change_reason", None)

        before = snapshot_event(event)
        for key, value in data.items():
            if key == "scheduled_at" and value is not None:
                setattr(event, key, to_utc(value))
            elif value is not None:
                setattr(event, key, value)

        after = snapshot_event(event)
        if before == after:
            if commit:
                self.db.commit()
                self.db.refresh(event)
            return event, None

        changed_fields = [item["field"] for item in diff_snapshots(before, after)]
        material = any(field in MATERIAL_FIELDS for field in changed_fields)

        self.events.bump_version(event)
        revision = self.revisions.create(
            cash_event_id=event.id,
            version=event.current_version,
            before=before,
            after=after,
            changed_fields=changed_fields,
            material=material,
            change_reason=reason or "修改事项",
            changed_by=actor.id,
            changed_by_name=actor.display_name,
            changed_at=utcnow(),
        )

        if material:
            from app.services.analysis_service import AnalysisService
            from app.services.enhancement_service import EnhancementService

            AnalysisService(self.db).mark_stale(
                event.merchant_id, reason="收付款事项发生变更"
            )
            enhancement = EnhancementService(self.db)
            enhancement.bump_ledger_revision(event.merchant_id, commit=False)
            enhancement.mark_runs_stale(event.merchant_id, "收付款事项已变化")

        if commit:
            self.db.commit()
            self.db.refresh(event)
        return event, revision

    # ------------------------------------------------------------------
    def cancel_event(
        self, event: CashEvent, *, actor: User, reason: str | None = None, commit: bool = True
    ) -> CashEvent:
        """取消事项：state = cancelled，绝不物理删除历史业务记录。"""
        if event.state == STATE_CANCELLED:
            raise ValidationFailed("该事项已经是取消状态", code="ALREADY_CANCELLED")

        before = snapshot_event(event)
        event.state = STATE_CANCELLED
        after = snapshot_event(event)

        self.events.bump_version(event)
        self.revisions.create(
            cash_event_id=event.id,
            version=event.current_version,
            before=before,
            after=after,
            changed_fields=["state"],
            material=True,
            change_reason=reason or "取消事项",
            changed_by=actor.id,
            changed_by_name=actor.display_name,
            changed_at=utcnow(),
        )

        from app.services.analysis_service import AnalysisService
        from app.services.enhancement_service import EnhancementService

        AnalysisService(self.db).mark_stale(event.merchant_id, reason="收付款事项被取消")
        enhancement = EnhancementService(self.db)
        enhancement.bump_ledger_revision(event.merchant_id, commit=False)
        enhancement.mark_runs_stale(event.merchant_id, "收付款事项已变化")

        if commit:
            self.db.commit()
            self.db.refresh(event)
        return event

    # ------------------------------------------------------------------
    def build_revision_diffs(self, event: CashEvent) -> list[RevisionDiff]:
        rows = self.revisions.list_for_event(event.id)
        diffs: list[RevisionDiff] = []
        for row in rows:
            diffs.append(
                RevisionDiff(
                    version=row.version,
                    changed_fields=list(row.changed_fields or []),
                    material=row.material,
                    change_reason=row.change_reason,
                    changed_by_name=row.changed_by_name,
                    changed_at=row.changed_at,
                    before=row.before_json,
                    after=row.after_json,
                    changes=diff_snapshots(row.before_json, row.after_json),
                )
            )
        return diffs

    # ------------------------------------------------------------------
    @staticmethod
    def _manual_raw_content(payload: CashEventCreate, actor: User, cash_key: str) -> str:
        """手工录入也保留一份原始内容，保证来源抽屉有据可查。"""
        return (
            f"手工录入 | 操作人={actor.display_name} | 事项编号={cash_key} | "
            f"名称={payload.title} | 方向={DIRECTION_LABELS.get(payload.direction, payload.direction)} | "
            f"金额={format_cny(int(payload.amount_cents))} | 预计时间={to_utc(payload.scheduled_at).isoformat()}"
        )

    def create_source_record(
        self,
        profile: MerchantProfile,
        *,
        source_type: str,
        created_by: str,
        file_name: str | None = None,
        stored_file_name: str | None = None,
        row_number: int | None = None,
        raw_content: str | None = None,
        import_batch_id: str | None = None,
    ) -> SourceRecord:
        content_hash = None
        if raw_content:
            content_hash = hashlib.sha256(raw_content.encode("utf-8")).hexdigest()
        return self.sources.create(
            merchant_id=profile.id,
            source_type=source_type,
            created_by=created_by,
            file_name=file_name,
            stored_file_name=stored_file_name,
            row_number=row_number,
            raw_content=raw_content,
            content_hash=content_hash,
            import_batch_id=import_batch_id,
        )
