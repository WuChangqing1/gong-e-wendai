#!/usr/bin/env python
"""SQLite 一致性备份。

使用 SQLite 在线备份 API，在 WAL 模式下也能得到一致快照。
备份文件写入 ``BACKUP_DIR``（默认 ``<项目>/data/backups``，生产为数据目录下的 backups）。

用法::

    python scripts/backup_db.py                 # 备份一次
    python scripts/backup_db.py --keep 14       # 备份并清理 14 天前的备份
    python scripts/backup_db.py --out /path     # 指定备份目录
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND_DIR))


def human(size: int) -> str:
    units = ["B", "KB", "MB", "GB"]
    value = float(size)
    index = 0
    while value >= 1024 and index < len(units) - 1:
        value /= 1024
        index += 1
    return f"{value:.1f} {units[index]}"


def main() -> int:
    parser = argparse.ArgumentParser(description="工 e 稳袋 数据库备份")
    parser.add_argument("--out", default=None, help="备份目录（默认数据目录下的 backups/）")
    parser.add_argument("--keep", type=int, default=0, help="保留天数，超过则清理（0 表示不清理）")
    args = parser.parse_args()

    from app.core.config import settings  # noqa: PLC0415

    db_path = settings.database_path
    if db_path is None:
        print("当前 DATABASE_URL 不是 SQLite，未提供备份能力", file=sys.stderr)
        return 2
    if not db_path.exists():
        print(f"数据库不存在：{db_path}", file=sys.stderr)
        return 2

    backup_dir = Path(args.out) if args.out else db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    target = backup_dir / f"app-{stamp}.db"

    source = sqlite3.connect(str(db_path))
    dest = sqlite3.connect(str(target))
    try:
        with dest:
            source.backup(dest)
    finally:
        dest.close()
        source.close()

    size = target.stat().st_size
    print(f"备份完成：{target}  ({human(size)})")

    if args.keep > 0:
        deadline = datetime.now(UTC) - timedelta(days=args.keep)
        removed = 0
        for item in backup_dir.glob("app-*.db"):
            try:
                created = datetime.strptime(item.stem.removeprefix("app-"), "%Y%m%d-%H%M%S")
            except ValueError:
                continue
            if created.replace(tzinfo=UTC) < deadline:
                item.unlink()
                removed += 1
        print(f"已清理 {removed} 个超过 {args.keep} 天的备份")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
