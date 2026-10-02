#!/usr/bin/env python
"""生产构建脚本：检查前端产物并给出部署所需信息。

用法::

    python scripts/build_production.py          # 只检查
    python scripts/build_production.py --build  # 先执行 npm ci && npm run build
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIR = PROJECT_ROOT / "frontend"
BACKEND_DIR = PROJECT_ROOT / "backend"
DIST_DIR = FRONTEND_DIR / "dist"

sys.path.insert(0, str(BACKEND_DIR))


def run(command: list[str], cwd: Path) -> int:
    print(f"$ {' '.join(command)}  (cwd={cwd})")
    return subprocess.run(command, cwd=cwd, check=False).returncode


def main() -> int:
    parser = argparse.ArgumentParser(description="工 e 稳袋 生产构建")
    parser.add_argument("--build", action="store_true", help="实际执行 npm ci && npm run build")
    parser.add_argument("--skip-ci", action="store_true", help="跳过 npm ci，只跑 build")
    args = parser.parse_args()

    if args.build:
        if not args.skip_ci:
            code = run(["npm", "ci"], FRONTEND_DIR)
            if code != 0:
                print("npm ci 失败", file=sys.stderr)
                return code
        code = run(["npm", "run", "build"], FRONTEND_DIR)
        if code != 0:
            print("前端构建失败", file=sys.stderr)
            return code

    from app.core.config import settings  # noqa: PLC0415

    print("\n=== 构建检查 ===")
    index = DIST_DIR / "index.html"
    assets = DIST_DIR / "assets"
    problems: list[str] = []

    if not index.exists():
        problems.append(f"缺少前端构建产物：{index}")
    if not assets.exists() or not any(assets.glob("*.js")):
        problems.append(f"缺少前端静态资源：{assets}")

    print(f"前端产物目录   : {DIST_DIR}")
    if assets.exists():
        total = sum(item.stat().st_size for item in assets.iterdir() if item.is_file())
        print(f"静态资源体积   : {total / 1024 / 1024:.2f} MB")

    print(f"应用环境       : {settings.app_env}")
    print(f"数据库地址     : {settings.database_path or settings.database_url}")
    print(f"上传目录       : {settings.upload_path}")
    print(f"日志目录       : {settings.log_path}")
    print(f"智能服务       : {'已配置' if settings.ai_configured else '未启用'}")

    prod_problems = settings.validate_production()
    if settings.is_production:
        problems.extend(prod_problems)

    if problems:
        print("\n存在问题：")
        for item in problems:
            print(f"  - {item}")
        return 1

    print("\n构建检查通过。生产启动命令：")
    print("  uvicorn app.main:app --host 0.0.0.0 --port 18082 --workers 1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
