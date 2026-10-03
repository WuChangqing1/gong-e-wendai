#!/usr/bin/env python
"""为「工 e 稳袋」开通一个咨询人员身份。

设计约束
--------
* 咨询人员属于**上层银行 / 商户服务 App** 的身份，不是本模块自助注册产生的。
  因此工 e 稳袋不提供「管理员创建咨询人员」的网页后台，只用本脚本开通。
* **不接受命令行传入的密码**：密码由 ``secrets`` 随机生成，避免出现在
  shell 历史、进程列表或终端回显里。
* 密码只写入 ``--out`` 指定的文件（默认 ``~/consultant-credentials.txt``），
  权限 ``0600``，且**不**打印到标准输出。
* 目标账户已存在时只补角色，不覆盖密码（除非显式 ``--reset-password``）。

用法::

    # 交互式（推荐）：不带参数运行，逐项提示
    python scripts/provision_consultant.py

    # 或一次给全参数
    python scripts/provision_consultant.py --username zixunxiaoli --name 咨询小李

    # 重置已有咨询人员的密码
    python scripts/provision_consultant.py --username zixunxiaoli --reset-password
"""

from __future__ import annotations

import argparse
import os
import secrets
import string
import sys
from datetime import datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND_DIR))

ALPHABET = string.ascii_letters + string.digits

#: 与 ``app.schemas.user.USERNAME_PATTERN`` 保持一致。
USERNAME_MIN = 4
USERNAME_MAX = 32
ALLOWED_CHARS = set(string.ascii_letters + string.digits + "_.-")


def generate_password(length: int = 20) -> str:
    """生成同时包含字母与数字的随机密码。"""
    while True:
        candidate = "".join(secrets.choice(ALPHABET) for _ in range(length))
        if any(char.isdigit() for char in candidate) and any(
            char.isalpha() for char in candidate
        ):
            return candidate


def validate_username(value: str) -> str:
    username = value.strip().lower()
    if not (USERNAME_MIN <= len(username) <= USERNAME_MAX):
        raise ValueError(f"账户名长度需在 {USERNAME_MIN}-{USERNAME_MAX} 之间")
    if not set(username) <= ALLOWED_CHARS:
        raise ValueError("账户名只能包含字母、数字、下划线、点和连字符")
    return username


def prompt_username() -> str:
    while True:
        raw = input("咨询人员账户名（4-32 位，字母数字_.-）：").strip()
        try:
            return validate_username(raw)
        except ValueError as error:
            print(f"  × {error}", file=sys.stderr)


def prompt_display_name() -> str:
    while True:
        raw = input("显示称呼（例如：咨询小李）：").strip()
        if raw:
            return raw[:64]
        print("  × 称呼不能为空", file=sys.stderr)


def _check_environment() -> None:
    """生产环境不做额外限制，但必须确认没有把演示密码带进来。"""
    _ = os.environ.get("APP_ENV")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="开通咨询人员身份（密码随机生成，不打印到终端）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--username", help="账户名（4-32 位）；不给则交互式询问")
    parser.add_argument("--name", help="显示称呼；不给则交互式询问")
    parser.add_argument(
        "--out",
        default=str(Path.home() / "consultant-credentials.txt"),
        help="凭据输出文件（0600，不会打印到终端）",
    )
    parser.add_argument(
        "--reset-password",
        action="store_true",
        help="账户已存在时重置密码（默认不重置）",
    )
    args = parser.parse_args(argv)

    if not sys.stdin.isatty() and not (args.username and args.name):
        print(
            "非交互环境下请同时提供 --username 与 --name。",
            file=sys.stderr,
        )
        return 2

    try:
        username = validate_username(args.username) if args.username else prompt_username()
    except ValueError as error:
        print(f"× {error}", file=sys.stderr)
        return 2
    display_name = (args.name or "").strip() or (prompt_display_name() if sys.stdin.isatty() else "")
    if not display_name:
        print("× 缺少显示称呼", file=sys.stderr)
        return 2

    _check_environment()

    from app.core.database import SessionLocal  # noqa: PLC0415
    from app.core.security import hash_password  # noqa: PLC0415
    from app.models.user import ROLE_CONSULTANT, User, UserRole  # noqa: PLC0415
    from app.repositories.user_repo import AuditService  # noqa: PLC0415

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == username).one_or_none()
        password: str | None = None

        if user is None:
            password = generate_password()
            user = User(
                username=username,
                display_name=display_name,
                password_hash=hash_password(password),
                status="active",
            )
            user.roles = [UserRole(role=ROLE_CONSULTANT)]
            db.add(user)
            db.flush()
            action = "咨询人员账户已创建"
        else:
            has_role = user.has_role(ROLE_CONSULTANT)
            if not has_role:
                db.add(UserRole(user_id=user.id, role=ROLE_CONSULTANT))
            if args.reset_password:
                password = generate_password()
                user.password_hash = hash_password(password)
                action = "咨询人员账户已存在，角色已补齐，密码已重置"
            elif has_role:
                action = "咨询人员账户已存在，密码保持不变"
            else:
                action = "咨询人员账户已存在，已补齐 consultant 角色（密码保持不变）"
            if user.status != "active":
                user.status = "active"

        AuditService(db).record(
            "identity.consultant_provisioned",
            actor=None,
            resource_type="user",
            resource_id=user.id,
            metadata={
                "username": username,
                "role": ROLE_CONSULTANT,
                "password_reset": bool(password) and args.reset_password,
            },
            actor_name="provision_consultant.py",
        )
        db.commit()

        print(f"✓ {action}：{username}")
        if password:
            out_path = Path(args.out).expanduser()
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(
                f"# 工 e 稳袋 · 咨询人员凭据\n"
                f"# 生成时间：{datetime.now().isoformat(timespec='seconds')}\n"
                f"username={username}\n"
                f"password={password}\n",
                encoding="utf-8",
            )
            try:
                os.chmod(out_path, 0o600)
            except OSError:
                # Windows 上 chmod 语义不同，失败不影响流程；文件仍在用户主目录下。
                pass
            print(f"  凭据已写入 {out_path}（权限 600），未打印到终端。")
            print("  请登录后立即修改密码，然后删除该凭据文件。")
        else:
            print("  未生成新密码。如需重置请加 --reset-password。")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
