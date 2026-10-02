"""CSV 解析与编码识别工具。

支持 UTF-8、UTF-8 BOM 与 GB18030（覆盖常见中文 CSV），并自动识别分隔符。
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass

ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "gbk", "big5", "latin-1")

DELIMITERS = (",", "\t", ";", "|")

#: 文件扩展名白名单
ALLOWED_EXTENSIONS = (".csv", ".txt", ".tsv")

#: 列名别名，用于自动映射中文/英文表头
COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "cash_key": (
        "cash_key",
        "cashkey",
        "事项编号",
        "编号",
        "流水号",
        "交易号",
        "订单号",
        "id",
    ),
    "title": ("title", "事项名称", "名称", "摘要", "备注摘要", "交易名称", "描述", "memo"),
    "direction": ("direction", "收支方向", "方向", "借贷", "收付", "类型"),
    "amount": ("amount", "金额", "交易金额", "发生额", "金额(元)", "金额（元）", "amount_yuan"),
    "event_time": (
        "event_time",
        "交易时间",
        "发生时间",
        "时间",
        "time",
        "transaction_time",
    ),
    "scheduled_at": (
        "scheduled_at",
        "预计时间",
        "计划时间",
        "到期日",
        "付款日期",
        "应付款日",
        "plan_time",
    ),
    "state": ("state", "状态", "status"),
    "source_label": ("source_label", "来源说明", "来源", "渠道", "source"),
    "note": ("note", "备注", "说明", "remark", "comment"),
}

DIRECTION_ALIASES: dict[str, str] = {
    "inflow": "inflow",
    "in": "inflow",
    "收入": "inflow",
    "收": "inflow",
    "进账": "inflow",
    "入账": "inflow",
    "贷": "inflow",
    "outflow": "outflow",
    "out": "outflow",
    "支出": "outflow",
    "付": "outflow",
    "出账": "outflow",
    "借": "outflow",
}

STATE_ALIASES: dict[str, str] = {
    "scheduled": "scheduled",
    "计划中": "scheduled",
    "计划": "scheduled",
    "待处理": "scheduled",
    "included_in_opening": "included_in_opening",
    "已计入期初": "included_in_opening",
    "已入期初": "included_in_opening",
    "cancelled": "cancelled",
    "已取消": "cancelled",
    "取消": "cancelled",
}

UNKNOWN_VALUES = {"", "-", "--", "—", "n/a", "na", "null", "none", "无", "未知"}


@dataclass
class DecodedFile:
    text: str
    encoding: str
    delimiter: str
    columns: list[str]
    rows: list[tuple[int, dict[str, str]]]
    """``rows`` 为 ``(原始行号, 原始字段映射)``，行号从 1 开始且不含表头。"""


class CsvParseError(ValueError):
    """CSV 无法解析。"""


def sniff_delimiter(sample: str) -> str:
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters="".join(DELIMITERS))
        return dialect.delimiter
    except csv.Error:
        # 退化为按出现次数选择
        counts = {item: sample.count(item) for item in DELIMITERS}
        best = max(counts, key=lambda key: counts[key])
        return best if counts[best] > 0 else ","


def decode_bytes(raw: bytes) -> tuple[str, str]:
    """返回 ``(文本, 使用的编码)``。"""
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig"), "utf-8-sig"
    for encoding in ENCODINGS:
        try:
            text = raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
        # GB18030 能解码任意字节序列，需要用"是否产生大量替换字符"来排除误判
        if text.count("\ufffd") > 0 and encoding != "utf-8":
            continue
        return text, encoding
    raise CsvParseError("无法识别文件编码，请另存为 UTF-8 或 GB18030 后重试")


def normalise_header(value: str) -> str:
    return re.sub(r"[\s\u3000]+", "", (value or "").strip().lower())


def auto_mapping(columns: list[str]) -> dict[str, str]:
    """根据表头自动建立字段映射。"""
    mapping: dict[str, str] = {}
    normalised = {column: normalise_header(column) for column in columns}
    for field, aliases in COLUMN_ALIASES.items():
        for column, key in normalised.items():
            if key in {normalise_header(alias) for alias in aliases}:
                mapping[field] = column
                break
    return mapping


def parse_csv(raw: bytes) -> DecodedFile:
    if not raw:
        raise CsvParseError("文件内容为空")
    text, encoding = decode_bytes(raw)
    stripped = text.lstrip("\ufeff")
    if not stripped.strip():
        raise CsvParseError("文件内容为空")

    sample = stripped[:8192]
    delimiter = sniff_delimiter(sample)

    reader = csv.reader(io.StringIO(stripped, newline=""), delimiter=delimiter)
    try:
        header = next(reader)
    except StopIteration as exc:
        raise CsvParseError("文件缺少表头") from exc

    columns = [str(item).strip() for item in header]
    if not any(columns):
        raise CsvParseError("表头为空")

    rows: list[tuple[int, dict[str, str]]] = []
    for index, values in enumerate(reader, start=1):
        if not any(str(item).strip() for item in values):
            continue  # 跳过完全空行
        record = {
            columns[position]: (values[position] if position < len(values) else "")
            for position in range(len(columns))
        }
        rows.append((index, record))

    return DecodedFile(
        text=text,
        encoding=encoding,
        delimiter=delimiter,
        columns=columns,
        rows=rows,
    )


def normalise_direction(raw: str | None) -> str | None:
    if raw is None:
        return None
    value = str(raw).strip().lower()
    if value in UNKNOWN_VALUES:
        return None
    return DIRECTION_ALIASES.get(value)


def normalise_state(raw: str | None, *, default: str = "scheduled") -> tuple[str | None, bool]:
    """返回 ``(状态, 是否识别成功)``。"""
    if raw is None or str(raw).strip().lower() in UNKNOWN_VALUES:
        return default, True
    value = str(raw).strip().lower()
    mapped = STATE_ALIASES.get(value)
    if mapped is None:
        return None, False
    return mapped, True


def is_blank(value: str | None) -> bool:
    return value is None or str(value).strip().lower() in UNKNOWN_VALUES
