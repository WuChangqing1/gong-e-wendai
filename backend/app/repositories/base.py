"""通用数据访问助手。"""

from __future__ import annotations

import math

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.schemas.common import PageMeta


def paginate(db: Session, statement: Select, *, page: int, page_size: int) -> tuple[list, PageMeta]:
    """对 ``select`` 语句做统一分页，返回 ``(rows, meta)``。"""
    page = max(1, int(page))
    page_size = max(1, min(200, int(page_size)))

    total = db.scalar(select(func.count()).select_from(statement.order_by(None).subquery())) or 0
    rows = (
        db.execute(statement.limit(page_size).offset((page - 1) * page_size)).scalars().all()
    )
    total_pages = max(1, math.ceil(total / page_size)) if total else 0
    return list(rows), PageMeta(
        page=page, page_size=page_size, total=int(total), total_pages=total_pages
    )
