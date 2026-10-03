#!/usr/bin/env python
"""推送前的密钥泄露自检。

检查内容
--------
1. 当前进程的 GLM 密钥是否出现在任何 **Git 已跟踪或已暂存** 的文件里
2. 是否存在不该入库的敏感文件（``.env``、``.env.production``、``app.db``、
   ``uploads/*``、``logs/*``、私钥等）

输出纪律
--------
只输出**文件路径**，绝不输出匹配到的内容，也绝不输出密钥本身。
每次 push 之前都应执行本脚本。

用法::

    python scripts/check_secrets.py
    python scripts/check_secrets.py --include-history   # 额外扫描最近提交的 diff
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: 绝不允许进入版本库的路径与文件名
FORBIDDEN_PATTERNS = (
    re.compile(r"(^|/)\.env$"),
    re.compile(r"(^|/)\.env\.(production|prod|local|staging)$"),
    re.compile(r"\.db(-wal|-shm)?$"),
    re.compile(r"\.sqlite3?$"),
    re.compile(r"(^|/)uploads/[^/]+$"),
    re.compile(r"(^|/)logs?/[^/]+$"),
    re.compile(r"\.(pem|key|p12|pfx)$"),
    re.compile(r"(^|/)secrets?/"),
    re.compile(r"(^|/)credentials\.json$"),
)

#: 允许存在的模板文件
ALLOWED_EXCEPTIONS = (".env.example",)


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
    if result.returncode != 0:
        return ""
    return result.stdout


def tracked_files() -> list[str]:
    return [line.strip() for line in git("ls-files").splitlines() if line.strip()]


def staged_files() -> list[str]:
    output = git("diff", "--cached", "--name-only")
    return [line.strip() for line in output.splitlines() if line.strip()]


def read_secret() -> str:
    """读取当前进程可用的 GLM 密钥。只检查是否存在，不输出内容。

    查找顺序：进程环境变量 → Windows 机器级 → Windows 用户级。

    Windows 的**机器级**环境变量在
    ``HKLM\\SYSTEM\\CurrentControlSet\\Control\\Session Manager\\Environment``，
    不是 ``HKLM\\Environment``（XP 时代遗留位置，现代系统为空）。
    """
    value = os.environ.get("GLM", "")
    if value:
        return value.strip()
    if os.name != "nt":
        return ""
    try:
        import winreg  # noqa: PLC0415

        for root, sub in (
            (
                winreg.HKEY_LOCAL_MACHINE,
                r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
            ),
            (winreg.HKEY_LOCAL_MACHINE, "Environment"),  # 兼容旧位置
            (winreg.HKEY_CURRENT_USER, "Environment"),
        ):
            try:
                with winreg.OpenKey(root, sub) as key:
                    found, _ = winreg.QueryValueEx(key, "GLM")
                    if found:
                        return str(found).strip()
            except OSError:
                continue
    except ImportError:  # pragma: no cover - 非 Windows
        return ""
    return ""


def scan_for_secret(secret: str, paths: list[str]) -> list[str]:
    """返回包含密钥的文件路径。**不返回匹配内容。**"""
    hits: list[str] = []
    if not secret:
        return hits
    needle = secret.encode("utf-8")
    for relative in paths:
        target = ROOT / relative
        try:
            if not target.is_file():
                continue
            if target.stat().st_size > 20 * 1024 * 1024:
                continue
            if needle in target.read_bytes():
                hits.append(relative)
        except OSError:
            continue
    return hits


def scan_working_tree_for_secret(secret: str) -> list[str]:
    """扫描所有未被忽略的工作区文件（防止密钥刚写进新文件就被提交）。"""
    if not secret:
        return []
    output = git("status", "--porcelain", "--untracked-files=all")
    candidates = []
    for line in output.splitlines():
        if len(line) < 4:
            continue
        path = line[3:].strip().strip('"')
        if path:
            candidates.append(path)
    return scan_for_secret(secret, candidates)


def scan_forbidden_paths(paths: list[str]) -> list[str]:
    offenders: list[str] = []
    for relative in paths:
        if any(relative.endswith(item) or relative == item for item in ALLOWED_EXCEPTIONS):
            continue
        for pattern in FORBIDDEN_PATTERNS:
            if pattern.search(relative):
                offenders.append(relative)
                break
    return offenders


def main() -> int:
    parser = argparse.ArgumentParser(description="检查敏感信息是否进入版本库")
    parser.add_argument(
        "--include-history",
        action="store_true",
        help="额外扫描最近一次提交的 diff",
    )
    args = parser.parse_args()

    secret = read_secret()
    files = tracked_files()
    staged = staged_files()

    problems: list[str] = []

    # 1. 不该入库的文件
    forbidden = scan_forbidden_paths(sorted(set(files + staged)))
    if forbidden:
        print("发现不应进入版本库的文件：")
        for path in forbidden:
            print(f"  - {path}")
        problems.extend(forbidden)

    # 2. 密钥是否出现在受版本控制的文件里
    hit = scan_for_secret(secret, sorted(set(files + staged)))
    if hit:
        print("发现敏感信息：")
        for path in hit:
            print(f"  - {path}")
        problems.extend(hit)

    # 3. 密钥是否出现在尚未提交的工作区文件里
    working_hits = scan_working_tree_for_secret(secret)
    if working_hits:
        print("发现敏感信息（尚未提交）：")
        for path in working_hits:
            print(f"  - {path}")
        problems.extend(working_hits)

    # 4. 最近一次提交的 diff
    if args.include_history and secret:
        diff = git("show", "--no-color", "--format=", "HEAD")
        if diff and secret in diff:
            print("发现敏感信息：最近一次提交的内容")
            problems.append("HEAD")

    if problems:
        print()
        print(f"检查失败：{len(set(problems))} 处需要处理。")
        print("提示：把值改从环境变量读取，并确认 .gitignore 覆盖对应文件。")
        return 1

    if not secret:
        print("未在本机找到 GLM 环境变量；仅完成文件路径检查。")
    print("检查通过：未发现敏感信息进入版本库。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
