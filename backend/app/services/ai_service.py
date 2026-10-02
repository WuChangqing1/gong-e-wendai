"""AI Provider Layer（正式接入智谱 GLM）。

设计目标：业务代码不绑定任何一家模型厂商，但默认供应商是智谱 GLM。

* 统一通过 :class:`AIService` 暴露能力：

  - :meth:`AIService.extract_cash_event_from_text`
  - :meth:`AIService.extract_cash_event_from_image`
  - :meth:`AIService.explain_analysis`
  - :meth:`AIService.draft_consultation`

* 文本任务使用 ``GLM_TEXT_MODEL``（默认 ``glm-4.5-air``）
* 图片任务使用 ``GLM_VISION_MODEL``（默认 ``glm-4.6v``）
* 底层是 OpenAI-compatible HTTP API；供应商差异只体现在环境变量
* 超时、无 Key、HTTP 失败、返回非法 JSON、网络失败都会抛出
  :class:`~app.core.errors.AIServiceError`，由上层转换为"智能服务暂时不可用"，
  **绝不影响**登录、事件管理、CSV、现金流计算、家庭协同与经营咨询

硬性约束
--------
* AI 不得重新计算金额、不得修改引擎结果、不得猜测未知金额或时间、
  不得进行信用评分或违约预测、不得直接写入未经确认的现金事件
* AI 返回的新增金额（不在输入里的）会被后端过滤，并在响应中标注
* 密钥只以 ``SecretStr`` 保存，绝不进入日志、Traceback、API 响应或 Git
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import time
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

#: 允许的图片类型与大小
ALLOWED_IMAGE_TYPES = ("image/png", "image/jpeg", "image/webp")

#: 智谱不支持 temperature=0；低随机业务任务使用最小合理非零值。
TEXT_TEMPERATURE = 0.1
VISION_TEMPERATURE = 0.1
EXPLAIN_TEMPERATURE = 0.2
DRAFT_TEMPERATURE = 0.2

#: 只对限流与临时故障重试
RETRYABLE_STATUS = (429, 500, 502, 503, 504)
#: 参数或鉴权问题不重试：重试不会变好
FATAL_STATUS = (400, 401, 403, 404, 422)

#: 系统提示词：明确禁止重新计算、新增事实、猜测与承诺
EXTRACT_SYSTEM_PROMPT = """你是「工 e 稳袋」的结构化信息提取助手，服务于中国小微经营者。
你的唯一任务是把用户提供的原文（文字或图片中的文字）整理成一条收付款事项的结构化 JSON。

必须遵守：
1. 只提取原文中明确出现的信息，禁止推测、补全或编造任何金额与日期。
2. amount_cents 必须是整数分（元 × 100，四舍五入到分）。
3. scheduled_at 必须是 ISO-8601 时间字符串；原文只有日期时用当地时间的 09:00。
4. direction 只能是 "inflow"（收入）或 "outflow"（支出）。
5. state 固定为 "scheduled"。
6. event_type 只能是：settlement / sale_receipt / supplier_payment / rent / refund /
   payroll / utility / tax / loan_repayment / platform_fee / transfer_in /
   transfer_out / other_inflow / other_outflow。
7. channel 是结算或收款渠道（如"微信支付""支付宝""银行卡"），原文没写就返回 null。
8. 如果原文没有明确金额或时间，对应字段返回 null，并在 warnings 中说明。
9. 禁止进行信用评分、违约预测、贷款建议或收入预测。
10. 只输出 JSON，不要输出解释文字、不要使用 Markdown 代码块。

输出 JSON 结构：
{"title": "", "direction": "inflow", "amount_cents": 0, "scheduled_at": "",
 "state": "scheduled", "event_type": "other_inflow", "source_label": "",
 "channel": null, "confidence": {}, "warnings": []}
"""

IMAGE_EXTRACT_SYSTEM_PROMPT = """你是「工 e 稳袋」的截图信息提取助手，服务于中国小微经营者。
用户会上传一张结算通知、付款通知或收付款凭证的截图。
请只读取截图中清晰可见的文字信息，整理成一条收付款事项的结构化 JSON。

必须遵守：
1. 只提取截图中能看清的信息，看不清或没有的信息一律返回 null，禁止猜测。
2. amount_cents 必须是整数分（元 × 100）。截图里的金额通常是元，请换算成分。
3. scheduled_at 必须是 ISO-8601 时间字符串；只有日期时用当地时间的 09:00。
4. direction 只能是 "inflow" 或 "outflow"；看不出收支方向时返回 null。
5. event_type 只能是：settlement / sale_receipt / supplier_payment / rent / refund /
   payroll / utility / tax / loan_repayment / platform_fee / transfer_in /
   transfer_out / other_inflow / other_outflow。
6. channel 填截图中的收款渠道，例如"微信支付""支付宝""银行卡"；没有则 null。
7. 在 warnings 中逐条说明哪些字段看不清或需要用户核对。
8. 禁止进行信用评分、违约预测、贷款建议或收入预测。
9. 只输出 JSON，不要输出解释文字、不要使用 Markdown 代码块。

输出 JSON 结构：
{"title": "", "direction": "inflow", "amount_cents": 0, "scheduled_at": "",
 "state": "scheduled", "event_type": "other_inflow", "source_label": "",
 "channel": null, "confidence": {}, "warnings": []}
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
    direction: str | None = Field(default="inflow")
    amount_cents: int | None = None
    scheduled_at: str | None = None
    state: str = "scheduled"
    event_type: str = "other_inflow"
    source_label: str | None = None
    channel: str | None = None
    confidence: dict[str, float] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)

    @field_validator("direction")
    @classmethod
    def _check_direction(cls, value: Any) -> Any:
        if value in (None, "", "null"):
            return None
        cleaned = str(value).strip().lower()
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

    @field_validator("event_type")
    @classmethod
    def _check_event_type(cls, value: str) -> str:
        from app.models.cash import EVENT_TYPES

        cleaned = (value or "").strip().lower()
        return cleaned if cleaned in EVENT_TYPES else "other_inflow"

    @field_validator("channel")
    @classmethod
    def _check_channel(cls, value: Any) -> Any:
        if value in (None, "", "null", "无", "未知"):
            return None
        return str(value).strip()[:64]

    @field_validator("confidence", mode="before")
    @classmethod
    def _coerce_confidence(cls, value: Any) -> Any:
        """容忍模型把 confidence 写成数字或字符串：不能因此丢掉整条结果。"""
        if not isinstance(value, dict):
            return {}
        cleaned: dict[str, float] = {}
        for key, item in value.items():
            try:
                cleaned[str(key)] = float(item)
            except (TypeError, ValueError):
                continue
        return cleaned

    @field_validator("warnings", mode="before")
    @classmethod
    def _coerce_warnings(cls, value: Any) -> Any:
        """容忍模型写成单个字符串或 null。"""
        if value in (None, "", "null"):
            return []
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            return [str(item) for item in value if str(item).strip()]
        return [str(value)]

    @field_validator("title", "source_label", mode="before")
    @classmethod
    def _coerce_text(cls, value: Any) -> Any:
        if value is None:
            return ""
        if isinstance(value, (int, float)):
            return str(value)
        return value

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

    def complete(
        self,
        *,
        system: str,
        user: str,
        temperature: float = TEXT_TEMPERATURE,
        image: ImageInput | None = None,
    ) -> str: ...


@dataclass(frozen=True, slots=True)
class ImageInput:
    """待识别的图片。只保存在内存与私有上传目录，绝不进入公开静态资源。"""

    data: bytes
    media_type: str


class GLMOpenAICompatibleClient:
    """智谱 GLM 的 OpenAI-compatible 客户端，同时支持文本与多模态。"""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        text_model: str = "",
        vision_model: str = "",
        timeout: float = 30.0,
        vision_timeout: float = 45.0,
        max_tokens: int = 1024,
        max_retries: int = 2,
        model: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        # ``model`` 是旧参数名，等价于同时指定文本与视觉模型
        self.text_model = text_model or model or ""
        self.vision_model = vision_model or model or ""
        self.timeout = timeout
        self.vision_timeout = vision_timeout
        self.max_tokens = max_tokens
        self.max_retries = max(0, int(max_retries))

    @property
    def model(self) -> str:
        """向后兼容：默认文本模型。"""
        return self.text_model

    @property
    def endpoint(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return f"{self.base_url}/chat/completions"

    def complete(
        self,
        *,
        system: str,
        user: str,
        temperature: float = TEXT_TEMPERATURE,
        image: ImageInput | None = None,
    ) -> str:
        use_vision = image is not None
        model = self.vision_model if use_vision else self.text_model
        if not model:
            raise AIServiceError("智能服务尚未配置")

        content: Any = user
        if image is not None:
            content = [
                {"type": "text", "text": user},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": _data_url(image),
                    },
                },
            ]

        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": content},
            ],
            # 智谱不支持 temperature=0，低随机任务用最小合理非零值
            "temperature": float(temperature),
            "stream": False,
            # 这些任务不需要复杂推理，关闭思考以免浪费额度
            "thinking": {"type": "disabled"},
        }
        if self.max_tokens > 0:
            payload["max_tokens"] = self.max_tokens

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }
        timeout = self.vision_timeout if use_vision else self.timeout

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                with httpx.Client(timeout=timeout) as client:
                    response = client.post(self.endpoint, json=payload, headers=headers)
            except httpx.TimeoutException as exc:
                last_error = exc
                if attempt < self.max_retries:
                    time.sleep(min(2.0, 0.5 * (2**attempt)))
                    continue
                raise AIServiceError("智能服务响应超时") from exc
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt < self.max_retries:
                    time.sleep(min(2.0, 0.5 * (2**attempt)))
                    continue
                raise AIServiceError("智能服务网络异常") from exc

            if response.status_code in RETRYABLE_STATUS and attempt < self.max_retries:
                # 只记录状态码，绝不记录响应正文（可能含敏感内容）
                logger.warning("AI provider returned HTTP %s, retrying", response.status_code)
                time.sleep(min(4.0, 0.5 * (2**attempt)))
                continue

            if response.status_code >= 400:
                # 400/401/403 属于参数或鉴权问题，重试不会变好
                logger.warning(
                    "AI provider returned HTTP %s%s",
                    response.status_code,
                    " (fatal)" if response.status_code in FATAL_STATUS else "",
                )
                raise AIServiceError("智能服务返回异常状态")

            try:
                data = response.json()
            except ValueError as exc:
                raise AIServiceError("智能服务返回内容无法解析") from exc

            try:
                text = str(data["choices"][0]["message"]["content"])
            except (KeyError, IndexError, TypeError) as exc:
                raise AIServiceError("智能服务返回结构不符合预期") from exc

            _record_usage(data, model=model, use_vision=use_vision)
            return text

        raise AIServiceError("智能服务暂时不可用") from last_error


def _data_url(image: ImageInput) -> str:
    encoded = base64.b64encode(image.data).decode("ascii")
    return f"data:{image.media_type};base64,{encoded}"


def _record_usage(data: dict[str, Any], *, model: str, use_vision: bool) -> None:
    """记录调用审计。

    只记录：模型、是否视觉、状态、token 用量。
    **不记录**密钥、不记录完整 prompt、不记录完整响应。
    """
    usage = data.get("usage") or {}
    logger.info(
        "ai.call model=%s vision=%s prompt_tokens=%s completion_tokens=%s",
        model,
        use_vision,
        usage.get("prompt_tokens"),
        usage.get("completion_tokens"),
    )


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
        return GLMOpenAICompatibleClient(
            base_url=settings.resolved_ai_base_url,
            api_key=settings.resolved_ai_api_key,
            text_model=settings.text_model,
            vision_model=settings.vision_model,
            timeout=settings.glm_timeout_seconds,
            vision_timeout=settings.glm_vision_timeout_seconds,
            max_tokens=settings.glm_max_tokens,
            max_retries=settings.glm_max_retries,
        )

    def status(self) -> dict[str, Any]:
        """对外状态。**绝不**包含密钥、密钥前后缀或长度。"""
        return settings.ai_status_public()

    # ------------------------------------------------------------------
    def _extract_event(
        self,
        *,
        system: str,
        user: str,
        raw_source_text: str,
        image: ImageInput | None = None,
        temperature: float = TEXT_TEMPERATURE,
        verify_amount_against_source: bool = True,
    ) -> ExtractedEventResult:
        try:
            raw = self.client().complete(
                system=system, user=user, temperature=temperature, image=image
            )
        except AIServiceError:
            raise
        except Exception as exc:  # noqa: BLE001 - 供应商异常一律降级为可用提示
            # 任何未预期的供应商异常都不得冒泡成 500：统一变成「智能服务暂时不可用」
            logger.warning("AI provider raised %s", type(exc).__name__)
            raise AIServiceError("智能服务暂时不可用") from exc

        payload = _load_json_object(raw)

        try:
            event = ExtractedEvent.model_validate(payload)
        except ValidationError as exc:
            logger.warning("AI extract validation failed: %s", exc.error_count())
            raise AIServiceError("智能服务返回的内容不符合要求，请手动录入") from exc

        warnings = list(event.warnings)
        filtered: list[str] = []

        if event.amount_cents is not None and event.amount_cents <= 0:
            # 0 元或负数的收付款事项没有业务含义：模型看不清金额时会返回 0，
            # 直接采用会在页面上显示成一条「0 元」事项，必须清空并要求补填。
            filtered.append(_format_cents(event.amount_cents))
            event = event.model_copy(update={"amount_cents": None})
            warnings.append("识别结果里的金额不是有效的正数，已清空，请手动填写。")

        if event.amount_cents is not None:
            if verify_amount_against_source and not _amount_appears_in_text(
                event.amount_cents, raw_source_text
            ):
                # 文字来源必须能逐字核对；对不上说明是猜测，直接清空
                filtered.append(_format_cents(event.amount_cents))
                event = event.model_copy(update={"amount_cents": None})
                warnings.append(
                    "原文中没有找到与提取金额一致的数字，已清空该金额，请手动确认。"
                )
            elif image is not None:
                # 图片来源无法做逐字核对：保留候选值，但必须让用户明确核对
                warnings.append(
                    f"截图识别的金额为 {_format_cents(event.amount_cents)}，请与截图核对后再保存。"
                )

        if event.direction is None:
            warnings.append("无法判断这笔款项是收入还是支出，请手动选择。")

        if not event.title.strip():
            warnings.append("没有识别到事项名称，请手动填写。")

        if event.scheduled_at is None:
            warnings.append("原文中没有明确的时间，请手动填写预计时间。")

        event = event.model_copy(update={"warnings": warnings})
        return ExtractedEventResult(
            event=event,
            raw_text=raw_source_text,
            filtered_amounts=filtered,
            used_fields=["image"] if image is not None else ["text"],
        )

    def extract_cash_event_from_text(self, text: str) -> ExtractedEventResult:
        """粘贴文字 → 待确认的收付款事项。**不会直接入库。**"""
        cleaned = (text or "").strip()
        if len(cleaned) < 4:
            raise AIServiceError("请提供更完整的原文")
        if len(cleaned) > settings.ai_text_max_chars:
            raise AIServiceError(
                f"文字太长了，请精简到 {settings.ai_text_max_chars} 字以内"
            )
        return self._extract_event(
            system=EXTRACT_SYSTEM_PROMPT,
            user=f"原文：\n{cleaned}\n\n现在输出 JSON。",
            raw_source_text=cleaned,
            temperature=TEXT_TEMPERATURE,
        )

    def extract_cash_event_from_image(
        self, data: bytes, *, media_type: str
    ) -> ExtractedEventResult:
        """上传截图 → 待确认的收付款事项。**不会直接入库。**"""
        if not data:
            raise AIServiceError("请选择要识别的图片")
        if media_type not in ALLOWED_IMAGE_TYPES:
            raise AIServiceError("只支持 PNG、JPEG、WEBP 格式的截图")
        if len(data) > settings.ai_vision_max_bytes:
            limit_mb = settings.ai_vision_max_bytes // (1024 * 1024)
            raise AIServiceError(f"图片不能超过 {limit_mb}MB")
        # 注入测试替身时不要求全局配置；真实调用才需要视觉模型
        if self._client is None and not settings.vision_configured:
            raise AIServiceError("智能服务尚未配置")

        return self._extract_event(
            system=IMAGE_EXTRACT_SYSTEM_PROMPT,
            user=(
                "请从这张收付款截图里提取结构化信息。"
                "只读取清晰可见的内容，看不清的字段留空并写进 warnings。现在输出 JSON。"
            ),
            raw_source_text="",
            image=ImageInput(data=data, media_type=media_type),
            temperature=VISION_TEMPERATURE,
            # 图片没有可比对的文本，金额保留为候选值并要求用户核对
            verify_amount_against_source=False,
        )

    # 向后兼容别名
    def extract_cash_event(self, text: str) -> ExtractedEventResult:
        return self.extract_cash_event_from_text(text)

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
