#!/usr/bin/env python
"""开发环境种子数据（**仅用于开发**）。

生产环境第一次启动只运行 Migration，不会自动生成任何账户或种子数据。
本脚本会显式创建以下账户（密码统一为 ``Wendai2025``）：

| 账户 | 角色 | 说明 |
| --- | --- | --- |
| `merchant_demo` | merchant | 经营主体，已登记资金时点与三笔未来事项 |
| `family_demo` | family_member | 家庭成员，已通过家庭审核 |
| `consultant_demo` | consultant | 咨询人员 |
| `admin_demo` | admin | 管理员 |

用法::

    conda activate gonghangcup
    cd backend
    python ../scripts/seed_dev_data.py
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND_DIR))

PASSWORD = "Wendai2025"


def main() -> int:
    from app.core.config import settings  # noqa: PLC0415

    if settings.is_production:
        print("拒绝在 production 环境生成种子数据", file=sys.stderr)
        return 2

    from app.core.database import SessionLocal  # noqa: PLC0415
    from app.core.security import hash_password  # noqa: PLC0415
    from app.models.household import Household, HouseholdMembership  # noqa: PLC0415
    from app.models.merchant import BusinessAccountSnapshot, MerchantProfile  # noqa: PLC0415
    from app.models.user import ROLE_ADMIN, ROLE_CONSULTANT, ROLE_FAMILY_MEMBER, ROLE_MERCHANT, User, UserRole  # noqa: PLC0415
    from app.schemas.cash_event import CashEventCreate  # noqa: PLC0415
    from app.services.event_service import CashEventService  # noqa: PLC0415
    from app.utils.timeutil import utcnow  # noqa: PLC0415

    db = SessionLocal()
    try:
        if db.query(User).filter(User.username == "merchant_demo").one_or_none() is not None:
            print("种子数据已存在，跳过。如需重建请先清空数据库。")
            return 0

        def make_user(username: str, display: str, roles: list[str]) -> User:
            user = User(
                username=username,
                display_name=display,
                password_hash=hash_password(PASSWORD),
                status="active",
            )
            user.roles = [UserRole(role=role) for role in roles]
            db.add(user)
            db.flush()
            return user

        merchant_user = make_user("merchant_demo", "王掌柜", [ROLE_MERCHANT])
        family_user = make_user("family_demo", "王太太", [ROLE_FAMILY_MEMBER])
        consultant_user = make_user("consultant_demo", "咨询小李", [ROLE_CONSULTANT])
        admin_user = make_user("admin_demo", "系统管理员", [ROLE_ADMIN])

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

        base = utcnow().replace(second=0, microsecond=0)
        db.add(
            BusinessAccountSnapshot(
                merchant_id=profile.id,
                opening_balance_cents=60_000,
                pending_settlement_cents=0,
                snapshot_at=base,
                source_type="manual",
                created_by=merchant_user.id,
                note="开发种子数据",
            )
        )

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
                joined_at=base,
                decided_by=merchant_user.id,
                decided_at=base,
            )
        )
        db.add(
            HouseholdMembership(
                household_id=household.id,
                user_id=family_user.id,
                role="member",
                relation_label="配偶",
                status="active",
                joined_at=base,
                decided_by=merchant_user.id,
                decided_at=base,
            )
        )
        db.commit()

        service = CashEventService(db)
        events = [
            ("DEV-SETTLE-0001", "商户结算款", "inflow", 220_000, 1, "settlement", "结算通知 8821"),
            ("DEV-PAY-0001", "供应商货款", "outflow", 100_000, 2, "supplier_payment", "采购合同 HT-2025-018"),
            ("DEV-SETTLE-0002", "平台结算款", "inflow", 70_000, 5, "settlement", "平台账单 2025-10"),
        ]
        for key, title, direction, cents, day, event_type, label in events:
            service.create_event(
                profile,
                CashEventCreate(
                    cash_key=key,
                    title=title,
                    direction=direction,
                    amount_cents=cents,
                    scheduled_at=base + timedelta(days=day, hours=2),
                    event_type=event_type,
                    state="scheduled",
                    source_label=label,
                ),
                actor=merchant_user,
                commit=False,
            )
        db.commit()

        print("开发种子数据已创建：")
        print(f"  经营主体   merchant_demo   / {PASSWORD}")
        print(f"  家庭成员   family_demo     / {PASSWORD}   （已加入家庭，邀请码 DEVDEMO1）")
        print(f"  咨询人员   consultant_demo / {PASSWORD}")
        print(f"  管理员     admin_demo      / {PASSWORD}")
        print(f"  数据库     {settings.database_path or settings.database_url}")
        _ = (consultant_user, admin_user)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
