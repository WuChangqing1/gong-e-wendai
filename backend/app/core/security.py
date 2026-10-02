"""Password hashing, token issuing and cookie helpers."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta
from typing import Any, Literal

import jwt
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher

from app.core.config import settings
from app.core.errors import Unauthenticated
from app.utils.timeutil import to_utc, utcnow

_password_hash = PasswordHash((Argon2Hasher(),))

ACCESS_COOKIE_NAME = "gew_access"
REFRESH_COOKIE_NAME = "gew_refresh"
CSRF_HEADER = "x-requested-with"
CSRF_HEADER_VALUE = "XMLHttpRequest"

TokenType = Literal["access", "refresh"]


# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------
def hash_password(password: str) -> str:
    """Argon2 hash.  Plaintext passwords are never stored or logged."""
    return _password_hash.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        return _password_hash.verify(password, hashed)
    except Exception:
        return False


def needs_rehash(hashed: str) -> bool:
    try:
        return _password_hash.verify_and_update("", hashed)[1] is not None
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------
def create_token(
    subject: str,
    token_type: TokenType,
    *,
    expires_delta: timedelta | None = None,
    extra: dict[str, Any] | None = None,
) -> tuple[str, datetime]:
    """Return ``(encoded_jwt, expires_at_utc)``."""
    if expires_delta is None:
        expires_delta = (
            timedelta(minutes=settings.jwt_access_expire_minutes)
            if token_type == "access"
            else timedelta(days=settings.jwt_refresh_expire_days)
        )
    now = utcnow()
    expires_at = now + expires_delta
    payload: dict[str, Any] = {
        "sub": str(subject),
        "type": token_type,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
        "jti": secrets.token_urlsafe(16),
    }
    if extra:
        payload.update(extra)
    encoded = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return encoded, expires_at


def decode_token(token: str, expected_type: TokenType | None = None) -> dict[str, Any]:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["exp", "sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise Unauthenticated("登录状态已过期，请重新登录") from exc
    except jwt.PyJWTError as exc:
        raise Unauthenticated("登录凭证无效") from exc

    if expected_type is not None and payload.get("type") != expected_type:
        raise Unauthenticated("登录凭证类型不正确")
    return payload


def token_fingerprint(token: str) -> str:
    """Stable, non-reversible identifier for a refresh token (for revocation)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def constant_time_equals(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


# ---------------------------------------------------------------------------
# Cookies
# ---------------------------------------------------------------------------
def set_auth_cookies(response, access_token: str, refresh_token: str) -> None:  # noqa: ANN001
    """Attach HttpOnly auth cookies to a response."""
    common: dict[str, Any] = {
        "httponly": True,
        "samesite": "lax",
        "secure": settings.cookie_secure,
        "path": "/",
    }
    if settings.cookie_domain:
        common["domain"] = settings.cookie_domain
    response.set_cookie(
        ACCESS_COOKIE_NAME,
        access_token,
        max_age=int(timedelta(minutes=settings.jwt_access_expire_minutes).total_seconds()),
        **common,
    )
    response.set_cookie(
        REFRESH_COOKIE_NAME,
        refresh_token,
        max_age=int(timedelta(days=settings.jwt_refresh_expire_days).total_seconds()),
        **common,
    )


def clear_auth_cookies(response) -> None:  # noqa: ANN001
    for name in (ACCESS_COOKIE_NAME, REFRESH_COOKIE_NAME):
        response.delete_cookie(name, path="/", domain=settings.cookie_domain or None)


def generate_invite_code(length: int = 8) -> str:
    """Human-friendly, unambiguous invite code."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def utc_from_timestamp(value: int | float) -> datetime:
    return to_utc(datetime.fromtimestamp(value, tz=None))
