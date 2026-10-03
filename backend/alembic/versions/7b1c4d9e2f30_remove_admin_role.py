"""remove the admin product role

Revision ID: 7b1c4d9e2f30
Revises: 6e784eb94252
Create Date: 2026-10-03 21:05:00.000000

V3 产品定位变更：工 e 稳袋是上层银行 / 商户服务 App 中的一个业务模块，
平台级用户管理与系统运维由上层系统承担。因此本系统**不再存在 admin 业务身份**，
也没有用户管理后台、运行状态面板与 ``/api/v1/admin/*`` 接口。

本迁移只做**数据层**清理，不删除任何用户、不删除任何业务数据：

1. 同时拥有 ``merchant`` / ``family_member`` / ``consultant`` 与 ``admin`` 的账户：
   保留其业务身份，仅移除 ``admin`` 这一行。
2. **仅**拥有 ``admin`` 的账户：不物理删除用户（会连带删除其数据），
   改为移除 ``admin`` 角色并把账户置为 ``disabled``。
   代码层的 ``get_current_user`` 会拒绝 ``status != "active"`` 的账户登录，
   因此这些账户无法再进入系统，但审计与历史数据保持完整。
3. 写入一条 ``audit_logs`` 记录，说明本次角色移除的影响面。

降级（downgrade）**不回填 admin 角色**：这是产品口径变更，
把身份悄悄加回去比不加更危险。降级只保证迁移链连续。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "7b1c4d9e2f30"
down_revision: str | None = "6e784eb94252"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: 本次保留的业务身份
BUSINESS_ROLES = ("merchant", "family_member", "consultant")


def upgrade() -> None:
    connection = op.get_bind()

    # --- 1. 统计受影响账户（迁移前留档，写进审计日志） ---
    total_admin = connection.execute(
        sa.text("SELECT COUNT(*) FROM user_roles WHERE role = 'admin'")
    ).scalar_one()
    admin_only = connection.execute(
        sa.text(
            """
            SELECT COUNT(*) FROM user_roles a
            WHERE a.role = 'admin'
              AND NOT EXISTS (
                SELECT 1 FROM user_roles b
                WHERE b.user_id = a.user_id AND b.role != 'admin'
              )
            """
        )
    ).scalar_one()
    mixed = int(total_admin) - int(admin_only)

    # --- 2. admin-only 账户：先停用，再移除角色 ---
    #    顺序很重要：先停用可以保证即使中途失败，也不会留下「无角色但可登录」的账户。
    connection.execute(
        sa.text(
            """
            UPDATE users SET status = 'disabled'
            WHERE id IN (
                SELECT a.user_id FROM user_roles a
                WHERE a.role = 'admin'
                  AND NOT EXISTS (
                    SELECT 1 FROM user_roles b
                    WHERE b.user_id = a.user_id AND b.role != 'admin'
                  )
            )
            """
        )
    )

    # --- 3. 移除全部 admin 角色行 ---
    connection.execute(sa.text("DELETE FROM user_roles WHERE role = 'admin'"))

    # --- 4. 审计留痕 ---
    if int(total_admin) > 0:
        now = datetime.now(UTC)
        connection.execute(
            sa.text(
                """
                INSERT INTO audit_logs
                    (id, actor_id, actor_name, action, resource_type, resource_id,
                     merchant_id, metadata_json, ip_address, created_at)
                VALUES
                    (:id, NULL, 'alembic', 'identity.admin_role_removed', 'role', 'admin',
                     NULL, :metadata, NULL, :created_at)
                """
            ),
            {
                "id": uuid4().hex,
                "metadata": json.dumps(
                    {
                        "removed_role": "admin",
                        "affected": int(total_admin),
                        "kept_business_role": mixed,
                        "disabled_admin_only": int(admin_only),
                        "business_roles": list(BUSINESS_ROLES),
                    },
                    ensure_ascii=False,
                ),
                "created_at": now,
            },
        )


def downgrade() -> None:
    """不回填 admin 角色 —— 产品口径变更不可逆。

    这里只把「因本次迁移被停用的纯 admin 账户」恢复为 active 是无意义的：
    它们移除角色后已经没有任何业务身份。因此 downgrade 保持空操作，
    仅保证 alembic 链路可回退。
    """
    return None
