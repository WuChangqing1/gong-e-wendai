"""经营咨询渠道抽象层。

当前阶段使用系统内部流程（``InternalConsultationProvider``）。未来接入外部机构时，
只需新增一个 Provider 实现并在配置中切换，不需要重写咨询系统。

约束：Provider 只负责"把事项送出去 / 把结果收回来"，不参与任何金额计算，
也不允许访问家庭数据、经营余额、可提用金额与留底金额。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.utils.timeutil import utcnow


@dataclass
class ProviderSubmission:
    """提交给渠道的字段集合（已按白名单裁剪）。"""

    case_no: str
    question_type: str
    question: str
    fields: dict[str, Any]
    submitted_at: datetime = field(default_factory=utcnow)


@dataclass
class ProviderResult:
    external_reference: str | None
    status: str
    message: str
    fields: dict[str, Any] = field(default_factory=dict)


class ConsultationProvider(ABC):
    """经营咨询渠道接口。"""

    key: str = "internal"
    label: str = "系统内部受理"

    @abstractmethod
    def submit(self, submission: ProviderSubmission) -> ProviderResult:
        """提交咨询事项，返回渠道受理信息。"""

    @abstractmethod
    def fetch_result(self, external_reference: str) -> ProviderResult | None:
        """按渠道编号拉取处理结果（内部流程返回 ``None``）。"""

    def available(self) -> bool:
        return True


class InternalConsultationProvider(ConsultationProvider):
    """系统内部业务流程：由具备 consultant 角色的账户在咨询工作台受理。"""

    key = "internal"
    label = "系统内部受理"

    def submit(self, submission: ProviderSubmission) -> ProviderResult:
        return ProviderResult(
            external_reference=None,
            status="submitted",
            message="已进入经营咨询中心，等待咨询人员受理",
        )

    def fetch_result(self, external_reference: str) -> ProviderResult | None:
        _ = external_reference
        return None


_PROVIDERS: dict[str, ConsultationProvider] = {
    InternalConsultationProvider.key: InternalConsultationProvider(),
}


def get_consultation_provider(key: str = "internal") -> ConsultationProvider:
    provider = _PROVIDERS.get(key)
    if provider is None:
        raise KeyError(f"未注册的咨询渠道：{key}")
    return provider


def register_consultation_provider(provider: ConsultationProvider) -> None:
    """注册新的咨询渠道（供后续外部机构接入使用）。"""
    _PROVIDERS[provider.key] = provider


# ---------------------------------------------------------------------------
# 结算数据渠道接口（预留）
# ---------------------------------------------------------------------------
@dataclass
class SettlementRecord:
    reference: str
    amount_cents: int
    settled_at: datetime
    state: str
    title: str
    raw: dict[str, Any] = field(default_factory=dict)


class SettlementProvider(ABC):
    """结算数据渠道接口：用于未来直接核对到账情况。"""

    key: str = "internal"
    label: str = "系统内部记录"

    @abstractmethod
    def query_settlements(
        self, *, merchant_reference: str, start: datetime, end: datetime
    ) -> list[SettlementRecord]:
        """查询区间内的结算记录。"""

    def available(self) -> bool:
        return False


class InternalSettlementProvider(SettlementProvider):
    """当前阶段不接入外部结算系统：结算信息由商户录入或咨询结果更新。"""

    key = "internal"
    label = "商户录入"

    def query_settlements(
        self, *, merchant_reference: str, start: datetime, end: datetime
    ) -> list[SettlementRecord]:
        _ = (merchant_reference, start, end)
        return []

    def available(self) -> bool:
        return False


_SETTLEMENT_PROVIDERS: dict[str, SettlementProvider] = {
    InternalSettlementProvider.key: InternalSettlementProvider(),
}


def get_settlement_provider(key: str = "internal") -> SettlementProvider:
    provider = _SETTLEMENT_PROVIDERS.get(key)
    if provider is None:
        raise KeyError(f"未注册的结算渠道：{key}")
    return provider


def register_settlement_provider(provider: SettlementProvider) -> None:
    _SETTLEMENT_PROVIDERS[provider.key] = provider


def provider_catalog() -> dict[str, Any]:
    return {
        "consultation": [
            {"key": item.key, "label": item.label, "available": item.available()}
            for item in _PROVIDERS.values()
        ],
        "settlement": [
            {"key": item.key, "label": item.label, "available": item.available()}
            for item in _SETTLEMENT_PROVIDERS.values()
        ],
    }
