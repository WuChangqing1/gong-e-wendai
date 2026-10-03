"""生产账号与角色清单（只读）。

按角色分组列出账号，并区分：
* 演示 / 功能账号（由 setup_demo_accounts.py 创建）
* 管理员（独立开通）
* e2e 测试残留账号（前缀可识别）
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict

DB = "/home/ubuntu/apps/gong-e-wendai-data/app.db"

#: 由 setup_demo_accounts.py 创建的固定演示账号
DEMO_ACCOUNTS = {"wangzhanggui", "wangtaitai", "zixunxiaoli"}

conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
try:
    rows = conn.execute(
        """
        select u.username, u.display_name, u.status,
               (select group_concat(r.role) from user_roles r where r.user_id = u.id),
               u.last_login_at
        from users u
        order by u.created_at
        """
    ).fetchall()

    by_role: dict[str, list[str]] = defaultdict(list)
    for username, _display, status, roles, _last in rows:
        for role in (roles or "").split(","):
            if role:
                by_role[role].append(f"{username}({status})")

    print("=== 按角色统计 ===")
    for role in sorted(by_role):
        print(f"  {role:16} 共 {len(by_role[role]):3} 个")

    print()
    print("=== 管理员（全部列出） ===")
    for name in by_role.get("admin", []):
        print("   ", name)

    print()
    print("=== 咨询人员（全部列出） ===")
    for name in by_role.get("consultant", []):
        print("   ", name)

    print()
    print("=== 家庭成员（全部列出） ===")
    for name in by_role.get("family_member", []):
        print("   ", name)

    merchants = sorted(by_role.get("merchant", []))
    real = [m for m in merchants if m.split("(")[0] in DEMO_ACCOUNTS]
    print()
    print(f"=== 经营者：共 {len(merchants)} 个 ===")
    print("  演示账号：")
    for name in real:
        print("   ", name)
    print(f"  其余 {len(merchants) - len(real)} 个为端到端测试残留账号（前缀如 ui/login/today/charts/... 等）")
    print("  示例：", ", ".join(m.split("(")[0] for m in merchants if m.split("(")[0] not in DEMO_ACCOUNTS)[:400])

    print()
    print("=== 有家庭关系的账号 ===")
    members = conn.execute(
        """
        select u.username, m.status, h.name
        from household_memberships m
        join users u on u.id = m.user_id
        join households h on h.id = m.household_id
        order by h.name, u.username
        """
    ).fetchall()
    for username, status, household in members:
        if username in DEMO_ACCOUNTS or household == "王家小院":
            print(f"   {username:16} {status:8} 家庭：{household}")

    print()
    print("=== 演示商户的资金算例 ===")
    demo = conn.execute(
        """
        select p.business_name, s.opening_balance_cents, s.pending_settlement_cents,
               p.default_buffer_amount_cents,
               (select count(*) from cash_events c where c.merchant_id = p.id) as events
        from merchant_profiles p
        left join business_account_snapshots s on s.merchant_id = p.id
        join users u on u.id = p.user_id
        where u.username = 'wangzhanggui'
        """
    ).fetchone()
    if demo:
        name, opening, pending, buffer, events = demo
        print(f"   经营名称：{name}")
        print(f"   期初余额：{opening / 100:.2f} 元   待结算：{pending / 100:.2f} 元")
        print(f"   经营留底：{buffer / 100:.2f} 元   事项数：{events}")
finally:
    conn.close()
