"""现金事件与来源记录的数据访问。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Select, or_, select
from sqlalchemy.orm import Session, joinedload

from app.models.cash import CashEvent, CashEventRevision, SourceRecord
from app.models.merchant import MerchantProfile
from app.utils.timeutil import to_utc


class SourceRecordRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get(self, record_id: str) -> SourceRecord | None:
        return self.db.get(SourceRecord, record_id)

    def get_for_merchant(self, record_id: str, merchant_id: str) -> SourceRecord | None:
        return self.db.scalar(
            select(SourceRecord).where(
                SourceRecord.id == record_id, SourceRecord.merchant_id == merchant_id
            )
        )

    def create(
        self,
        *,
        merchant_id: str,
        source_type: str,
        created_by: str | None,
        file_name: str | None = None,
        stored_file_name: str | None = None,
        row_number: int | None = None,
        raw_content: str | None = None,
        content_hash: str | None = None,
        import_batch_id: str | None = None,
    ) -> SourceRecord:
        record = SourceRecord(
            merchant_id=merchant_id,
            source_type=source_type,
            file_name=file_name,
            stored_file_name=stored_file_name,
            row_number=row_number,
            raw_content=raw_content,
            content_hash=content_hash,
            import_batch_id=import_batch_id,
            created_by=created_by,
        )
        self.db.add(record)
        self.db.flush()
        return record

    def list_for_merchant(
        self, merchant_id: str, *, source_type: str | None = None, limit: int = 100
    ) -> list[SourceRecord]:
        statement = select(SourceRecord).where(SourceRecord.merchant_id == merchant_id)
        if source_type:
            statement = statement.where(SourceRecord.source_type == source_type)
        statement = statement.order_by(SourceRecord.created_at.desc()).limit(limit)
        return list(self.db.scalars(statement).all())

    def exists_batch(self, merchant_id: str, import_batch_id: str) -> bool:
        return (
            self.db.scalar(
                select(SourceRecord.id)
                .where(
                    SourceRecord.merchant_id == merchant_id,
                    SourceRecord.import_batch_id == import_batch_id,
                )
                .limit(1)
            )
            is not None
        )


class CashEventRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    # ------------------------------------------------------------------
    def get(self, event_id: str) -> CashEvent | None:
        return self.db.scalar(
            select(CashEvent).options(joinedload(CashEvent.source_record)).where(CashEvent.id == event_id)
        )

    def get_for_merchant(self, event_id: str, merchant_id: str) -> CashEvent | None:
        return self.db.scalar(
            select(CashEvent)
            .options(joinedload(CashEvent.source_record))
            .where(CashEvent.id == event_id, CashEvent.merchant_id == merchant_id)
        )

    def get_by_cash_key(self, merchant_id: str, cash_key: str) -> CashEvent | None:
        return self.db.scalar(
            select(CashEvent).where(
                CashEvent.merchant_id == merchant_id, CashEvent.cash_key == cash_key
            )
        )

    def existing_cash_keys(self, merchant_id: str, keys: list[str]) -> set[str]:
        if not keys:
            return set()
        rows = self.db.scalars(
            select(CashEvent.cash_key).where(
                CashEvent.merchant_id == merchant_id, CashEvent.cash_key.in_(keys)
            )
        ).all()
        return set(rows)

    # ------------------------------------------------------------------
    def build_query(
        self,
        merchant_id: str,
        *,
        search: str | None = None,
        direction: str | None = None,
        state: str | None = None,
        event_type: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        source_type: str | None = None,
    ) -> Select:
        statement = select(CashEvent).where(CashEvent.merchant_id == merchant_id)
        if search:
            keyword = f"%{search.strip()}%"
            statement = statement.where(
                or_(
                    CashEvent.title.like(keyword),
                    CashEvent.cash_key.like(keyword),
                    CashEvent.note.like(keyword),
                    CashEvent.source_label.like(keyword),
                )
            )
        if direction:
            statement = statement.where(CashEvent.direction == direction)
        if state:
            statement = statement.where(CashEvent.state == state)
        if event_type:
            statement = statement.where(CashEvent.event_type == event_type)
        if source_type:
            statement = statement.where(CashEvent.source_type == source_type)
        if start is not None:
            statement = statement.where(
                CashEvent.scheduled_at >= to_utc(start).replace(tzinfo=None)
            )
        if end is not None:
            statement = statement.where(
                CashEvent.scheduled_at <= to_utc(end).replace(tzinfo=None)
            )
        return statement.order_by(CashEvent.scheduled_at.asc(), CashEvent.created_at.asc())

    # ------------------------------------------------------------------
    def create(self, **kwargs) -> CashEvent:
        event = CashEvent(**kwargs)
        self.db.add(event)
        self.db.flush()
        return event

    def bump_version(self, event: CashEvent) -> int:
        event.current_version = int(event.current_version) + 1
        return event.current_version


class RevisionRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(
        self,
        *,
        cash_event_id: str,
        version: int,
        before: dict | None,
        after: dict | None,
        changed_fields: list[str],
        material: bool,
        change_reason: str | None,
        changed_by: str | None,
        changed_by_name: str | None,
        changed_at: datetime,
    ) -> CashEventRevision:
        revision = CashEventRevision(
            cash_event_id=cash_event_id,
            version=version,
            before_json=before,
            after_json=after,
            changed_fields=changed_fields,
            material=material,
            change_reason=change_reason,
            changed_by=changed_by,
            changed_by_name=changed_by_name,
            changed_at=changed_at,
        )
        self.db.add(revision)
        self.db.flush()
        return revision

    def list_for_event(self, cash_event_id: str) -> list[CashEventRevision]:
        return list(
            self.db.scalars(
                select(CashEventRevision)
                .where(CashEventRevision.cash_event_id == cash_event_id)
                .order_by(CashEventRevision.version.desc())
            ).all()
        )

    def list_for_merchant(
        self, merchant_id: str, *, limit: int = 50
    ) -> list[tuple[CashEventRevision, CashEvent]]:
        rows = self.db.execute(
            select(CashEventRevision, CashEvent)
            .join(CashEvent, CashEvent.id == CashEventRevision.cash_event_id)
            .where(CashEvent.merchant_id == merchant_id)
            .order_by(CashEventRevision.changed_at.desc())
            .limit(limit)
        ).all()
        return [(row[0], row[1]) for row in rows]

    def get_version(self, cash_event_id: str, version: int) -> CashEventRevision | None:
        return self.db.scalar(
            select(CashEventRevision).where(
                CashEventRevision.cash_event_id == cash_event_id,
                CashEventRevision.version == version,
            )
        )


def merchant_scope(db: Session, merchant_id: str) -> MerchantProfile | None:
    return db.get(MerchantProfile, merchant_id)
