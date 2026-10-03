#!/usr/bin/env python
"""提交前守卫：阻止密钥与禁止入库的文件进入提交。

为什么需要它
------------
GitHub 的 secret scanning 只认识已知厂商的密钥形态，**智谱不在其中**。
一旦密钥被提交并推送，即使随后删除，历史里依然存在；仓库已公开，
等于永久泄露。因此本仓库自带这道守卫作为主要防线。

检查范围（只针对本次提交的暂存内容）
------------------------------------
1. 暂存区里是否有不该入库的文件（.env / *.db / uploads / logs / 私钥 …）
2. 暂存内容的 diff 里是否出现真实 GLM 密钥（需要环境变量 GLM）
3. 是否出现常见密钥形态（不依赖 GLM，队友没有密钥时同样有效）

只输出路径，绝不输出匹配到的内容。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

FORBIDDEN = (
    re.compile(r"(^|/)\.env$"),
    re.compile(r"(^|/)\.env\.(production|prod|local|staging)$"),
    re.compile(r"\.db(-wal|-shm)?$"),
    re.compile(r"\.sqlite3?$"),
    re.compile(r"(^|/)uploads/[^/]+$"),
    re.compile(r"(^|/)logs?/[^/]+$"),
    re.compile(r"\.(pem|key|p12|pfx)$"),
    re.compile(r"(^|/)secrets?/"),
    re.compile(r"(^|/)credentials\.json$"),
    re.compile(r"(^|/)admin-credentials\.txt$"),
)

ALLOWED = (".env.example",)

#: 已知厂商密钥形态（不依赖本机是否有 GLM）
SHAPES = (
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("GitHub PAT", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b")),
    ("OpenAI 风格密钥", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
    ("智谱密钥形态", re.compile(r"\b[0-9a-f]{32}\.[A-Za-z0-9]{8,}\b")),
    ("私钥块", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("阿里云 AK", re.compile(r"\bLTAI[0-9A-Za-z]{12,}\b")),
    ("AWS AK", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
)

#: 允许出现（示例/占位/测试）
ALLOW_VALUES = (
    "dev-only-change-me",
    "test-secret-key-for-pytest-only",
    "YourPass123",
    "Wendai2025",
    "Wendai@2025",
    "example.com",
    "0000000000000000",
)


def git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], capture_output=True, text=True, encoding="utf-8", errors="replace", check=False
    )
    return result.stdout


def main() -> int:
    staged = [line.strip() for line in git("diff", "--cached", "--name-only").splitlines() if line.strip()]
    if not staged:
        return 0

    problems: list[str] = []

    for path in staged:
        if any(path.endswith(item) or path == item for item in ALLOWED):
            continue
        for pattern in FORBIDDEN:
            if pattern.search(path):
                problems.append(f"禁止入库的文件：{path}")
                break

    # 暂存 diff（新增行）里查密钥
    diff = git("diff", "--cached", "--unified=0")
    added = [line[1:] for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++")]

    secret = os.environ.get("GLM", "").strip()
    if secret:
        for line in added:
            if secret in line:
                problems.append("暂存内容中出现真实的 GLM API Key")
                break

    for line in added:
        for label, pattern in SHAPES:
            match = pattern.search(line)
            if not match:
                continue
            if any(item in match.group(0) for item in ALLOW_VALUES):
                continue
            if any(item in line for item in ALLOW_VALUES):
                continue
            problems.append(f"暂存内容中出现疑似密钥：{label}")
            break

    if problems:
        print("提交被阻止：发现敏感信息", file=sys.stderr)
        for item in dict.fromkeys(problems):
            print(f"  - {item}", file=sys.stderr)
        print(file=sys.stderr)
        print("处理方式：", file=sys.stderr)
        print("  * 值改从环境变量读取，不要把真实值写进代码", file=sys.stderr)
        print("  * 确认 .gitignore 覆盖对应文件后 git rm --cached <文件>", file=sys.stderr)
        print("  * 若确实需要提交示例，请使用明显的占位符", file=sys.stderr)
        print("  * 如已推送过，请立即吊销并重新签发该密钥", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
