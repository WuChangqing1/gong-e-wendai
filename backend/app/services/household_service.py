"""家庭协同服务：家庭、成员、协同卡、反馈与评论。

隐私边界：家庭成员默认无法访问收付款事项、经营账户、分析全量数据与经营咨询，
只能看到商户明确分享的协同卡片。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import Conflict, Forbidden, NotFound, ValidationFailed
from app.core.security import generate_invite_code
from app.models.cash import AnalysisResult, CashEvent
from app.models.household import (
    CARD_DECISION,
    CARD_REVISION,
    CARD_RISK,
    MEMBERSHIP_ACTIVE,
    MEMBERSHIP_PENDING,
    MEMBERSHIP_REMOVED,
    REACTION_READ,
    Household,
    HouseholdCard,
    HouseholdCardComment,
    HouseholdCardReaction,
    HouseholdCardRecipient,
    HouseholdMembership,
)
from app.models.merchant import MerchantProfile
from app.models.user import User
from app.schemas.household import (
    CardCommentOut,
    CardOut,
    CardRecipientOut,
    HouseholdOut,
    MemberOut,
)
from app.utils.money import format_cny
from app.utils.timeutil import utcnow

#: 允许分享的字段白名单
#
# 这是**服务端**白名单：只有出现在这里的字段才可能出现在持久化的 payload 里，
# 接收端永远只能读取过滤后的 payload，不能根据 ``cash_event_id`` 再补全未共享字段。
#
# 每个字段只产出下表列出的键，**不做隐式带出**：勾选「最紧张时间」不会顺带给出
# 最紧时点余额，勾选「风险摘要」不会顺带给出缺口金额。这样「用户勾了什么」
# 与「家人能看到什么」严格一一对应。
SHAREABLE_FIELDS = (
    "max_withdrawable",
    "planned_amount",
    "limiting_point",
    "limiting_balance",
    "end_balance",
    "key_payments",
    "risk_summary",
    "payment_gap",
    "buffer_gap",
    "pending_inflows",
    "revision_summary",
)

#: 默认不勾选的敏感字段
SENSITIVE_FIELDS = (
    "opening_balance",
    "all_transactions",
    "full_csv",
    "consultations",
)

FIELD_LABELS = {
    "max_withdrawable": "今日可提用金额",
    "planned_amount": "计划家庭提用金额",
    "limiting_point": "最紧张时间",
    "limiting_balance": "最紧时点余额",
    "end_balance": "期末余额",
    "key_payments": "关键经营付款",
    "risk_summary": "风险摘要",
    "payment_gap": "付款缺口",
    "buffer_gap": "留底缺口",
    "pending_inflows": "尚未到账的收入",
    "revision_summary": "事项变更摘要",
}

#: 字段 → 产出的 payload 键。**逐项对应**，没有勾选就不生成。
#: 事项级字段（event_*）只在 ``key_payments`` 下生成；``revision_summary`` 只允许
#: 接收端知道「有一笔事项发生过变更」，不提供任何事项细节。
SHARED_FIELD_OUTPUTS: dict[str, tuple[str, ...]] = {
    "max_withdrawable": ("max_withdrawable_cents",),
    "planned_amount": ("planned_household_amount_cents",),
    "limiting_point": ("limiting_timestamp",),
    "limiting_balance": ("limiting_balance_cents",),
    "end_balance": ("end_balance_cents",),
    "key_payments": ("key_payments", "limiting_event_title"),
    "risk_summary": ("risk_summary", "status"),
    "payment_gap": ("payment_gap_cents",),
    "buffer_gap": ("buffer_gap_cents",),
    "pending_inflows": ("pending_inflows",),
    "revision_summary": (),
}

#: 事项级字段的产物；只有勾选 ``key_payments`` 才会出现。
#: ``event_title`` / ``event_amount_cents`` / ``event_scheduled_at`` / ``event_version``
#: 属于事项细节，不属于「变更摘要」，因此不由 ``revision_summary`` 带出。
EVENT_FIELD_GATE = "key_payments"
EVENT_FIELD_OUTPUTS = (
    "event_title",
    "event_amount_cents",
    "event_scheduled_at",
    "event_version",
)

MEMBERSHIP_STATUS_LABEL = {
    MEMBERSHIP_PENDING: "待确认",
    MEMBERSHIP_ACTIVE: "已加入",
    MEMBERSHIP_REMOVED: "已移除",
}


class HouseholdService:
    def __init__(self, db: Session) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # 家庭与成员
    # ------------------------------------------------------------------
    def get_for_owner(self, merchant_id: str) -> Household | None:
        return self.db.scalar(
            select(Household).where(Household.merchant_id == merchant_id).limit(1)
        )

    def require_for_owner(self, merchant_id: str) -> Household:
        household = self.get_for_owner(merchant_id)
        if household is None:
            raise NotFound("尚未创建家庭")
        return household

    def create_household(self, profile: MerchantProfile, owner: User, name: str) -> HouseholdOut:
        existing = self.get_for_owner(profile.id)
        if existing is not None:
            raise Conflict("已经创建过家庭", code="HOUSEHOLD_EXISTS")

        household = Household(
            name=name.strip(),
            owner_id=owner.id,
            merchant_id=profile.id,
            invite_code=self._unique_invite_code(),
            invite_code_active=True,
        )
        self.db.add(household)
        self.db.flush()

        # 商户本人也是家庭成员（owner 角色），但不是"被分享对象"
        self.db.add(
            HouseholdMembership(
                household_id=household.id,
                user_id=owner.id,
                role="owner",
                status=MEMBERSHIP_ACTIVE,
                joined_at=utcnow(),
                decided_by=owner.id,
                decided_at=utcnow(),
            )
        )
        self.db.commit()
        self.db.refresh(household)
        return self.to_out(household, viewer=owner)

    def _unique_invite_code(self) -> str:
        for _ in range(20):
            code = generate_invite_code()
            exists = self.db.scalar(select(Household.id).where(Household.invite_code == code))
            if exists is None:
                return code
        raise Conflict("邀请码生成失败，请重试", code="INVITE_CODE_FAILED")

    def rotate_invite_code(self, household: Household) -> str:
        household.invite_code = self._unique_invite_code()
        household.invite_code_rotated_at = utcnow()
        household.invite_code_active = True
        self.db.commit()
        self.db.refresh(household)
        return household.invite_code

    def join_by_invite(self, user: User, invite_code: str, relation_label: str | None) -> tuple[Household, HouseholdMembership]:
        household = self.db.scalar(
            select(Household).where(Household.invite_code == invite_code.strip().upper())
        )
        if household is None or not household.invite_code_active:
            raise NotFound("邀请码无效或已失效", code="INVALID_INVITE_CODE")

        existing = self.db.scalar(
            select(HouseholdMembership).where(
                HouseholdMembership.household_id == household.id,
                HouseholdMembership.user_id == user.id,
            )
        )
        if existing is not None:
            if existing.status == MEMBERSHIP_ACTIVE:
                raise Conflict("你已经加入该家庭", code="ALREADY_MEMBER")
            if existing.status == MEMBERSHIP_PENDING:
                raise Conflict("你的加入申请正在等待确认", code="MEMBERSHIP_PENDING")
            existing.status = MEMBERSHIP_PENDING
            existing.relation_label = relation_label
            self.db.commit()
            self.db.refresh(existing)
            return household, existing

        membership = HouseholdMembership(
            household_id=household.id,
            user_id=user.id,
            role="member",
            relation_label=relation_label,
            status=MEMBERSHIP_PENDING,
        )
        self.db.add(membership)
        self.db.commit()
        self.db.refresh(membership)
        return household, membership

    def list_members(self, household: Household) -> list[HouseholdMemberRow]:
        rows = self.db.scalars(
            select(HouseholdMembership)
            .where(HouseholdMembership.household_id == household.id)
            .order_by(HouseholdMembership.created_at.asc())
        ).all()
        result: list[HouseholdMemberRow] = []
        for item in rows:
            user = self.db.get(User, item.user_id)
            if user is None:
                continue
            result.append(HouseholdMemberRow(membership=item, user=user))
        return result

    def approve_member(self, household: Household, membership_id: str, actor: User) -> HouseholdMembership:
        membership = self.db.get(HouseholdMembership, membership_id)
        if membership is None or membership.household_id != household.id:
            raise NotFound("成员申请不存在")
        if membership.status == MEMBERSHIP_ACTIVE:
            raise Conflict("该成员已经加入", code="ALREADY_ACTIVE")
        membership.status = MEMBERSHIP_ACTIVE
        membership.joined_at = utcnow()
        membership.decided_by = actor.id
        membership.decided_at = utcnow()
        self.db.commit()
        self.db.refresh(membership)
        return membership

    def remove_member(self, household: Household, membership_id: str, actor: User) -> HouseholdMembership:
        membership = self.db.get(HouseholdMembership, membership_id)
        if membership is None or membership.household_id != household.id:
            raise NotFound("成员不存在")
        if membership.user_id == household.owner_id:
            raise ValidationFailed("不能移除家庭创建者", code="CANNOT_REMOVE_OWNER")
        membership.status = MEMBERSHIP_REMOVED
        membership.decided_by = actor.id
        membership.decided_at = utcnow()
        self.db.commit()
        self.db.refresh(membership)
        return membership

    def active_member_ids(self, household: Household) -> list[str]:
        rows = self.db.scalars(
            select(HouseholdMembership.user_id).where(
                HouseholdMembership.household_id == household.id,
                HouseholdMembership.status == MEMBERSHIP_ACTIVE,
                HouseholdMembership.user_id != household.owner_id,
            )
        ).all()
        return list(rows)

    def memberships_for_user(self, user_id: str) -> list[tuple[HouseholdMembership, Household]]:
        rows = self.db.execute(
            select(HouseholdMembership, Household)
            .join(Household, Household.id == HouseholdMembership.household_id)
            .where(
                HouseholdMembership.user_id == user_id,
                HouseholdMembership.status != MEMBERSHIP_REMOVED,
            )
        ).all()
        return [(row[0], row[1]) for row in rows]

    # ------------------------------------------------------------------
    # 协同卡
    # ------------------------------------------------------------------
    def build_payload(
        self,
        *,
        card_type: str,
        shared_fields: list[str],
        analysis: AnalysisResult | None,
        cash_event: CashEvent | None,
        planned_amount_cents: int | None,
    ) -> dict:
        """按服务端白名单生成分享数据包。

        关键约束：

        * **没有勾选的字段，这里根本不会生成** —— 接收端只能读到过滤后的 payload，
          不能通过 ``cash_event_id`` 反查补全。
        * **逐项对应** —— 每个勾选项只产出 :data:`SHARED_FIELD_OUTPUTS` 里列出的键，
          不做隐式带出（勾「最紧张时间」不会顺带给出最紧时点余额）。
        """
        payload: dict = {}
        fields = [item for item in shared_fields if item in SHAREABLE_FIELDS]

        def wanted(field: str) -> bool:
            return field in fields

        if analysis is not None:
            if wanted("max_withdrawable"):
                payload["max_withdrawable_cents"] = analysis.max_withdrawable_cents

            if wanted("limiting_point") and analysis.limiting_timestamp is not None:
                payload["limiting_timestamp"] = analysis.limiting_timestamp.isoformat()

            if wanted("limiting_balance"):
                payload["limiting_balance_cents"] = analysis.limiting_balance_cents

            if wanted("end_balance"):
                payload["end_balance_cents"] = self._end_balance(analysis)

            if wanted("risk_summary"):
                payload["risk_summary"] = self._risk_summary(analysis)
                payload["status"] = analysis.status

            if wanted("payment_gap"):
                payload["payment_gap_cents"] = analysis.payment_gap_cents

            if wanted("buffer_gap"):
                payload["buffer_gap_cents"] = analysis.buffer_gap_cents

            if wanted("key_payments"):
                payload["key_payments"] = self._key_payments(analysis)
                # 最紧时点的事项名称属于事项级信息
                title = self._key_payment_title(analysis)
                if title:
                    payload["limiting_event_title"] = title

            if wanted("pending_inflows"):
                payload["pending_inflows"] = [
                    {
                        "title": item.title,
                        "amount_text": item.amount_text,
                        "scheduled_at": item.scheduled_at.isoformat(),
                    }
                    for item in analysis.pending_inflows_at_limit
                ]

        # 事项级字段只由「关键经营付款」带出；变更摘要不提供事项细节。
        if cash_event is not None and wanted(EVENT_FIELD_GATE):
            payload["event_title"] = cash_event.title
            payload["event_amount_cents"] = cash_event.amount_cents
            payload["event_scheduled_at"] = cash_event.scheduled_at.isoformat()
            payload["event_version"] = cash_event.current_version

        if planned_amount_cents is not None and wanted("planned_amount"):
            payload["planned_household_amount_cents"] = int(planned_amount_cents)

        if card_type == CARD_RISK and wanted("risk_summary") and analysis is not None:
            payload.setdefault("risk_summary", self._risk_summary(analysis))

        return payload

    @staticmethod
    def _scenario_zero(analysis: AnalysisResult) -> dict:
        """权威情景。

        共同约束模式的 ``scenarios[0]`` 是「按当前计划」，而顶层结论可能绑定
        另一个情景。``payload`` 现在记录了 ``binding_scenario_index``，这里优先用它，
        保证分享出去的数字与用户看到的结论来自同一个情景。
        """
        payload = analysis.payload or {}
        scenarios = payload.get("scenarios") or []
        if not isinstance(scenarios, list):
            return {}
        index = payload.get("binding_scenario_index")
        if isinstance(index, int) and 0 <= index < len(scenarios):
            candidate = scenarios[index]
            if isinstance(candidate, dict):
                return candidate
        if scenarios and isinstance(scenarios[0], dict):
            return scenarios[0]
        return {}

    @classmethod
    def _end_balance(cls, analysis: AnalysisResult) -> int | None:
        """期末余额：取自引擎结果（场景 0），不重新计算。"""
        value = cls._scenario_zero(analysis).get("end_balance_cents")
        return int(value) if isinstance(value, int) else None

    @staticmethod
    def _risk_summary(analysis: AnalysisResult) -> str:
        if analysis.payment_gap_cents > 0:
            return (
                f"未来 7 天会出现付款缺口 {format_cny(analysis.payment_gap_cents)}，"
                f"最紧张时点余额 {format_cny(analysis.limiting_balance_cents)}。"
            )
        if analysis.buffer_gap_cents > 0:
            return (
                f"未来 7 天余额将低于经营留底，留底缺口 {format_cny(analysis.buffer_gap_cents)}。"
            )
        return "未来 7 天已确认的收付款事项都可以覆盖，资金安排可行。"

    @staticmethod
    def _key_payment_title(analysis: AnalysisResult) -> str | None:
        payload = analysis.payload or {}
        title = payload.get("limiting_event_title")
        if title:
            return str(title)
        scenarios = payload.get("scenarios") or []
        if scenarios and isinstance(scenarios, list):
            points = scenarios[0].get("points") or []
            limiting_ts = analysis.limiting_timestamp.isoformat() if analysis.limiting_timestamp else None
            for point in points:
                if point.get("timestamp") == limiting_ts and point.get("event_title"):
                    return str(point["event_title"])
        return None

    @staticmethod
    def _key_payments(analysis: AnalysisResult) -> list[dict]:
        payload = analysis.payload or {}
        scenarios = payload.get("scenarios") or []
        if not scenarios:
            return []
        points = scenarios[0].get("points") or []
        outflow_points = [
            {
                "title": point.get("event_title"),
                "amount_text": point.get("delta_text"),
                "scheduled_at": point.get("timestamp"),
                "balance_text": point.get("balance_text"),
            }
            for point in points
            if point.get("direction") == "outflow"
        ]
        return outflow_points[:5]

    def build_preview(
        self,
        profile: MerchantProfile,
        *,
        card_type: str,
        shared_fields: list[str],
        analysis_result_id: str | None,
        cash_event_id: str | None,
        planned_amount_cents: int | None,
        analysis_override: AnalysisResult | None = None,
    ) -> tuple[str, str, dict, list[str]]:
        fields = [item for item in shared_fields if item in SHAREABLE_FIELDS]
        if not fields:
            raise ValidationFailed("请至少选择一个要分享的内容", code="NO_SHARED_FIELD")

        analysis = analysis_override
        if analysis is None and analysis_result_id:
            analysis = self.db.get(AnalysisResult, analysis_result_id)
            if analysis is not None and analysis.merchant_id != profile.id:
                raise NotFound("分析结果不存在")
        if analysis is None:
            analysis = self.db.scalar(
                select(AnalysisResult)
                .where(AnalysisResult.merchant_id == profile.id)
                .order_by(AnalysisResult.created_at.desc())
                .limit(1)
            )

        cash_event = None
        if cash_event_id:
            cash_event = self.db.get(CashEvent, cash_event_id)
            if cash_event is not None and cash_event.merchant_id != profile.id:
                raise NotFound("收付款事项不存在")

        payload = self.build_payload(
            card_type=card_type,
            shared_fields=fields,
            analysis=analysis,
            cash_event=cash_event,
            planned_amount_cents=planned_amount_cents,
        )

        title = {
            CARD_DECISION: "家庭提用决策确认",
            CARD_RISK: "资金风险提醒",
            CARD_REVISION: "事项变更通知",
        }.get(card_type, "家庭协同卡")

        if cash_event is not None and card_type == CARD_REVISION:
            # 标题只在勾选了「关键经营付款」（事项级字段的唯一入口）时才带事项名称。
            # 未勾选时用通用标题，避免接收端仅凭卡片标题拿到未共享的事项信息。
            event_shared = EVENT_FIELD_GATE in fields
            title = (
                f"事项变更通知：{cash_event.title}" if event_shared else "事项变更通知"
            )

        summary_parts: list[str] = []
        if "max_withdrawable_cents" in payload:
            value = payload["max_withdrawable_cents"]
            summary_parts.append(
                "今日可提用金额 " + (format_cny(value) if isinstance(value, int) else "暂不可计算")
            )
        if "planned_household_amount_cents" in payload:
            summary_parts.append(
                "计划家庭提用 " + format_cny(payload["planned_household_amount_cents"])
            )
        if "limiting_timestamp" in payload and payload["limiting_timestamp"]:
            summary_parts.append("最紧张时间 " + str(payload["limiting_timestamp"])[:16].replace("T", " "))
        if "risk_summary" in payload:
            summary_parts.append(str(payload["risk_summary"]))
        if "key_payments" in payload and payload["key_payments"]:
            first = payload["key_payments"][0]
            summary_parts.append(f"关键付款：{first.get('title')} {first.get('amount_text')}")

        return title, "；".join(summary_parts), payload, fields

    def create_card(
        self,
        profile: MerchantProfile,
        household: Household,
        owner: User,
        *,
        card_type: str,
        shared_fields: list[str],
        title: str | None,
        summary: str | None,
        analysis_result_id: str | None,
        cash_event_id: str | None,
        planned_amount_cents: int | None,
    ) -> HouseholdCard:
        member_ids = self.active_member_ids(household)
        if not member_ids:
            raise ValidationFailed(
                "还没有已加入的家庭成员，请先邀请并确认", code="NO_ACTIVE_MEMBER"
            )

        resolved_title, resolved_summary, payload, fields = self.build_preview(
            profile,
            card_type=card_type,
            shared_fields=shared_fields,
            analysis_result_id=analysis_result_id,
            cash_event_id=cash_event_id,
            planned_amount_cents=planned_amount_cents,
        )

        card = HouseholdCard(
            household_id=household.id,
            merchant_id=profile.id,
            card_type=card_type,
            title=(title or resolved_title)[:128],
            summary=(summary or resolved_summary)[:2000],
            payload=payload,
            shared_fields=fields,
            analysis_result_id=analysis_result_id,
            cash_event_id=cash_event_id,
            planned_household_amount_cents=planned_amount_cents,
            system_max_withdrawable_cents=payload.get("max_withdrawable_cents"),
            created_by=owner.id,
        )
        self.db.add(card)
        self.db.flush()

        for user_id in member_ids:
            self.db.add(HouseholdCardRecipient(card_id=card.id, user_id=user_id))

        self.db.commit()
        self.db.refresh(card)
        return card

    # ------------------------------------------------------------------
    def list_cards_for_owner(self, merchant_id: str, *, card_type: str | None = None) -> list[HouseholdCard]:
        statement = select(HouseholdCard).where(HouseholdCard.merchant_id == merchant_id)
        if card_type:
            statement = statement.where(HouseholdCard.card_type == card_type)
        statement = statement.order_by(HouseholdCard.created_at.desc())
        return list(self.db.scalars(statement).all())

    def list_cards_for_recipient(
        self, user_id: str, *, card_type: str | None = None, unread_only: bool = False
    ) -> list[HouseholdCard]:
        statement = (
            select(HouseholdCard)
            .join(HouseholdCardRecipient, HouseholdCardRecipient.card_id == HouseholdCard.id)
            .where(HouseholdCardRecipient.user_id == user_id)
        )
        if card_type:
            statement = statement.where(HouseholdCard.card_type == card_type)
        if unread_only:
            statement = statement.where(HouseholdCardRecipient.is_read.is_(False))
        statement = statement.order_by(HouseholdCard.created_at.desc())
        return list(self.db.scalars(statement).all())

    def require_card_for_owner(self, card_id: str, merchant_id: str) -> HouseholdCard:
        card = self.db.get(HouseholdCard, card_id)
        if card is None or card.merchant_id != merchant_id:
            raise NotFound("协同卡片不存在")
        return card

    def require_card_for_recipient(self, card_id: str, user_id: str) -> tuple[HouseholdCard, HouseholdCardRecipient]:
        recipient = self.db.scalar(
            select(HouseholdCardRecipient).where(
                HouseholdCardRecipient.card_id == card_id,
                HouseholdCardRecipient.user_id == user_id,
            )
        )
        if recipient is None:
            raise Forbidden("你没有查看这张卡片的权限")
        card = self.db.get(HouseholdCard, card_id)
        if card is None:
            raise NotFound("协同卡片不存在")
        return card, recipient

    def mark_read(self, card: HouseholdCard, recipient: HouseholdCardRecipient) -> HouseholdCard:
        if not recipient.is_read:
            recipient.is_read = True
            recipient.read_at = utcnow()
        self._record_reaction(card, recipient.user_id, REACTION_READ)
        self.db.commit()
        self.db.refresh(card)
        return card

    def react(self, card: HouseholdCard, recipient: HouseholdCardRecipient, reaction: str) -> HouseholdCard:
        recipient.reaction = reaction
        recipient.reacted_at = utcnow()
        if not recipient.is_read:
            recipient.is_read = True
            recipient.read_at = utcnow()
        self._record_reaction(card, recipient.user_id, reaction)
        self.db.commit()
        self.db.refresh(card)
        return card

    def _record_reaction(self, card: HouseholdCard, user_id: str, reaction: str) -> None:
        self.db.add(
            HouseholdCardReaction(
                card_id=card.id, user_id=user_id, reaction=reaction, created_at=utcnow()
            )
        )

    def add_comment(self, card: HouseholdCard, user: User, content: str) -> HouseholdCardComment:
        comment = HouseholdCardComment(
            card_id=card.id, user_id=user.id, content=content.strip(), created_at=utcnow()
        )
        self.db.add(comment)
        self.db.commit()
        self.db.refresh(comment)
        return comment

    def delete_card(self, card: HouseholdCard) -> None:
        self.db.delete(card)
        self.db.commit()

    # ------------------------------------------------------------------
    def to_out(self, household: Household, *, viewer: User | None = None) -> HouseholdOut:
        members = [
            MemberOut(
                membership_id=row.membership.id,
                user_id=row.user.id,
                display_name=row.user.display_name,
                username=row.user.username,
                role=row.membership.role,
                relation_label=row.membership.relation_label,
                status=row.membership.status,
                joined_at=row.membership.joined_at,
            )
            for row in self.list_members(household)
        ]
        is_owner = viewer is not None and viewer.id == household.owner_id
        return HouseholdOut(
            id=household.id,
            name=household.name,
            owner_id=household.owner_id,
            merchant_id=household.merchant_id,
            invite_code=household.invite_code if is_owner else None,
            invite_code_active=household.invite_code_active,
            created_at=household.created_at,
            members=members,
        )

    def card_to_out(self, card: HouseholdCard, *, viewer: User) -> CardOut:
        recipients = []
        mine: HouseholdCardRecipient | None = None
        for item in card.recipients:
            user = self.db.get(User, item.user_id)
            recipients.append(
                CardRecipientOut(
                    user_id=item.user_id,
                    display_name=user.display_name if user else "未知成员",
                    is_read=item.is_read,
                    read_at=item.read_at,
                    reaction=item.reaction,
                    reacted_at=item.reacted_at,
                )
            )
            if item.user_id == viewer.id:
                mine = item

        comments = []
        for item in card.comments:
            user = self.db.get(User, item.user_id)
            comments.append(
                CardCommentOut(
                    id=item.id,
                    user_id=item.user_id,
                    display_name=user.display_name if user else "未知成员",
                    content=item.content,
                    created_at=item.created_at,
                )
            )

        return CardOut(
            id=card.id,
            household_id=card.household_id,
            card_type=card.card_type,
            title=card.title,
            summary=card.summary,
            payload=card.payload or {},
            shared_fields=list(card.shared_fields or []),
            system_max_withdrawable_cents=card.system_max_withdrawable_cents,
            planned_household_amount_cents=card.planned_household_amount_cents,
            cash_event_id=card.cash_event_id,
            created_at=card.created_at,
            updated_at=card.updated_at,
            recipients=recipients,
            comments=comments,
            my_reaction=mine.reaction if mine else None,
            is_read=mine.is_read if mine else False,
        )


class HouseholdMemberRow:
    __slots__ = ("membership", "user")

    def __init__(self, membership: HouseholdMembership, user: User) -> None:
        self.membership = membership
        self.user = user
