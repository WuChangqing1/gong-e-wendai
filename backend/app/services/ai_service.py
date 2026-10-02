"""AI Provider Layer。

设计目标：业务代码不绑定任何一家模型厂商。

* 统一通过 :class:`AIService` 暴露能力（``extract_cash_event`` /
  ``explain_analysis`` / ``draft_consultation``）
* 使用 OpenAI-compatible HTTP API，厂商差异只体现在环境变量
  （``AI_BASE_URL`` / ``AI_MODEL`` / ``AI_API_KEY``）
* 超时、无 Key、HTTP 失败、返回非法 JSON、网络失败都会抛出
  :class:`~app.core.errors.AIServiceError`，由上层转换为"智能服务暂时不可用"，
  **绝不影响**登录、事件管理、CSV、现金流计算、家庭协同与经营咨询

硬性约束：
* AI 不得重新计算金额、不得修改引擎结果、不得猜测未知金额或时间、
  不得进行信用评分或违约预测、不得直接写入未经确认的现金事件
* AI 返回的新增金额（不在输入里的）会被后端过滤，并在响应中标注
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, Field, ValidationError, field_validator

from app.core.config import settings
from app.core.errors import AIServiceError
from app.core.logging import get_logger
from app.utils.money import MoneyError, to_cents
from app.utils.timeutil import parse_datetime, utcnow

logger = get_logger(__name__)

#: 系统提示词：明确禁止重新计算、新增事实、猜测与承诺
EXTRACT_SYSTEM_PROMPT = """你是「工 e 稳袋」的结构化信息提取助手，服务于中国小微经营者。
你的唯一任务是把用户粘贴的一段中文原文，整理成一条收付款事项的结构化 JSON。

必须遵守：
1. 只提取原文中明确出现的信息，禁止推测、补全或编造任何金额与日期。
2. amount_cents 必须是整数分（元 × 100，四舍五入到分）。
3. scheduled_at 必须是 ISO-8601 时间字符串；原文只有日期时用当地时间的 09:00。
4. direction 只能是 "inflow"（收入）或 "outflow"（支出）。
5. state 固定为 "scheduled"。
6. 如果原文没有明确金额或时间，对应字段返回 null，并在 warnings 中说明。
7. 禁止进行信用评分、违约预测、贷款建议或收入预测。
8. 只输出 JSON，不要输出解释文字、不要使用 Markdown 代码块。

输出 JSON 结构：
{"title": "", "direction": "inflow", "amount_cents": 0, "scheduled_at": "",
 "state": "scheduled", "source_label": "", "confidence": {}, "warnings": []}
"""

EXPLAIN_SYSTEM_PROMPT = """你是「工 e 稳袋」的说明助手，服务于中国小微经营者。
你只会收到系统已经计算好的结论字段，你的任务是用通俗中文把它们讲清楚。

必须遵守：
1. 严禁重新计算任何金额、严禁给出与输入不一致的新金额。
2. 严禁新增输入中没有的事实、事件、日期或人物。
3. 严禁猜测未来的到账情况，严禁承诺"一定会到账"，严禁做贷款或授信建议。
4. 只用输入字段中出现的金额，如需引用请原样引用。
5. 用 3-5 句普通话说明：今天可以提用多少、是什么限制了它、需要注意什么。
6. 不要使用"CVaR""线性规划""约束求解"等专业术语。
"""

DRAFT_SYSTEM_PROMPT = """你是「工 e 稳袋」的咨询事项整理助手。
用户会给你一组已经脱敏的字段（白名单字段）和一句口语化的疑问，
请把它整理成一段规范的咨询描述。

必须遵守：
1. 只能使用给定字段中的信息，禁止编造金额、时间、单号或机构名称。
2. 不要给出结论、不要承诺处理时效、不要做信用或贷款判断。
3. 输出 2-4 句中文，包含：涉及的事项、具体疑问、希望核实的内容。
4. 只输出正文，不要输出 JSON、标题或 Markdown。
"""


class ExtractedEvent(BaseModel):
    """AI 提取结果（严格校验）。"""

    title: str = Field(default="")
    direction: str = Field(default="inflow")
    amount_cents: int | None = None
    scheduled_at: str | None = None
    state: str = "scheduled"
    source_label: str | None = None
    confidence: dict[str, float] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)

    @field_validator("direction")
    @classmethod
    def _check_direction(cls, value: str) -> str:
        cleaned = (value or "").strip().lower()
        if cleaned not in ("inflow", "outflow"):
            raise ValueError("direction 只能是 inflow 或 outflow")
        return cleaned

    @field_validator("state")
    @classmethod
    def _check_state(cls, value: str) -> str:
        cleaned = (value or "scheduled").strip().lower()
        if cleaned not in ("scheduled", "included_in_opening", "cancelled"):
            return "scheduled"
        return cleaned

    @field_validator("amount_cents", mode="before")
    @classmethod
    def _coerce_amount(cls, value: Any) -> Any:
        if value in (None, "", "null"):
            return None
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            # 允许 AI 返回"元"，用 Decimal 精确换算
            try:
                return int((Decimal(str(value)) * 100).quantize(Decimal("1")))
            except (InvalidOperation, ValueError):
                return None
        text = str(value).strip()
        if not text:
            return None
        try:
            # 优先按"分"解释纯整数
            if re.fullmatch(r"-?\d+", text):
                return int(text)
            return to_cents(text)
        except (MoneyError, ValueError):
            return None

    @field_validator("scheduled_at", mode="before")
    @classmethod
    def _coerce_time(cls, value: Any) -> Any:
        if value in (None, "", "null"):
            return None
        try:
            return parse_datetime(value).isoformat()
        except (ValueError, TypeError):
            return None


@dataclass
class ExtractedEventResult:
    event: ExtractedEvent
    raw_text: str
    filtered_amounts: list[str] = field(default_factory=list)
    used_fields: list[str] = field(default_factory=list)


class ChatClient(Protocol):
    """可替换的对话补全客户端（便于测试注入测试替身）。"""

    def complete(self, *, system: str, user: str) -> str: ...


class OpenAICompatibleClient:
    """基于 OpenAI-compatible HTTP API 的客户端。"""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    @property
    def endpoint(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return f"{self.base_url}/chat/completions"

    def complete(self, *, system: str, user: str) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0,
            "stream": False,
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(self.endpoint, json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            raise AIServiceError("智能服务响应超时") from exc
        except httpx.HTTPError as exc:
            raise AIServiceError("智能服务网络异常") from exc

        if response.status_code >= 400:
            # 不记录响应正文，避免泄露密钥或内部信息
            logger.warning("AI provider returned HTTP %s", response.status_code)
            raise AIServiceError("智能服务返回异常状态")

        try:
            data = response.json()
        except ValueError as exc:
            raise AIServiceError("智能服务返回内容无法解析") from exc

        try:
            return str(data["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            raise AIServiceError("智能服务返回结构不符合预期") from exc


class AIService:
    """业务代码唯一入口。"""

    def __init__(self, client: ChatClient | None = None) -> None:
        self._client = client

    # ------------------------------------------------------------------
    @property
    def configured(self) -> bool:
        return settings.ai_configured

    def client(self) -> ChatClient:
        if self._client is not None:
            return self._client
        if not settings.ai_configured:
            raise AIServiceError("智能服务尚未配置")
        return OpenAICompatibleClient(
            base_url=settings.ai_base_url,
            api_key=settings.ai_api_key,
            model=settings.ai_model,
            timeout=settings.ai_timeout_seconds,
        )

    def status(self) -> dict[str, Any]:
        return {
            "enabled": bool(settings.ai_enabled),
            "configured": settings.ai_configured,
            "available": settings.ai_configured,
            "model": settings.ai_model if settings.ai_configured else None,
            "provider": "openai-compatible",
        }

    # ------------------------------------------------------------------
    def extract_cash_event(self, text: str) -> ExtractedEventResult:
        """把自然语言整理成一条待确认的收付款事项。

        **不会直接入库**：调用方必须让用户确认后再写入。
        """
        cleaned = (text or "").strip()
        if len(cleaned) < 4:
            raise AIServiceError("请提供更完整的原文")

        raw = self.client().complete(
            system=EXTRACT_SYSTEM_PROMPT,
            user=f"原文：\n{cleaned}\n\n现在输出 JSON。",
        )
        payload = _load_json_object(raw)

        try:
            event = ExtractedEvent.model_validate(payload)
        except ValidationError as exc:
            logger.warning("AI extract validation failed: %s", exc.error_count())
            raise AIServiceError("智能服务返回的内容不符合要求，请手动录入") from exc

        # 金额必须能在原文中找到，否则视为"猜测"并剔除
        filtered: list[str] = []
        if event.amount_cents is not None:
            if not _amount_appears_in_text(event.amount_cents, cleaned):
                filtered.append(_format_cents(event.amount_cents))
                event = event.model_copy(
                    update={
                        "amount_cents": None,
                        "warnings": [
                            *event.warnings,
                            "原文中没有找到与提取金额一致的数字，已清空该金额，请手动确认。",
                        ],
                    }
                )

        if event.scheduled_at is None:
            event = event.model_copy(
                update={
                    "warnings": [
                        *event.warnings,
                        "原文中没有明确的时间，请手动填写预计时间。",
                    ]
                }
            )

        return ExtractedEventResult(
            event=event,
            raw_text=cleaned,
            filtered_amounts=filtered,
            used_fields=["text"],
        )

    # ------------------------------------------------------------------
    def explain_analysis(self, payload: dict[str, Any]) -> dict[str, Any]:
        """把结构化分析结果翻译成通俗中文。

        金额与结论由确定性引擎给出；AI 只做表达。
        """
        allowed_fields = (
            "max_withdrawable_cents",
            "opening_balance_cents",
            "buffer_cents",
            "status",
            "limiting_timestamp",
            "limiting_balance_cents",
            "limiting_event_title",
            "limiting_reason",
            "payment_gap_cents",
            "buffer_gap_cents",
            "window_inflow_cents",
            "window_outflow_cents",
            "pending_inflows",
        )
        sanitized = {key: payload.get(key) for key in allowed_fields if key in payload}
        if not sanitized:
            raise AIServiceError("缺少可供说明的分析结果")

        known_amounts = _collect_amounts(sanitized)
        user_prompt = (
            "系统计算结果（金额单位：分）：\n"
            + json.dumps(sanitized, ensure_ascii=False, indent=2)
            + "\n\n请用 3-5 句普通话说明给经营者听。"
        )

        raw = self.client().complete(system=EXPLAIN_SYSTEM_PROMPT, user=user_prompt)
        explanation = (raw or "").strip()
        if not explanation:
            raise AIServiceError("智能服务没有返回可用说明")

        # 一致性校验：解释中出现的金额必须来自输入
        unknown = _find_unknown_amounts(explanation, known_amounts)
        if unknown:
            logger.warning("AI explanation contained unknown amounts: %s", unknown)
            explanation = _strip_unknown_amounts(explanation, unknown)
            explanation += "\n\n（说明中与计算结果不一致的金额已被系统移除，请以页面上的计算结果为准。）"

        return {
            "explanation": explanation,
            "used_fields": sorted(sanitized.keys()),
            "filtered_amounts": unknown,
        }

    # ------------------------------------------------------------------
    def draft_consultation(self, *, question_type: str, question: str, fields: dict[str, Any]) -> dict[str, Any]:
        """把口语化疑问整理成规范的咨询描述。只使用白名单字段。"""
        from app.models.consultation import CONSULTATION_ALLOWED_FIELDS

        allowed = {key: fields.get(key) for key in CONSULTATION_ALLOWED_FIELDS if key in fields}
        user_prompt = (
            f"问题类型：{question_type}\n"
            f"经营者原话：{question}\n"
            f"可引用字段：\n{json.dumps(allowed, ensure_ascii=False, indent=2)}\n\n"
            "请整理成一段规范的咨询描述。"
        )
        raw = self.client().complete(system=DRAFT_SYSTEM_PROMPT, user=user_prompt)
        draft = (raw or "").strip()
        if not draft:
            raise AIServiceError("智能服务没有返回可用内容")

        known_amounts = _collect_amounts(allowed)
        unknown = _find_unknown_amounts(draft, known_amounts)
        if unknown:
            draft = _strip_unknown_amounts(draft, unknown)

        return {
            "draft": draft,
            "allowed_fields": sorted(allowed.keys()),
            "filtered_amounts": unknown,
        }

    # ------------------------------------------------------------------
    def health(self) -> dict[str, Any]:
        status = self.status()
        status["checked_at"] = utcnow().isoformat()
        return status


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------
_JSON_BLOCK = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)
_AMOUNT_IN_TEXT = re.compile(r"(\d[\d,]*(?:\.\d+)?)")


def _load_json_object(raw: str) -> dict[str, Any]:
    """从模型输出中稳健地取出一个 JSON 对象。"""
    text = (raw or "").strip()
    if not text:
        raise AIServiceError("智能服务没有返回内容")

    candidates: list[str] = []
    block = _JSON_BLOCK.search(text)
    if block:
        candidates.append(block.group(1))
    candidates.append(text)
    obj = _JSON_OBJECT.search(text)
    if obj:
        candidates.append(obj.group(0))

    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, dict):
            return data

    raise AIServiceError("智能服务返回的内容不是合法的 JSON")


def _format_cents(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    value = abs(int(cents))
    return f"{sign}{value // 100}.{value % 100:02d}"


def _amount_appears_in_text(cents: int, text: str) -> bool:
    if cents == 0:
        return True
    candidates = {_format_cents(cents)}
    yuan = abs(cents) / 100
    if yuan.is_integer():
        candidates.add(str(int(yuan)))
        candidates.add(f"{int(yuan):,}")
    candidates.add(f"{yuan:,.2f}")
    candidates.add(f"{abs(cents)}")

    for token in _AMOUNT_IN_TEXT.findall(text):
        cleaned = token.replace(",", "")
        try:
            value = Decimal(cleaned)
        except InvalidOperation:
            continue
        if int((value * 100).quantize(Decimal("1"))) == abs(cents) or abs(cents) == int(value):
            return True
        for candidate in candidates:
            if candidate and candidate.replace(",", "") == cleaned:
                return True
    return False


def _collect_amounts(payload: Any) -> set[int]:
    """递归收集输入中出现过的所有金额（分）。"""
    found: set[int] = set()

    def walk(node: Any, key: str | None = None) -> None:
        if isinstance(node, dict):
            for sub_key, value in node.items():
                walk(value, str(sub_key))
        elif isinstance(node, list):
            for item in node:
                walk(item, key)
        elif isinstance(node, int) and not isinstance(node, bool):
            if key and ("cents" in key or "amount" in key):
                found.add(int(node))
            found.add(int(node))
        elif isinstance(node, str):
            for token in _AMOUNT_IN_TEXT.findall(node):
                cleaned = token.replace(",", "")
                try:
                    value = Decimal(cleaned)
                except InvalidOperation:
                    continue
                if "." in cleaned:
                    found.add(int((value * 100).quantize(Decimal("1"))))
                    found.add(int(value))
                else:
                    found.add(int(value))
                    found.add(int(value) * 100)
        elif isinstance(node, float):
            try:
                found.add(int((Decimal(str(node)) * 100).quantize(Decimal("1"))))
            except InvalidOperation:
                pass

    walk(payload)
    return found


def _find_unknown_amounts(text: str, known: set[int]) -> list[str]:
    """返回解释文字中出现但不在输入里的金额。"""
    unknown: list[str] = []
    # 日期与时间片段中的数字不是金额（例如 2025-10-03、10:30）
    masked = _DATETIME_TOKEN.sub(lambda m: " " * len(m.group(0)), text)
    for token in _AMOUNT_IN_TEXT.findall(masked):
        cleaned = token.replace(",", "")
        try:
            value = Decimal(cleaned)
        except InvalidOperation:
            continue
        if "." in cleaned:
            cents = int((value * 100).quantize(Decimal("1")))
            candidates = {cents, int(value)}
        else:
            as_yuan = int(value) * 100
            candidates = {int(value), as_yuan}
        if candidates & known:
            continue
        # 过滤明显不是金额的编号（如尾号 8821、事项编号 20251001）
        if _looks_like_identifier(text, token):
            continue
        unknown.append(token)
    return sorted(set(unknown))


_DATETIME_TOKEN = re.compile(r"\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?(?:\s*\d{1,2}:\d{2}(?::\d{2})?)?")

_IDENTIFIER_CONTEXT = re.compile(
    r"(尾号|编号|单号|批次|版号|第\s*\d+\s*天|\d+\s*月\s*\d+\s*日|[vV]\d|\d{1,2}:\d{2})"
)


def _looks_like_identifier(text: str, token: str) -> bool:
    pattern = re.compile(rf"(?:{_IDENTIFIER_CONTEXT.pattern})[^\d]{{0,3}}{re.escape(token)}")
    return bool(pattern.search(text))


def _strip_unknown_amounts(text: str, unknown: list[str]) -> str:
    result = text
    for token in unknown:
        result = result.replace(token, "（金额请以页面计算结果为准）")
    return result


def ai_available() -> bool:
    return settings.ai_configured
