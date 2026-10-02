"""经营咨询服务中心。

关键约束：
* 咨询字段白名单：只有白名单字段可以进入咨询事项，家庭信息、完整余额、
  可提用金额、留底金额、完整现金流曲线一律不共享
* 咨询人员不能直接修改商户的收付款事项；更正必须由商户确认
* 所有状态流转写入时间线与审计日志
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import Conflict, Forbidden, NotFound, ValidationFailed
from app.models.cash import CashEvent
from app.models.consultation import (
    CASE_CLOSED,
    CASE_DRAFT,
    CASE_NEED_MORE_INFORMATION,
    CASE_STATUS_LABELS,
    CASE_SUBMITTED,
    CASE_UNDER_REVIEW,
    CASE_VERIFIED,
    CONSULTATION_ALLOWED_FIELDS,
    CONSULTATION_FORBIDDEN_FIELDS,
    QUESTION_TYPES,
    ConsultationCase,
    ConsultationUpdate,
)
from app.models.merchant import MerchantProfile
from app.models.user import ROLE_CONSULTANT, User
from app.schemas.consultation import (
    ConsultationOut,
    ConsultationUpdateOut,
)
from app.services.event_service import CashEventService
from app.utils.money import format_cny
from app.utils.timeutil import utcnow

ALLOWED_TRANSITIONS: dict[str, tuple[str, ...]] = {
    CASE_DRAFT: (CASE_SUBMITTED,),
    CASE_SUBMITTED: (CASE_UNDER_REVIEW,),
    CASE_UNDER_REVIEW: (CASE_NEED_MORE_INFORMATION, CASE_VERIFIED, CASE_CLOSED),
    CASE_NEED_MORE_INFORMATION: (CASE_UNDER_REVIEW, CASE_VERIFIED),
    CASE_VERIFIED: (CASE_CLOSED, CASE_UNDER_REVIEW),
    CASE_CLOSED: (),
}

#: 咨询人员可见的商户字段（严格白名单）
CONSULTANT_VISIBLE_FIELDS = (
    "case_no",
    "event_type",
    "event_title",
    "amount_cents",
    "scheduled_at",
    "event_state",
    "source_summary",
    "question",
    "event_version",
)


def _case_no(seq: int) -> str:
    return f"ZX{datetime.now(UTC).strftime('%Y%m%d')}{seq:04d}"


class ConsultationService:
    def __init__(self, db: Session) -> None:
        self.db = db

    # ------------------------------------------------------------------
    def _next_sequence(self) -> int:
        today = datetime.now(UTC).strftime("%Y%m%d")
        total = self.db.scalar(
            select(func.count(ConsultationCase.id)).where(
                ConsultationCase.case_no.like(f"ZX{today}%")
            )
        )
        return int(total or 0) + 1

    # ------------------------------------------------------------------
    def build_shared_fields(self, case_no: str, event: CashEvent, question: str) -> dict:
        """构造进入咨询事项的字段集合（白名单裁剪）。"""
        return {
            "case_no": case_no,
            "event_type": event.event_type,
            "event_title": event.title,
            "amount_cents": event.amount_cents,
            "scheduled_at": event.scheduled_at.isoformat(),
            "event_state": event.state,
            "source_summary": self._source_summary(event),
            "question": question[:2000],
            "event_version": event.current_version,
        }

    @staticmethod
    def _source_summary(event: CashEvent) -> str:
        labels = {
            "manual": "手工录入",
            "csv_import": "CSV 导入",
            "ai_extract": "智能录入",
            "consultation_update": "咨询更正",
        }
        base = labels.get(event.source_type, event.source_type)
        if event.source_label:
            return f"{base} · {event.source_label}"[:200]
        return base

    # ------------------------------------------------------------------
    def create_case(
        self,
        profile: MerchantProfile,
        merchant: User,
        *,
        cash_event_id: str,
        question_type: str,
        question: str,
        ai_draft: str | None,
        status: str,
    ) -> ConsultationCase:
        if question_type not in QUESTION_TYPES:
            raise ValidationFailed("不支持的问题类型", code="UNSUPPORTED_QUESTION_TYPE")

        event = self.db.get(CashEvent, cash_event_id)
        if event is None or event.merchant_id != profile.id:
            raise NotFound("收付款事项不存在")

        case_no = _case_no(self._next_sequence())
        shared = self.build_shared_fields(case_no, event, question)

        case = ConsultationCase(
            case_no=case_no,
            merchant_id=profile.id,
            cash_event_id=event.id,
            cash_event_version=event.current_version,
            question_type=question_type,
            question=question,
            ai_draft=ai_draft,
            shared_fields=shared,
            allowed_field_names=list(CONSULTATION_ALLOWED_FIELDS),
            status=CASE_DRAFT,
            provider_key="internal",
            created_by=merchant.id,
        )
        self.db.add(case)
        self.db.flush()

        self._add_update(
            case,
            actor=merchant,
            action="created",
            to_status=CASE_DRAFT,
            content="创建咨询事项",
        )

        if status == CASE_SUBMITTED:
            self.submit(case, merchant, commit=False)

        self.db.commit()
        self.db.refresh(case)
        return case

    # ------------------------------------------------------------------
    def submit(self, case: ConsultationCase, actor: User, *, commit: bool = True) -> ConsultationCase:
        self._ensure_transition(case, CASE_SUBMITTED)
        from app.services.providers import (
            ProviderSubmission,
            get_consultation_provider,
        )

        provider = get_consultation_provider(case.provider_key)
        result = provider.submit(
            ProviderSubmission(
                case_no=case.case_no,
                question_type=case.question_type,
                question=case.question,
                fields=dict(case.shared_fields or {}),
            )
        )
        case.external_reference = result.external_reference
        case.submitted_at = utcnow()
        self._add_update(
            case,
            actor=actor,
            action="submitted",
            from_status=CASE_DRAFT,
            to_status=CASE_SUBMITTED,
            content=result.message,
        )
        case.status = CASE_SUBMITTED
        if commit:
            self.db.commit()
            self.db.refresh(case)
        return case

    # ------------------------------------------------------------------
    def start_review(self, case: ConsultationCase, consultant: User) -> ConsultationCase:
        self._require_consultant(consultant)
        self._ensure_transition(case, CASE_UNDER_REVIEW)
        previous = case.status
        case.status = CASE_UNDER_REVIEW
        case.assignee_id = consultant.id
        self._add_update(
            case,
            actor=consultant,
            action="under_review",
            from_status=previous,
            to_status=CASE_UNDER_REVIEW,
            content="咨询人员已受理",
        )
        self.db.commit()
        self.db.refresh(case)
        return case

    def request_information(
        self, case: ConsultationCase, consultant: User, content: str
    ) -> ConsultationCase:
        self._require_consultant(consultant)
        self._ensure_transition(case, CASE_NEED_MORE_INFORMATION)
        previous = case.status
        case.status = CASE_NEED_MORE_INFORMATION
        case.assignee_id = case.assignee_id or consultant.id
        self._add_update(
            case,
            actor=consultant,
            action="need_more_information",
            from_status=previous,
            to_status=CASE_NEED_MORE_INFORMATION,
            content=content,
        )
        self.db.commit()
        self.db.refresh(case)
        return case

    def verify(
        self,
        case: ConsultationCase,
        consultant: User,
        *,
        resolution_summary: str,
        resolution_fields: dict | None = None,
    ) -> ConsultationCase:
        self._require_consultant(consultant)
        self._ensure_transition(case, CASE_VERIFIED)
        previous = case.status
        case.status = CASE_VERIFIED
        case.resolution_summary = resolution_summary
        case.resolution_fields = self._sanitise_resolution(resolution_fields or {})
        case.assignee_id = case.assignee_id or consultant.id
        self._add_update(
            case,
            actor=consultant,
            action="verified",
            from_status=previous,
            to_status=CASE_VERIFIED,
            content=resolution_summary,
        )
        self.db.commit()
        self.db.refresh(case)
        return case

    def close(
        self, case: ConsultationCase, actor: User, summary: str | None = None
    ) -> ConsultationCase:
        if not (actor.has_role(ROLE_CONSULTANT) or case.merchant_id):
            raise Forbidden("没有权限完成该事项")
        self._ensure_transition(case, CASE_CLOSED)
        previous = case.status
        case.status = CASE_CLOSED
        case.closed_at = utcnow()
        if summary:
            case.resolution_summary = summary
        self._add_update(
            case,
            actor=actor,
            action="closed",
            from_status=previous,
            to_status=CASE_CLOSED,
            content=summary or "事项已完成",
        )
        self.db.commit()
        self.db.refresh(case)
        return case

    # ------------------------------------------------------------------
    def apply_resolution_to_event(
        self, case: ConsultationCase, merchant: User
    ) -> tuple[CashEvent, list[str]]:
        """商户确认后，把咨询结果写回收付款事项（产生新版本并触发重算）。"""
        if case.merchant_id != self._merchant_id_of(merchant):
            raise Forbidden("没有权限更正该事项")
        if case.status not in (CASE_VERIFIED, CASE_CLOSED):
            raise Conflict("咨询尚未给出核实结果，暂时无法更正", code="CASE_NOT_VERIFIED")
        if not case.cash_event_id:
            raise ValidationFailed("该咨询没有关联收付款事项", code="NO_LINKED_EVENT")

        event = self.db.get(CashEvent, case.cash_event_id)
        if event is None or event.merchant_id != case.merchant_id:
            raise NotFound("收付款事项不存在")

        updates = self._resolution_to_event_fields(case.resolution_fields or {})
        if not updates:
            raise ValidationFailed(
                "咨询结果中没有可以直接更正的事项字段", code="NOTHING_TO_APPLY"
            )

        from app.schemas.cash_event import CashEventUpdate

        service = CashEventService(self.db)
        updated, revision = service.update_event(
            event,
            CashEventUpdate(**updates, change_reason=f"根据咨询结果更正（{case.case_no}）"),
            actor=merchant,
            commit=False,
        )
        if updated.source_type != "consultation_update":
            updated.source_type = "consultation_update"
            source = service.create_source_record(
                self.db.get(MerchantProfile, case.merchant_id),  # type: ignore[arg-type]
                source_type="consultation_update",
                created_by=merchant.id,
                raw_content=f"咨询事项 {case.case_no}：{case.resolution_summary or ''}",
            )
            updated.source_record_id = source.id
            updated.source_label = f"咨询 {case.case_no}"

        self._add_update(
            case,
            actor=merchant,
            action="applied_to_event",
            content="商户已根据咨询结果更正收付款事项并重新计算",
        )
        self.db.commit()
        self.db.refresh(updated)
        return updated, list(revision.changed_fields) if revision else []

    def _merchant_id_of(self, user: User) -> str | None:
        profile = self.db.scalar(
            select(MerchantProfile).where(MerchantProfile.user_id == user.id)
        )
        return profile.id if profile else None

    @staticmethod
    def _resolution_to_event_fields(fields: dict) -> dict:
        allowed = {"amount_cents", "scheduled_at", "state", "title", "note", "source_label"}
        result: dict = {}
        for key, value in fields.items():
            if key not in allowed or value in (None, ""):
                continue
            if key == "scheduled_at":
                from app.utils.timeutil import parse_datetime

                try:
                    result[key] = parse_datetime(value)
                except ValueError:
                    continue
            else:
                result[key] = value
        return result

    @staticmethod
    def _sanitise_resolution(fields: dict) -> dict:
        """咨询结果字段同样受白名单约束，禁止出现家庭与余额敏感字段。"""
        forbidden = set(CONSULTATION_FORBIDDEN_FIELDS)
        cleaned: dict = {}
        for key, value in fields.items():
            if key in forbidden:
                continue
            cleaned[key] = value
        return cleaned

    # ------------------------------------------------------------------
    def list_for_merchant(self, merchant_id: str, page: int, page_size: int, status: str | None):
        statement = select(ConsultationCase).where(ConsultationCase.merchant_id == merchant_id)
        if status:
            statement = statement.where(ConsultationCase.status == status)
        statement = statement.order_by(ConsultationCase.created_at.desc())
        return self._paginate(statement, page, page_size)

    def queue(
        self, *, bucket: str | None, page: int, page_size: int, consultant: User
    ) -> tuple[list[ConsultationCase], int]:
        self._require_consultant(consultant)
        statement = select(ConsultationCase).where(ConsultationCase.status != CASE_DRAFT)
        if bucket:
            statement = statement.where(ConsultationCase.status == bucket)
        statement = statement.order_by(
            ConsultationCase.submitted_at.asc().nullslast(), ConsultationCase.created_at.asc()
        )
        return self._paginate(statement, page, page_size)

    def _paginate(self, statement, page: int, page_size: int):  # noqa: ANN001
        page = max(1, int(page))
        page_size = max(1, min(100, int(page_size)))
        total = self.db.scalar(select(func.count()).select_from(statement.order_by(None).subquery())) or 0
        rows = (
            self.db.execute(statement.limit(page_size).offset((page - 1) * page_size))
            .scalars()
            .all()
        )
        return list(rows), int(total)

    # ------------------------------------------------------------------
    def require_case_for_merchant(self, case_id: str, merchant_id: str) -> ConsultationCase:
        case = self.db.get(ConsultationCase, case_id)
        if case is None or case.merchant_id != merchant_id:
            raise NotFound("咨询事项不存在")
        return case

    def require_case_for_consultant(self, case_id: str, consultant: User) -> ConsultationCase:
        self._require_consultant(consultant)
        case = self.db.get(ConsultationCase, case_id)
        if case is None:
            raise NotFound("咨询事项不存在")
        if case.status == CASE_DRAFT:
            # 草稿尚未提交，咨询人员不可见
            raise Forbidden("该咨询尚未提交")
        return case

    @staticmethod
    def _require_consultant(user: User) -> None:
        if not user.has_role(ROLE_CONSULTANT):
            raise Forbidden("只有咨询人员可以执行该操作")

    @staticmethod
    def _ensure_transition(case: ConsultationCase, target: str) -> None:
        allowed = ALLOWED_TRANSITIONS.get(case.status, ())
        if target not in allowed:
            raise Conflict(
                f"当前状态「{CASE_STATUS_LABELS.get(case.status, case.status)}」不能变更为"
                f"「{CASE_STATUS_LABELS.get(target, target)}」",
                code="INVALID_STATUS_TRANSITION",
                details={"from": case.status, "to": target},
            )

    def _add_update(
        self,
        case: ConsultationCase,
        *,
        actor: User | None,
        action: str,
        content: str | None = None,
        from_status: str | None = None,
        to_status: str | None = None,
    ) -> ConsultationUpdate:
        roles = actor.role_names() if actor else []
        role_label = "咨询人员" if ROLE_CONSULTANT in roles else ("经营者" if roles else None)
        update = ConsultationUpdate(
            case_id=case.id,
            actor_id=actor.id if actor else None,
            actor_name=actor.display_name if actor else "系统",
            actor_role=role_label,
            action=action,
            from_status=from_status,
            to_status=to_status,
            content=content,
            visible_to_merchant=True,
        )
        self.db.add(update)
        return update

    # ------------------------------------------------------------------
    def to_out(self, case: ConsultationCase) -> ConsultationOut:
        return ConsultationOut(
            id=case.id,
            case_no=case.case_no,
            merchant_id=case.merchant_id,
            cash_event_id=case.cash_event_id,
            cash_event_version=case.cash_event_version,
            question_type=case.question_type,
            question=case.question,
            ai_draft=case.ai_draft,
            shared_fields=dict(case.shared_fields or {}),
            allowed_field_names=list(case.allowed_field_names or []),
            status=case.status,
            resolution_summary=case.resolution_summary,
            resolution_fields=dict(case.resolution_fields or {}),
            submitted_at=case.submitted_at,
            closed_at=case.closed_at,
            created_at=case.created_at,
            updated_at=case.updated_at,
            updates=[
                ConsultationUpdateOut(
                    id=item.id,
                    actor_name=item.actor_name,
                    actor_role=item.actor_role,
                    action=item.action,
                    from_status=item.from_status,
                    to_status=item.to_status,
                    content=item.content,
                    created_at=item.created_at,
                )
                for item in case.updates
                if item.visible_to_merchant
            ],
        )

    # ------------------------------------------------------------------
    @staticmethod
    def allowed_fields_preview(event: CashEvent, case_no: str = "（提交后生成）") -> dict:
        return {
            "allowed_fields": list(CONSULTATION_ALLOWED_FIELDS),
            "forbidden_fields": list(CONSULTATION_FORBIDDEN_FIELDS),
            "preview": {
                "case_no": case_no,
                "event_type": event.event_type,
                "event_title": event.title,
                "amount_cents": event.amount_cents,
                "amount_text": format_cny(event.amount_cents),
                "scheduled_at": event.scheduled_at.isoformat(),
                "event_state": event.state,
                "source_summary": ConsultationService._source_summary(event),
                "event_version": event.current_version,
            },
        }
