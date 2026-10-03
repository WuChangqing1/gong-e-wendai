"""生产库账号与角色审计（只读，不做任何修改）。

用途：确认是否存在彼此独立的 admin 账号，以及是否有普通经营账号被额外
授予了 admin 角色。脚本只读取，不写入。
"""

from __future__ import annotations

import sqlite3

DB = "/home/ubuntu/apps/gong-e-wendai-data/app.db"

conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
try:
    print("users:", conn.execute("select count(*) from users").fetchone()[0])
    rows = conn.execute(
        """
        select u.username, u.status, u.display_name,
               (select group_concat(r.role) from user_roles r where r.user_id = u.id)
        from users u
        order by u.created_at
        """
    ).fetchall()
    print("--- all accounts ---")
    for row in rows:
        print(" | ".join("" if item is None else str(item) for item in row))

    admins = conn.execute(
        "select u.username from users u join user_roles r on r.user_id = u.id where r.role = 'admin'"
    ).fetchall()
    print("--- admins ---")
    print([row[0] for row in admins])

    both = conn.execute(
        """
        select u.username from users u
        where exists (select 1 from user_roles r where r.user_id = u.id and r.role = 'admin')
          and exists (select 1 from user_roles r2 where r2.user_id = u.id and r2.role = 'merchant')
        """
    ).fetchall()
    print("--- merchant+admin ---")
    print([row[0] for row in both])

    consultants = conn.execute(
        "select u.username from users u join user_roles r on r.user_id = u.id where r.role = 'consultant'"
    ).fetchall()
    print("--- consultants ---")
    print([row[0] for row in consultants])

    print("--- analysis results ---")
    print("total/stale:", conn.execute("select count(*), sum(is_stale) from analysis_results").fetchone())
    print(
        "engine versions:",
        conn.execute(
            "select engine_version, count(*) from analysis_results group by engine_version"
        ).fetchall(),
    )
finally:
    conn.close()
