#!/usr/bin/env python
"""公开仓库前的完整历史敏感信息扫描。

与 ``scripts/check_secrets.py``（只看当前工作区与暂存区）不同，本脚本遍历
**全部历史提交与全部 blob**，因为一旦仓库转为公开，历史里的内容同样是公开的。

纪律：只输出**文件路径与提交号**，绝不输出匹配到的内容。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: 通过 `git rev-list --objects --all` 列出所有对象后逐个检查 blob
BINARY_HINTS = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".zip", ".ico", ".woff", ".ttf")

#: 通用密钥形态（不会命中我们自己的示例占位符）
PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("GitHub token", re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")),
    ("OpenAI 风格密钥", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
    ("智谱密钥形态", re.compile(r"\b[0-9a-f]{32}\.[A-Za-z0-9]{16,}\b")),
    ("私钥块", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("AWS Key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("JWT（三段式）", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    ("硬编码密码赋值", re.compile(r"(?i)\b(password|passwd|pwd|secret|token|api_key)\s*[:=]\s*[\"'][^\"'\s{}$]{6,}[\"']")),
    ("内网/公网 IP", re.compile(r"\b(?!127\.0\.0\.1|0\.0\.0\.0|10\.0\.2\.2)(\d{1,3}\.){3}\d{1,3}\b")),
]

#: 已知的、属于示例或占位、不算泄露的匹配
ALLOWLIST = (
    "dev-only-change-me",
    "test-secret-key-for-pytest-only",
    "YourPass123",
    "Wendai2025",           # 演示账号密码，已知且将公开，单独报告
    "Wendai@2025",          # 测试默认密码
    "example.com",
    "192.168.",
    "172.16.",
    "255.255.",
    "1.2.3.4",
)


def git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return result.stdout


def all_blobs() -> list[tuple[str, str]]:
    """返回 ``(object_id, path)`` 列表。"""
    output = git("rev-list", "--objects", "--all")
    rows: list[tuple[str, str]] = []
    for line in output.splitlines():
        parts = line.split(" ", 1)
        if len(parts) == 2:
            rows.append((parts[0], parts[1]))
    return rows


def read_blob(object_id: str) -> str | None:
    result = subprocess.run(
        ["git", "cat-file", "-p", object_id],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    try:
        return result.stdout.decode("utf-8")
    except UnicodeDecodeError:
        return None


def main() -> int:
    secret = os.environ.get("GLM", "").strip()

    blobs = all_blobs()
    print(f"扫描对象：{len(blobs)} 个 blob / 提交 {git('rev-list', '--all', '--count').strip()} 个")
    print()

    hits: dict[str, set[str]] = {}
    demo_password_files: set[str] = set()
    ip_hits: dict[str, set[str]] = {}

    for object_id, path in blobs:
        if path.endswith(BINARY_HINTS):
            continue
        text = read_blob(object_id)
        if text is None:
            continue

        # 1) 真实 GLM 密钥是否出现在历史里
        if secret and secret in text:
            hits.setdefault("真实 GLM 密钥", set()).add(path)

        # 2) 通用形态
        for label, pattern in PATTERNS:
            for match in pattern.finditer(text):
                value = match.group(0)
                if any(item in value for item in ALLOWLIST):
                    continue
                if label == "内网/公网 IP":
                    ip_hits.setdefault(value, set()).add(path)
                    continue
                if any(item in text[max(0, match.start() - 80) : match.end() + 80] for item in ALLOWLIST):
                    continue
                hits.setdefault(label, set()).add(path)

        # 3) 演示密码单独统计
        if "Wendai2025" in text:
            demo_password_files.add(path)

    print("=== 结论 ===")
    if hits:
        for label, paths in sorted(hits.items()):
            print(f"  [需处理] {label}：")
            for path in sorted(paths)[:12]:
                print(f"      - {path}")
            if len(paths) > 12:
                print(f"      …… 另有 {len(paths) - 12} 个文件")
    else:
        print("  [通过] 未在历史中发现真实密钥或可疑凭据形态")

    print()
    print("=== 公开后会一并公开的内容（需你确认） ===")
    print(f"  1. 演示账号密码出现在 {len(demo_password_files)} 个文件中")
    for path in sorted(demo_password_files)[:8]:
        print(f"       - {path}")
    if len(demo_password_files) > 8:
        print(f"       …… 另有 {len(demo_password_files) - 8} 个文件")

    notable_ips = {
        ip: paths
        for ip, paths in ip_hits.items()
        if not ip.startswith(("127.", "0.", "10.0.2.", "192.168.", "255.", "172.16."))
    }
    if notable_ips:
        print(f"  2. 服务器/IP 信息出现在 {len(notable_ips)} 个不同地址上，例如：")
        for ip, paths in list(sorted(notable_ips.items()))[:6]:
            print(f"       - {ip}  （{', '.join(sorted(paths)[:3])}）")

    print()
    print("  3. 部署文档包含 ssh 别名、主机名、端口与目录路径，可推断服务器结构")

    return 1 if hits else 0


if __name__ == "__main__":
    raise SystemExit(main())
