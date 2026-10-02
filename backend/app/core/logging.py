"""Logging configuration.

Logs go to stdout (captured by systemd / nohup) and, when ``LOG_DIR`` is
writable, to a rotating file.  Secrets never appear in log records.
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from app.core.config import settings

_CONFIGURED = False
_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"

# Attribute names that must never be written to logs.
REDACTED_KEYS = {
    "password",
    "new_password",
    "old_password",
    "current_password",
    "token",
    "access_token",
    "refresh_token",
    "jwt",
    "jwt_secret",
    "api_key",
    "authorization",
    "cookie",
    "secret",
}


def configure_logging() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return

    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)

    formatter = logging.Formatter(_FORMAT)

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(formatter)
    stream.setLevel(level)
    root.addHandler(stream)

    if settings.log_path:
        try:
            settings.log_path.mkdir(parents=True, exist_ok=True)
            file_handler = RotatingFileHandler(
                settings.log_path / "app.log",
                maxBytes=5 * 1024 * 1024,
                backupCount=5,
                encoding="utf-8",
            )
            file_handler.setFormatter(formatter)
            file_handler.setLevel(level)
            root.addHandler(file_handler)
        except OSError:
            # Read-only deployment directory: stdout is enough.
            pass

    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    configure_logging()
    return logging.getLogger(name)


def redact(payload: dict) -> dict:
    """Return a copy of ``payload`` with secret-looking keys masked."""
    safe: dict = {}
    for key, value in payload.items():
        if key.lower() in REDACTED_KEYS:
            safe[key] = "***"
        elif isinstance(value, dict):
            safe[key] = redact(value)
        else:
            safe[key] = value
    return safe
