"""Alembic 杩愯鐜銆?
鏁版嵁搴撳湴鍧€浠庡簲鐢ㄩ厤缃紙鐜鍙橀噺 / .env锛夎鍙栵紝淇濊瘉杩佺Щ涓庡疄闄呰繍琛屼娇鐢ㄥ悓涓€涓簱銆?"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import settings  # noqa: E402
from app.models import Base  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", settings.resolved_database_url())
target_metadata = Base.metadata


def render_item(type_, obj, autogen_context):  # noqa: ANN001, ANN201
    """Render custom column types as plain SQLAlchemy types.

    ``UTCDateTime`` is a ``TypeDecorator`` over ``DateTime``; without this hook
    autogenerate would emit ``app.models.base.UTCDateTime()`` which requires the
    application package to be importable from the migration script.

    娉ㄦ剰锛氬繀椤荤簿纭尮閰?``UTCDateTime``銆傝嫢瀵?``DateTime`` 涓€姒傝繑鍥炴覆鏌撳瓧绗︿覆锛?    Alembic 鐨勭増鏈啓鍏ユ祦绋嬩細琚牬鍧忥紙``alembic_version`` 涓嶅啓鍏ョ増鏈锛夈€?    """
    if type_ != "type":
        return False
    if obj.__class__.__name__ != "UTCDateTime":
        return False
    autogen_context.imports.add("import sqlalchemy as sa")
    return "sa.DateTime()"


def run_migrations_offline() -> None:
    context.configure(
        url=settings.resolved_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        render_as_batch=True,
        render_item=render_item,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    settings.ensure_runtime_dirs()
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        # SQLite 闇€瑕?batch 妯″紡鎵嶈兘淇敼鍒?        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            render_as_batch=True,
            render_item=render_item,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
