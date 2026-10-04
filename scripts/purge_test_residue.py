#!/usr/bin/env python
"""清理生产库中的测试残留账号（默认只报告，``--apply`` 才写库）。

背景
----
历史验收轮次在公网入口跑过端到端测试，库里积压了大量测试账号：

* ``端到端掌柜 / 端到端小吃店``（测试夹具写死的名称）
* ``界面注册掌柜``、``空数据掌柜``、``家庭成员小王``、脚本开通的咨询人员
* 早期脚本写中文时被 GBK 破坏成 ``????`` 的账号

这些账号没有任何业务价值，却让账号总数看起来像是有几百个真实经营者。

原则
----
* **三个正式账号绝不删除**：用户名命中保护名单时，脚本直接中止而不是静默跳过。
* 只删除「有明确测试特征」的账号：显示名 / 经营名命中特征名单，或名称已损坏成 ``?``。
* 连带删除这些账号**自己的**业务数据（事项、历史、结算、家庭、咨询、分析结果…），
  正式账号的数据不在删除范围内。
* ``xitongguanli``（已停用、无角色）保留：它是 V3「管理员身份移除」的审计主体。
* 删除前自动做一次 SQLite 在线备份。
* ``audit_logs`` 保持追加语义，不删除历史审计记录。

用法::

    python scripts/purge_test_residue.py              # 只报告
    python scripts/purge_test_residue.py --apply      # 备份后执行删除
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND_DIR))

#: 绝不删除的账号（正式业务身份 + 已停用的旧管理员）
PROTECTED_USERNAMES = (
    "wangzhanggui",
    "wangtaitai",
    "zixunxiaoli",
    "xitongguanli",
)

#: 测试夹具写死的显示名
RESIDUE_DISPLAY_NAMES = (
    "端到端掌柜",
    "界面注册掌柜",
    "空数据掌柜",
    "家庭成员小王",
    "验收掌柜",
)

#: 测试夹具写死的经营名
RESIDUE_BUSINESS_NAMES = (
    "端到端小吃店",
    "登录测试店",
    "界面注册小吃店",
    "空数据小店",
    "验收小铺",
)

#: 名称被编码破坏的判定：连续两个问号即可认定
MOJIBAKE_MARKER = "??"


def _ensure_utf8_output() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):  # pragma: no cover
            pass


def _is_mojibake(text: str | None) -> bool:
    return bool(text) and MOJIBAKE_MARKER in text


def classify(row: sqlite3.Row) -> str | None:
    """返回删除理由；返回 None 表示保留。"""
    username = row["username"]
    if username in PROTECTED_USERNAMES:
        return None

    display = row["display_name"]
    business = row["business_name"]

    if display in RESIDUE_DISPLAY_NAMES or business in RESIDUE_BUSINESS_NAMES:
        return "名称命中测试特征"
    if _is_mojibake(display) or _is_mojibake(business):
        return "名称已损坏（? 乱码）"
    if username.startswith("consultant") and display == "咨询小李":
        return "脚本开通的咨询人员"
    if username.startswith("member") and display == "家庭成员小王":
        return "测试家庭成员"
    return None


def collect(conn: sqlite3.Connection) -> tuple[list[sqlite3.Row], list[tuple[str, str]]]:
    rows = conn.execute(
        """
        select u.id, u.username, u.display_name, u.status,
               (select group_concat(r.role) from user_roles r where r.user_id = u.id) roles,
               (select business_name from merchant_profiles p where p.user_id = u.id) business_name,
               (select count(*) from cash_events e where e.merchant_id =
                    (select id from merchant_profiles p2 where p2.user_id = u.id)) events,
               (select count(*) from consultation_cases c where c.merchant_id =
                    (select id from merchant_profiles p3 where p3.user_id = u.id)) cases
        from users u
        order by u.created_at, u.username
        """
    ).fetchall()

    targets: list[sqlite3.Row] = []
    reasons: list[tuple[str, str]] = []
    for row in rows:
        reason = classify(row)
        if reason:
            targets.append(row)
            reasons.append((row["username"], reason))
    return targets, reasons


def backup(db_path: Path) -> Path:
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    target = backup_dir / f"app-{stamp}-pre-purge.db"
    source = sqlite3.connect(str(db_path))
    dest = sqlite3.connect(str(target))
    try:
        with dest:
            source.backup(dest)
    finally:
        dest.close()
        source.close()
    return target


#: 删除顺序：先删依赖行，再删主体；scope 里的子查询都指向临时表
DELETE_PLAN: tuple[tuple[str, str], ...] = (
    ("household_card_comments", "card_id in (select id from _purge_cards) or user_id in (select id from _purge_users)"),
    ("household_card_reactions", "card_id in (select id from _purge_cards) or user_id in (select id from _purge_users)"),
    ("household_card_recipients", "card_id in (select id from _purge_cards) or user_id in (select id from _purge_users)"),
    ("household_cards", "id in (select id from _purge_cards)"),
    ("household_memberships", "household_id in (select id from _purge_households) or user_id in (select id from _purge_users)"),
    ("households", "id in (select id from _purge_households)"),
    ("cash_event_revisions", "cash_event_id in (select id from _purge_events)"),
    ("scenario_event_overrides", "scenario_id in (select id from _purge_scenarios) or cash_event_id in (select id from _purge_events)"),
    ("scenarios", "id in (select id from _purge_scenarios)"),
    ("reserve_advice_confirmations", "merchant_id in (select id from _purge_merchants)"),
    ("analysis_results", "merchant_id in (select id from _purge_merchants)"),
    ("consultation_updates", "case_id in (select id from _purge_cases)"),
    ("consultation_cases", "id in (select id from _purge_cases)"),
    ("daily_cash_history", "merchant_id in (select id from _purge_merchants)"),
    ("settlement_records", "merchant_id in (select id from _purge_merchants)"),
    ("enhancement_runs", "merchant_id in (select id from _purge_merchants)"),
    ("merchant_analysis_states", "merchant_id in (select id from _purge_merchants)"),
    ("import_batches", "merchant_id in (select id from _purge_merchants)"),
    ("source_records", "merchant_id in (select id from _purge_merchants)"),
    ("cash_events", "id in (select id from _purge_events)"),
    ("business_account_snapshots", "merchant_id in (select id from _purge_merchants)"),
    ("merchant_profiles", "id in (select id from _purge_merchants)"),
    ("refresh_sessions", "user_id in (select id from _purge_users)"),
    ("user_roles", "user_id in (select id from _purge_users)"),
    ("users", "id in (select id from _purge_users)"),
)


def purge(conn: sqlite3.Connection, target_ids: list[str]) -> list[tuple[str, int]]:
    conn.execute("create temp table _purge_users (id text primary key)")
    conn.executemany("insert into _purge_users values (?)", [(item,) for item in target_ids])
    conn.execute(
        "create temp table _purge_merchants as "
        "select id from merchant_profiles where user_id in (select id from _purge_users)"
    )
    conn.execute(
        "create temp table _purge_households as "
        "select id from households where merchant_id in (select id from _purge_merchants) "
        "or owner_id in (select id from _purge_users)"
    )
    conn.execute(
        "create temp table _purge_cards as "
        "select id from household_cards where merchant_id in (select id from _purge_merchants) "
        "or household_id in (select id from _purge_households)"
    )
    conn.execute(
        "create temp table _purge_events as "
        "select id from cash_events where merchant_id in (select id from _purge_merchants)"
    )
    conn.execute(
        "create temp table _purge_cases as "
        "select id from consultation_cases where merchant_id in (select id from _purge_merchants)"
    )
    conn.execute(
        "create temp table _purge_scenarios as "
        "select id from scenarios where merchant_id in (select id from _purge_merchants)"
    )

    results: list[tuple[str, int]] = []
    with conn:
        for table, scope in DELETE_PLAN:
            cursor = conn.execute(f'delete from "{table}" where {scope}')
            results.append((table, cursor.rowcount or 0))
    return results


def main(argv: list[str] | None = None) -> int:
    _ensure_utf8_output()
    parser = argparse.ArgumentParser(description="清理测试残留账号（默认只报告）")
    parser.add_argument("--apply", action="store_true", help="备份后真正执行删除")
    parser.add_argument("--db", default=None, help="数据库路径（缺省取 settings.database_path）")
    parser.add_argument("--verbose", action="store_true", help="逐条列出将被删除的账号")
    args = parser.parse_args(argv)

    from app.core.config import settings  # noqa: PLC0415

    db_path = Path(args.db) if args.db else settings.database_path
    if db_path is None or not Path(db_path).exists():
        print(f"数据库不存在：{db_path}", file=sys.stderr)
        return 2

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        targets, reasons = collect(conn)
        protected = [
            row
            for row in conn.execute(
                "select username from users where username in "
                f"({','.join('?' * len(PROTECTED_USERNAMES))})",
                PROTECTED_USERNAMES,
            )
        ]
        found = {row["username"] for row in protected}

        # 保护名单必须存在且不被判定为残留，否则中止而不是静默跳过
        missing = [name for name in PROTECTED_USERNAMES[:3] if name not in found]
        if missing:
            print(f"保护账号缺失：{missing}，已中止", file=sys.stderr)
            return 3
        leaked = sorted({name for name, _ in reasons if name in PROTECTED_USERNAMES})
        if leaked:
            print(f"保护账号被判定为残留：{leaked}，已中止", file=sys.stderr)
            return 3

        total_users = conn.execute("select count(*) c from users").fetchone()["c"]
        print(f"数据库：{db_path}")
        print(f"账号总数：{total_users}")
        print(f"保护账号（不删除）：{', '.join(sorted(found))}")
        print(f"待清理账号：{len(targets)}")

        by_reason: dict[str, int] = {}
        for _, reason in reasons:
            by_reason[reason] = by_reason.get(reason, 0) + 1
        for reason, count in sorted(by_reason.items(), key=lambda kv: -kv[1]):
            print(f"  - {reason}: {count}")

        with_events = sum(1 for row in targets if row["events"])
        with_cases = sum(1 for row in targets if row["cases"])
        print(f"其中带现金事项的账号 {with_events} 个，带咨询记录的账号 {with_cases} 个")

        if args.verbose:
            for row in targets:
                print(
                    f"    {row['username']:<28} display={row['display_name']!r} "
                    f"business={row['business_name']!r} events={row['events']} cases={row['cases']}"
                )

        if not targets:
            print("无待清理账号（幂等）")
            return 0

        if not args.apply:
            print("\n这是报告模式：未备份、未写入。确认后加 --apply 执行。")
            return 0

        target_backup = backup(Path(db_path))
        print(f"\n已备份：{target_backup}")

        results = purge(conn, [row["id"] for row in targets])
        print("\n=== 删除明细 ===")
        for table, count in results:
            if count:
                print(f"  {table}: {count}")

        remaining = conn.execute("select count(*) c from users").fetchone()["c"]
        print(f"\n账号总数：{total_users} → {remaining}")

        # 审计留痕（append-only）。审计会话绑定到同一个库文件，
        # 这样脚本在副本库上演练时不会把记录写到别的库。
        from sqlalchemy import create_engine  # noqa: PLC0415
        from sqlalchemy.orm import sessionmaker  # noqa: PLC0415

        from app.repositories.user_repo import AuditService  # noqa: PLC0415

        engine = create_engine(f"sqlite+pysqlite:///{db_path}")
        session_factory = sessionmaker(bind=engine)
        session = session_factory()
        try:
            AuditService(session).record(
                "identity.test_residue_purged",
                actor=None,
                resource_type="user",
                resource_id=None,
                metadata={
                    "removed_users": len(targets),
                    "users_before": total_users,
                    "users_after": remaining,
                    "deleted_rows": {table: count for table, count in results if count},
                    "protected": sorted(found),
                },
                actor_name="purge_test_residue.py",
            )
            session.commit()
        finally:
            session.close()
            engine.dispose()

        print("审计已记录：identity.test_residue_purged")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
