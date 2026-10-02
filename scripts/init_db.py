#!/usr/bin/env python
"""初始化数据库：执行全部 Alembic 迁移并输出结果。

用法::

    cd backend
    python ../scripts/init_db.py
    # 或
    alembic upgrade head
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND_DIR))


def main() -> int:
    from app.core.config import settings  # noqa: PLC0415
    from app.core.database import check_database  # noqa: PLC0415

    settings.ensure_runtime_dirs()
    db_path = settings.database_path
    print(f"环境          : {settings.app_env}")
    print(f"数据库地址    : {db_path if db_path else settings.database_url}")
    print(f"上传目录      : {settings.upload_path}")
    print("执行迁移      : alembic upgrade head")

    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_DIR,
        check=False,
    )
    if result.returncode != 0:
        print("迁移失败", file=sys.stderr)
        return result.returncode

    print(f"数据库可用    : {check_database()}")
    print("初始化完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
