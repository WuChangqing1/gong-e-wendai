"""Time helpers.

The database always stores naive UTC datetimes; the API always serialises them
with an explicit ``Z``/``+00:00`` suffix.  The UI renders them in
``Asia/Shanghai``.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

APP_TIMEZONE_NAME = "Asia/Shanghai"
APP_TIMEZONE = ZoneInfo(APP_TIMEZONE_NAME)

WINDOW_DAYS = 7


def utcnow() -> datetime:
    """Timezone-aware current UTC time."""
    return datetime.now(UTC)


def to_utc(value: datetime) -> datetime:
    """Normalise any datetime to timezone-aware UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def to_naive_utc(value: datetime) -> datetime:
    """Naive UTC datetime, suitable for SQLite storage."""
    return to_utc(value).replace(tzinfo=None)


def as_utc(value: datetime | None) -> datetime | None:
    """Read a value back from SQLite as aware UTC."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def local_now() -> datetime:
    return utcnow().astimezone(APP_TIMEZONE)


def to_local(value: datetime | None) -> datetime | None:
    aware = as_utc(value)
    return None if aware is None else aware.astimezone(APP_TIMEZONE)


def parse_datetime(raw: object) -> datetime:
    """Parse a user/CSV supplied timestamp into aware UTC.

    Accepted: ISO-8601 (with or without timezone), ``YYYY-MM-DD``,
    ``YYYY/MM/DD``, ``YYYY-MM-DD HH:MM[:SS]``, ``YYYYMMDDHHMMSS`` and
    ``YYYYMMDD``.  Naive inputs are interpreted as ``Asia/Shanghai`` because
    that is what a Chinese merchant means by a bare date/time.
    """
    if isinstance(raw, datetime):
        return to_utc(raw)
    if isinstance(raw, date):
        return datetime.combine(raw, time.min, tzinfo=APP_TIMEZONE).astimezone(UTC)

    text = str(raw or "").strip()
    if not text:
        raise ValueError("时间为空")
    text = text.replace("\u00a0", " ").strip()
    text = text.translate(str.maketrans("０１２３４５６７８９", "0123456789"))

    candidate = text.replace("/", "-").replace("年", "-").replace("月", "-").replace("日", "")
    candidate = candidate.replace("T", " ").strip()

    # try ISO first (handles offsets)
    try:
        parsed = datetime.fromisoformat(candidate)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=APP_TIMEZONE).astimezone(UTC)
        return parsed.astimezone(UTC)
    except ValueError:
        pass

    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%Y%m%d%H%M%S",
        "%Y%m%d%H%M",
        "%Y%m%d",
    ):
        try:
            parsed = datetime.strptime(candidate, fmt)
        except ValueError:
            continue
        return parsed.replace(tzinfo=APP_TIMEZONE).astimezone(UTC)

    raise ValueError(f"无法识别的时间格式：{text}")


def window_bounds(snapshot_at: datetime) -> tuple[datetime, datetime]:
    """Return ``(start, end)`` of the 7-day analysis window (inclusive start)."""
    start = to_utc(snapshot_at)
    return start, start + timedelta(days=WINDOW_DAYS)


def iso(value: datetime | None) -> str | None:
    """Serialise to an ISO-8601 string with explicit UTC offset."""
    aware = as_utc(value)
    return None if aware is None else aware.isoformat().replace("+00:00", "Z")
