#!/usr/bin/env python
"""为生产环境开通一个独立的管理员账户。

设计约束
--------
* **不**接受命令行传入的密码：密码由脚本用 ``secrets`` 随机生成，
  避免出现在 shell 历史、进程列表或终端回显里。
* 密码只写入 ``--out`` 指定的文件（默认 ``~/admin-credentials.txt``），
  权限 ``0600``，且**不**打印到标准输出。
* 不会给经营者账号附加管理员角色：管理员必须是独立账户。
* 如果目标账户已经是管理员，只补角色不重置密码（除非显式 ``--reset-password``）。

用法::

    python scripts/provision_admin.py --username xitongguanli --name 系统管理员
    python scripts/provision_admin.py --username xitongguanli --reset-password
"""

from __future__ import annotations

import argparse
import os
import secrets
import string
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND_DIR))

ALPHABET = string.ascii_letters + string.digits


def generate_password(length: int = 20) -> str:
    """生成同时包含字母与数字的随机密码。"""
    while True:
        candidate = "".join(secrets.choice(ALPHABET) for _ in range(length))
        if any(char.isdigit() for char in candidate) and any(
            char.isalpha() for char in candidate
        ):
            return candidate


def main() -> int:
    parser = argparse.ArgumentParser(description="开通生产管理员账户（密码随机生成）")
    parser.add_argument("--username", required=True, help="账户名（4-32 位）")
    parser.add_argument("--name", default="系统管理员", help="显示称呼")
    parser.add_argument(
        "--out",
        default=str(Path.home() / "admin-credentials.txt"),
        help="凭据输出文件（0600，不会打印到终端）",
    )
    parser.add_argument(
        "--reset-password",
        action="store_true",
        help="账户已存在时重置密码（默认不重置）",
    )
    args = parser.parse_args()

    username = args.username.strip().lower()
    if not 4 <= len(username) <= 32:
        print("账户名长度必须为 4-32 位", file=sys.stderr)
        return 2

    from app.core.database import SessionLocal
    from app.core.security import hash_password
    from app.models.user import ROLE_ADMIN, User, UserRole
    from app.repositories.user_repo import AuditRepository
    from app.utils.timeutil import utcnow

    db = SessionLocal()
    outcome = ""
    password: str | None = None
    try:
        user = db.query(User).filter(User.username == username).one_or_none()
        if user is not None:
            already_admin = user.has_role(ROLE_ADMIN)
            if already_admin and not args.reset_password:
                outcome = f"账户 {username} 已经是管理员，未做任何修改"
            else:
                if args.reset_password:
                    password = generate_password()
                    user.password_hash = hash_password(password)
                if not already_admin:
                    db.add(UserRole(user_id=user.id, role=ROLE_ADMIN))
                AuditRepository(db).log(
                    action="admin.provisioned",
                    actor_name="provision_admin.py",
                    resource_type="user",
                    resource_id=user.id,
                    metadata={
                        "username": username,
                        "password_reset": bool(args.reset_password),
                    },
                )
                db.commit()
                outcome = f"已更新管理员账户 {username}"
        else:
            password = generate_password()
            user = User(
                username=username,
                display_name=args.name[:64],
                password_hash=hash_password(password),
                status="active",
                last_login_at=None,
            )
            user.roles = [UserRole(role=ROLE_ADMIN)]
            db.add(user)
            db.flush()
            AuditRepository(db).log(
                action="admin.provisioned",
                actor_name="provision_admin.py",
                resource_type="user",
                resource_id=user.id,
                metadata={"username": username, "created": True},
            )
            db.commit()
            outcome = f"已创建独立管理员账户 {username}"

        if password is not None:
            out_path = Path(args.out).expanduser()
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(
                "\n".join(
                    [
                        f"username: {username}",
                        f"password: {password}",
                        f"created_at: {utcnow().isoformat()}",
                        "",
                        "请立即登录并修改密码，然后删除本文件。",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            os.chmod(out_path, 0o600)

        # 只输出结论，绝不输出任何密码
        print(outcome)
        if password is not None:
            print(f"凭据已写入 {Path(args.out).expanduser()}（权限 600），未打印到终端")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
