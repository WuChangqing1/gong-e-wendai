"""在目标环境创建一套固定的演示账号（幂等，可重复执行）。

**仅限演示 / 开发环境。** 本脚本会创建固定密码的账号，因此在生产运行时
（``APP_ENV=production``）会被直接拒绝。生产管理员必须通过
``scripts/provision_admin.py`` 创建，密码由脚本随机生成且不打印到终端。

用法::

    PYTHONPATH=backend python scripts/setup_demo_accounts.py

账号（密码统一 Wendai2025）：
* wangzhanggui  经营者（**不再**附带管理员角色）
* wangtaitai    家庭成员（已加入王家小院，邀请码 DEVDEMO1）
* zixunxiaoli   咨询人员

脚本不做任何删除操作；已存在的账号只补角色，不覆盖密码。
"""

from __future__ import annotations

import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.join(os.path.expanduser("~"), "apps/gong-e-wendai/backend"))


def _guard_environment() -> None:
    """生产环境直接拒绝执行：固定密码账号不得进入正式环境。"""
    env = (os.environ.get("APP_ENV") or "").strip().lower()
    if env in {"production", "prod"}:
        print(
            "拒绝在 production 环境生成固定密码的演示账号。"
            "如需管理员，请使用 scripts/provision_admin.py。",
            file=sys.stderr,
        )
        raise SystemExit(2)


_guard_environment()

from app.core.database import SessionLocal  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.models.household import Household, HouseholdMembership  # noqa: E402
from app.models.merchant import BusinessAccountSnapshot, MerchantProfile  # noqa: E402
from app.models.user import (  # noqa: E402
    ROLE_CONSULTANT,
    ROLE_FAMILY_MEMBER,
    ROLE_MERCHANT,
    User,
    UserRole,
)
from app.utils.timeutil import utcnow  # noqa: E402

PASSWORD = "Wendai2025"

ACCOUNTS = [
    # 经营者账号：**不再**附加 admin 角色。
    # 管理员必须是彼此独立的账户，不能因为某个账号是经营者就顺带获得管理权限。
    ("wangzhanggui", "王掌柜", [ROLE_MERCHANT]),
    ("wangtaitai", "王太太", [ROLE_FAMILY_MEMBER]),
    ("zixunxiaoli", "咨询小李", [ROLE_CONSULTANT]),
]


def ensure_user(db, username: str, display: str, roles: list[str]) -> tuple[User, bool]:
    user = db.query(User).filter(User.username == username).one_or_none()
    created = False
    if user is None:
        user = User(
            username=username,
            display_name=display,
            password_hash=hash_password(PASSWORD),
            status="active",
        )
        user.roles = [UserRole(role=role) for role in roles]
        db.add(user)
        db.flush()
        created = True
    else:
        existing = {item.role for item in user.roles}
        for role in roles:
            if role not in existing:
                db.add(UserRole(user_id=user.id, role=role))
    return user, created


def main() -> int:
    db = SessionLocal()
    try:
        result: list[str] = []
        merchant_user, _ = ensure_user(db, *ACCOUNTS[0])
        family_user, _ = ensure_user(db, *ACCOUNTS[1])
        consultant_user, _ = ensure_user(db, *ACCOUNTS[2])
        db.commit()

        profile = (
            db.query(MerchantProfile).filter(MerchantProfile.user_id == merchant_user.id).one_or_none()
        )
        if profile is None:
            profile = MerchantProfile(
                user_id=merchant_user.id,
                business_name="王记小吃店",
                business_type="小餐饮",
                contact_name="王掌柜",
                phone_optional="13800000000",
                default_buffer_amount_cents=60_000,
                account_name="经营收款账户",
                account_masked_no="8821",
            )
            db.add(profile)
            db.flush()
            db.add(
                BusinessAccountSnapshot(
                    merchant_id=profile.id,
                    opening_balance_cents=60_000,
                    pending_settlement_cents=0,
                    snapshot_at=utcnow().replace(second=0, microsecond=0),
                    source_type="manual",
                    created_by=merchant_user.id,
                    note="演示账号初始化",
                )
            )
            db.commit()
            result.append("已创建经营档案与资金时点（期初 600 元、留底 600 元）")
        else:
            result.append("经营档案已存在，未改动")

        household = (
            db.query(Household).filter(Household.merchant_id == profile.id).one_or_none()
        )
        if household is None:
            household = Household(
                name="王家小院",
                owner_id=merchant_user.id,
                merchant_id=profile.id,
                invite_code="DEVDEMO1",
                invite_code_active=True,
            )
            db.add(household)
            db.flush()
            db.add(
                HouseholdMembership(
                    household_id=household.id,
                    user_id=merchant_user.id,
                    role="owner",
                    status="active",
                    joined_at=utcnow(),
                    decided_by=merchant_user.id,
                    decided_at=utcnow(),
                )
            )
            result.append("已创建家庭「王家小院」，邀请码 DEVDEMO1")
        else:
            result.append(f"家庭已存在（邀请码 {household.invite_code}）")

        membership = (
            db.query(HouseholdMembership)
            .filter(
                HouseholdMembership.household_id == household.id,
                HouseholdMembership.user_id == family_user.id,
            )
            .one_or_none()
        )
        if membership is None:
            db.add(
                HouseholdMembership(
                    household_id=household.id,
                    user_id=family_user.id,
                    role="member",
                    relation_label="配偶",
                    status="active",
                    joined_at=utcnow(),
                    decided_by=merchant_user.id,
                    decided_at=utcnow(),
                )
            )
            result.append("已把家庭成员加入家庭（状态 active）")
        else:
            result.append("家庭成员关系已存在")

        db.commit()

        print("演示账号就绪：")
        print(f"  经营者+管理员  wangzhanggui  / {PASSWORD}")
        print(f"  家庭成员       wangtaitai    / {PASSWORD}")
        print(f"  咨询人员       zixunxiaoli   / {PASSWORD}")
        for line in result:
            print(f"  - {line}")
        print(f"  数据库 {os.environ.get('DATABASE_URL', '(来自 .env.production)')}")
        _ = consultant_user
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
