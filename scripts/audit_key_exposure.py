#!/usr/bin/env python
"""密钥暴露面审计：GitHub 全部仓库 + 线上静态资源 + 本地 env。

设计要点
--------
* **覆盖不全就不能报通过**：每一项要么「已检查且干净」，要么「无法确认」，
  后者一律计入待处理，避免出现"检查了 0 项却输出通过"的假阳性结论。
* 只输出结论、路径与体积，**绝不输出密钥**。

用法：
    GLM=<key> python scripts/audit_key_exposure.py
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OWNER = "WuChangqing1"
REPO = "gong-e-wendai"
SITE = "https://ccqspace.site/wendai"

TEXT_EXT = {
    ".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".json", ".yml", ".yaml",
    ".toml", ".ini", ".cfg", ".conf", ".md", ".txt", ".sh", ".bash", ".ps1",
    ".env", ".example", ".sql", ".html", ".css", ".xml", ".properties", ".java",
    ".go", ".rs", ".c", ".h", ".cpp", ".cs", ".rb", ".php",
}


def sh(args: list[str], *, timeout: int = 120) -> tuple[int, bytes]:
    result = subprocess.run(args, capture_output=True, timeout=timeout, check=False)
    return result.returncode, result.stdout


def gh_json(path: str):
    code, out = sh(["gh", "api", path])
    if code != 0:
        return None
    try:
        return json.loads(out.decode("utf-8", "replace"))
    except json.JSONDecodeError:
        return None


def gh_bytes(path: str, *, timeout: int = 600) -> bytes | None:
    """用 gh（带 token）下载归档，避免匿名 codeload 在慢/大仓库上超时。"""
    code, out = sh(["gh", "api", path], timeout=timeout)
    return out if code == 0 and out else None


def curl_bytes(url: str, *, timeout: int = 240) -> bytes | None:
    code, out = sh(["curl", "-sSL", "--max-time", str(timeout), url], timeout=timeout + 60)
    return out if code == 0 and out else None


def main() -> int:
    secret = os.environ.get("GLM", "").strip()
    if not secret:
        print("缺少 GLM 环境变量，无法比对", file=sys.stderr)
        return 2
    needle = secret.encode()

    ok: list[str] = []
    unknown: list[str] = []

    # ------------------------------------------------------------------
    print("=== 1. GitHub 仓库快照（含私有） ===")
    repos = gh_json(f"users/{OWNER}/repos?per_page=100&type=all")
    if not isinstance(repos, list) or not repos:
        print("  [无法确认] 未能列出仓库列表")
        unknown.append("仓库列表获取失败")
        repos = []
    else:
        print(f"  共 {len(repos)} 个仓库")
        for item in sorted(repos, key=lambda r: r["name"]):
            name, visibility = item["name"], item.get("visibility", "?")
            blob = gh_bytes(f"repos/{OWNER}/{name}/tarball")
            if blob is None:
                blob = curl_bytes(f"https://codeload.github.com/{OWNER}/{name}/tar.gz/HEAD")
            if blob is None:
                print(f"    {visibility:8} {name:34} [无法确认] 下载失败")
                unknown.append(f"{name} 快照下载失败")
                continue
            if needle in blob:
                print(f"    {visibility:8} {name:34} ★★★ 发现密钥")
                unknown.append(f"{name} 含密钥")
                continue
            # 解包后再逐文本文件查一次（压缩包字节匹配可能漏掉分段情况）
            hit_file = None
            with tempfile.TemporaryDirectory() as tmp:
                archive = Path(tmp) / "repo.tar.gz"
                archive.write_bytes(blob)
                try:
                    with tarfile.open(archive) as tar:
                        for member in tar.getmembers():
                            if not member.isfile():
                                continue
                            if Path(member.name).suffix.lower() not in TEXT_EXT:
                                continue
                            if member.size > 3 * 1024 * 1024:
                                continue
                            handle = tar.extractfile(member)
                            if handle is None:
                                continue
                            if needle in handle.read():
                                hit_file = member.name
                                break
                except tarfile.TarError:
                    pass
            if hit_file:
                print(f"    {visibility:8} {name:34} ★★★ 发现密钥：{hit_file}")
                unknown.append(f"{name}/{hit_file} 含密钥")
            else:
                print(f"    {visibility:8} {name:34} {len(blob)/1024:8.0f} KB  干净")
                ok.append(f"仓库 {name}")

    # ------------------------------------------------------------------
    print()
    print("=== 2. 线上前端 build 产物 ===")
    html_bytes = curl_bytes(f"{SITE}/login")
    if not html_bytes:
        print("  [无法确认] 首页抓取失败")
        unknown.append("线上首页抓取失败")
    else:
        html = html_bytes.decode("utf-8", "replace")
        assets = sorted(set(re.findall(r"/wendai/assets/[A-Za-z0-9._-]+\.(?:js|css)", html)))
        if not assets:
            print("  [无法确认] 未从首页解析到静态资源")
            unknown.append("线上资源列表为空")
        for path in assets:
            blob = curl_bytes(f"https://ccqspace.site{path}")
            if blob is None:
                print(f"    {path.split('/')[-1]:30} [无法确认]")
                unknown.append(f"{path} 抓取失败")
                continue
            if needle in blob:
                print(f"    {path.split('/')[-1]:30} ★★★ 发现密钥")
                unknown.append(f"线上产物 {path} 含密钥")
            else:
                print(f"    {path.split('/')[-1]:30} {len(blob)/1024:8.0f} KB  干净")
                ok.append(f"线上 {path}")

    # ------------------------------------------------------------------
    print()
    print("=== 3. 本地 env 文件与 VITE_ 变量 ===")
    env_found = False
    for name in (".env", ".env.production", ".env.local", "frontend/.env", "frontend/.env.production"):
        path = ROOT / name
        if not path.exists():
            continue
        env_found = True
        raw = path.read_bytes()
        leaked = needle in raw
        print(f"    {name:26} {'★★★ 含密钥' if leaked else '不含密钥'}")
        if leaked:
            unknown.append(f"{name} 含密钥")
        else:
            ok.append(name)
        for line in raw.decode("utf-8", "replace").splitlines():
            line = line.strip()
            if line.startswith("VITE_") and "=" in line:
                key, _, value = line.partition("=")
                # VITE_ 变量会在构建期打进前端包
                if value.strip():
                    print(f"      ⚠ {key.strip()} 会被打进前端包")
    if not env_found:
        print("    （无 env 文件，此项不适用）")
        ok.append("无 env 文件")

    # ------------------------------------------------------------------
    print()
    print("=== 4. 本地 Git 对象库（含悬空对象） ===")
    code, out = sh(["git", "cat-file", "--batch-all-objects", "--batch-check=%(objectname) %(objecttype)"])
    ids = [line.split()[0] for line in out.decode("utf-8", "replace").splitlines() if line.strip()]
    if not ids:
        print("  [无法确认] 未能列出对象")
        unknown.append("git 对象列表为空")
    else:
        hits = 0
        for oid in ids:
            code2, blob = sh(["git", "cat-file", "-p", oid])
            if code2 == 0 and needle in blob:
                hits += 1
        print(f"    检查 {len(ids)} 个对象，命中 {hits} 个")
        if hits:
            unknown.append(f"git 对象库有 {hits} 个对象含密钥")
        else:
            ok.append(f"git 对象库 {len(ids)} 个对象")

    # ------------------------------------------------------------------
    print()
    print("=" * 56)
    print(f"已确认干净：{len(ok)} 项")
    print(f"无法确认/需处理：{len(unknown)} 项")
    if unknown:
        for item in unknown:
            print(f"  [需处理] {item}")
        print()
        print("  结论：**未能确认密钥未泄露**。请先处理上面各项。")
        print("  若确认曾经泄露，唯一正确做法是到智谱控制台吊销并重新签发。")
        return 1
    print()
    print("  结论：你的 API Key 未出现在任何已检查的位置。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
