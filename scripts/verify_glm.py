"""GLM 连通性验证（只输出结论，绝不输出密钥）。

用法：
    python scripts/verify_glm.py            # 只验证文本模型
    python scripts/verify_glm.py --vision   # 同时验证视觉模型

密钥来源优先级：进程环境变量 GLM > Windows Machine 环境变量 GLM。
脚本**不会**打印密钥、密钥前缀、后缀或长度。
"""

from __future__ import annotations

import argparse
import base64
import os
import struct
import sys
import zlib
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND_DIR))


def read_secret() -> str:
    """读取密钥。只检查是否存在，不输出内容。

    查找顺序：进程环境变量 → Windows 机器级 → Windows 用户级。

    Windows 的**机器级**环境变量存放在
    ``HKLM\\SYSTEM\\CurrentControlSet\\Control\\Session Manager\\Environment``，
    而不是 ``HKLM\\Environment``（后者是 XP 时代的遗留位置，现代系统上为空）。
    读错位置会让「系统环境变量里明明设置了 GLM」被判成不存在。
    """
    value = os.environ.get("GLM", "")
    if value:
        return str(value).strip()

    if os.name != "nt":
        return ""

    import winreg  # noqa: PLC0415

    machine = (
        r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"
    )
    candidates = (
        (winreg.HKEY_LOCAL_MACHINE, machine),
        (winreg.HKEY_LOCAL_MACHINE, r"Environment"),  # 兼容旧位置
        (winreg.HKEY_CURRENT_USER, r"Environment"),
    )
    for root, sub in candidates:
        try:
            with winreg.OpenKey(root, sub) as key:
                found, _ = winreg.QueryValueEx(key, "GLM")
        except OSError:
            continue
        if found:
            return str(found).strip()
    return ""


def make_test_png(text_hint: str = "结算通知 1288.00 元") -> bytes:
    """生成一张无敏感信息的测试 PNG（纯色 + 简单像素块，不含真实数据）。"""
    width, height = 240, 120
    # 白色背景 + 中间一条浅灰横带，模拟通知卡片版面
    rows = []
    for y in range(height):
        row = bytearray()
        for _x in range(width):
            band = 200 <= y <= 210
            row.extend((230, 230, 230) if band else (255, 255, 255))
        rows.append(bytes(row))

    raw = b"".join(b"\x00" + row for row in rows)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", header)
    png += chunk(b"IDAT", zlib.compress(raw, 9))
    png += chunk(b"IEND", b"")
    _ = text_hint
    return png


def main() -> int:
    parser = argparse.ArgumentParser(description="验证 GLM 连通性")
    parser.add_argument("--vision", action="store_true", help="同时验证视觉模型")
    args = parser.parse_args()

    from app.core.config import settings
    from app.core.errors import AIServiceError
    from app.services.ai_service import AIService

    key = read_secret()
    if not key:
        print("未找到 GLM 环境变量（进程 / Machine / User 均不存在）", file=sys.stderr)
        return 2

    settings.ai_enabled = True
    settings.glm_api_key = key
    print(f"credential: configured (provider={settings.ai_status_public()['provider']})")
    print(f"base_url: {settings.resolved_ai_base_url}")
    print(f"text_model: {settings.text_model}")

    service = AIService()
    try:
        result = service.extract_cash_event_from_text(
            "微信结算通知：结算款 1288.00 元，预计 2026-10-05 到账，尾号 8821。"
        )
    except AIServiceError as error:
        print(f"text model FAILED: {error}")
        return 1

    event = result.event
    print(
        "text model OK: "
        f"title={event.title!r} direction={event.direction} "
        f"amount_cents={event.amount_cents} scheduled_at={event.scheduled_at} "
        f"event_type={event.event_type}"
    )
    if event.amount_cents != 128800:
        print(f"NOTE: amount mismatch, got {event.amount_cents}")

    if args.vision:
        print(f"vision_model: {settings.vision_model}")
        png = make_test_png()
        print(f"test image: {len(png)} bytes (generated, no real data)")
        try:
            observed = service.extract_cash_event_from_image(png, media_type="image/png")
        except AIServiceError as error:
            print(f"vision model FAILED: {error}")
            return 1
        print(
            "vision model OK: "
            f"title={observed.event.title!r} direction={observed.event.direction} "
            f"warnings={len(observed.event.warnings)}"
        )
        encoded = base64.b64encode(png).decode("ascii")
        assert encoded  # 仅确认图片可编码，不输出内容

    print("verification complete; no credential was printed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
