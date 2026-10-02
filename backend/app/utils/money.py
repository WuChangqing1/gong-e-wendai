"""Deterministic money helpers.

Every monetary value inside the system is an ``int`` number of **cents**.
Floating point is never used for money.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

CURRENCY_CNY = "CNY"
CENT = Decimal("0.01")
MAX_AMOUNT_CENTS = 10**13  # 1000 亿元，防御性上限

_AMOUNT_RE = re.compile(r"^[+-]?(?:\d+)(?:\.\d+)?$")


class MoneyError(ValueError):
    """Raised when a value cannot be interpreted as a monetary amount."""


def _clean(raw: object) -> str:
    if raw is None:
        raise MoneyError("金额不能为空")
    if isinstance(raw, bool):
        raise MoneyError("金额格式不正确")
    if isinstance(raw, int):
        return str(raw)
    if isinstance(raw, Decimal):
        return format(raw, "f")
    if isinstance(raw, float):
        # Floats are accepted only as an already-decimal-ish convenience input
        # (e.g. JSON parsed elsewhere); convert through ``str`` to avoid binary
        # representation artefacts, then round half-up at cent precision.
        return repr(raw)
    text = str(raw).strip()
    if not text:
        raise MoneyError("金额不能为空")
    # Full-width digits / separators commonly seen in Chinese CSVs / Excel.
    text = text.replace("，", "").replace(",", "").replace("￥", "").replace("¥", "")
    text = text.replace(" ", "").replace("\u00a0", "")
    text = text.translate(str.maketrans("０１２３４５６７８９．－＋", "0123456789.-+"))
    return text


def to_cents(raw: object) -> int:
    """Normalise a user supplied amount to integer cents.

    ``"123.45"`` -> ``12345``.  Raises :class:`MoneyError` for anything that is
    not a plain decimal number (including ``"1e3"``, ``"abc"``, ``""``).
    """
    text = _clean(raw)
    if not _AMOUNT_RE.match(text):
        raise MoneyError("金额格式不正确")
    try:
        value = Decimal(text)
    except InvalidOperation as exc:  # pragma: no cover - guarded by regex
        raise MoneyError("金额格式不正确") from exc

    cents = int((value * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if abs(cents) > MAX_AMOUNT_CENTS:
        raise MoneyError("金额超出可处理范围")
    return cents


def cents_to_decimal_str(cents: int) -> str:
    """``12345`` -> ``"123.45"``."""
    sign = "-" if cents < 0 else ""
    value = abs(int(cents))
    return f"{sign}{value // 100}.{value % 100:02d}"


def cents_to_yuan(cents: int) -> Decimal:
    """``12345`` -> ``Decimal("123.45")`` (exact)."""
    return Decimal(cents).scaleb(-2)


def format_cny(cents: int) -> str:
    """``12345`` -> ``"¥123.45"``."""
    return f"¥{cents_to_decimal_str(cents)}"


def format_cny_grouped(cents: int) -> str:
    """``123456`` -> ``"¥1,234.56"``."""
    sign = "-" if cents < 0 else ""
    value = abs(int(cents))
    whole, frac = divmod(value, 100)
    return f"{sign}¥{whole:,}.{frac:02d}"


def sum_cents(values: object) -> int:
    """Sum an iterable of integer cents deterministically."""
    total = 0
    for item in values:  # type: ignore[union-attr]
        total += int(item)
    return total
