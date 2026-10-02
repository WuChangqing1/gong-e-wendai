#!/usr/bin/env python
"""创建或重置管理员账户。

管理员不支持自助注册（``ALLOW_ADMIN_REGISTRATION`` 默认关闭），必须通过本脚本创建。

用法::

    cd backend
    python ../scripts/create_admin.py --username admin --password 'YourPass123' --name 系统管理员
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND_DIR))


def main() -> int:
    parser = argparse.ArgumentParser(description="创建管理员账户")
    parser.add_argument("--username", required=True, help="账户名（4-32 位）")
    parser.add_argument("--password", required=True, help="密码（至少 8 位，含字母和数字）")
    parser.add_argument("--name", default="系统管理员", help="显示称呼")
    parser.add_argument("--reset-password", action="store_true", help="账户已存在时重置密码")
    args = parser.parse_args()

    username = args.username.strip().lower()
    if len(username) < 4 or len(username) > 32:
        print("账户名长度必须为 4-32 位", file=sys.stderr)
        return 2
    if len(args.password) < 8:
        print("密码至少 8 位", file=sys.stderr)
        return 2
    if args.password.isdigit() or args.password.isalpha():
        print("密码需要同时包含字母和数字", file=sys.stderr)
        return 2

    from app.core.database import SessionLocal
    from app.core.security import hash_password
    from app.models.user import ROLE_ADMIN, User, UserRole
    from app.repositories.user_repo import AuditRepository

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == username).one_or_none()
        if user is not None:
            if not args.reset_password:
                print(f"账户 {username} 已存在。如需重置密码请加 --reset-password", file=sys.stderr)
                return 1
            user.password_hash = hash_password(args.password)
            if not user.has_role(ROLE_ADMIN):
                db.add(UserRole(user_id=user.id, role=ROLE_ADMIN))
            AuditRepository(db).log(
                action="admin.password_reset",
                actor_name="create_admin.py",
                resource_type="user",
                resource_id=user.id,
                metadata={"username": username},
            )
            db.commit()
            print(f"已重置管理员账户 {username} 的密码")
            return 0

        user = User(
            username=username,
            display_name=args.name[:64],
            password_hash=hash_password(args.password),
            status="active",
        )
        user.roles = [UserRole(role=ROLE_ADMIN)]
        db.add(user)
        db.flush()
        AuditRepository(db).log(
            action="admin.created",
            actor_name="create_admin.py",
            resource_type="user",
            resource_id=user.id,
            metadata={"username": username},
        )
        db.commit()
        print(f"已创建管理员账户 {username}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
