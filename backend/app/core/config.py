"""Application configuration.

All runtime configuration is read from environment variables (optionally via a
``.env`` file).  Nothing secret is ever hard-coded here.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
# backend/app/core/config.py -> backend/app/core -> backend/app -> backend
BACKEND_DIR = Path(__file__).resolve().parents[2]
PROJECT_ROOT = BACKEND_DIR.parent

DEFAULT_DEV_JWT_SECRET = "dev-only-change-me-openssl-rand-hex-32"


def _resolve_path(value: str, *, default_relative_to: Path) -> Path:
    """Resolve a possibly-relative path against the project root."""
    raw = Path(value).expanduser()
    if raw.is_absolute():
        return raw
    return (default_relative_to / raw).resolve()


class Settings(BaseSettings):
    """Runtime settings for the backend application."""

    model_config = SettingsConfigDict(
        env_file=(PROJECT_ROOT / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------------- app ----------------
    app_env: str = Field(default="development")
    app_name: str = Field(default="gong-e-wendai")
    app_host: str = Field(default="127.0.0.1")
    app_port: int = Field(default=8000)
    app_version: str = Field(default="1.0.0")

    # ---------------- database ----------------
    database_url: str = Field(default="sqlite:///./data/app.db")
    sqlite_busy_timeout_ms: int = Field(default=5000)

    # ---------------- auth ----------------
    jwt_secret: str = Field(default=DEFAULT_DEV_JWT_SECRET)
    jwt_algorithm: str = Field(default="HS256")
    jwt_access_expire_minutes: int = Field(default=30)
    jwt_refresh_expire_days: int = Field(default=14)
    cookie_secure: bool = Field(default=False)
    cookie_domain: str | None = Field(default=None)

    # ---------------- cors ----------------
    cors_origins: str = Field(default="http://localhost:5173,http://127.0.0.1:5173")

    # ---------------- uploads / logs ----------------
    upload_dir: str = Field(default="./uploads")
    max_upload_mb: int = Field(default=5)
    log_level: str = Field(default="INFO")
    log_dir: str = Field(default="./logs")

    # ---------------- ai provider ----------------
    ai_enabled: bool = Field(default=False)
    ai_api_key: str = Field(default="")
    ai_base_url: str = Field(default="")
    ai_model: str = Field(default="")
    ai_timeout_seconds: float = Field(default=30.0)

    # ------------------------------------------------------------------
    # Derived helpers
    # ------------------------------------------------------------------
    @field_validator("app_env")
    @classmethod
    def _normalise_env(cls, value: str) -> str:
        return value.strip().lower()

    @property
    def is_production(self) -> bool:
        return self.app_env in {"production", "prod"}

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def database_path(self) -> Path | None:
        """Absolute path of the SQLite file, or ``None`` for non-SQLite URLs."""
        url = self.database_url
        prefix = "sqlite:///"
        if not url.startswith(prefix):
            return None
        raw = url[len(prefix) :]
        return _resolve_path(raw, default_relative_to=PROJECT_ROOT)

    def resolved_database_url(self) -> str:
        path = self.database_path
        if path is None:
            return self.database_url
        return f"sqlite:///{path.as_posix()}"

    @property
    def upload_path(self) -> Path:
        return _resolve_path(self.upload_dir, default_relative_to=PROJECT_ROOT)

    @property
    def log_path(self) -> Path:
        return _resolve_path(self.log_dir, default_relative_to=PROJECT_ROOT)

    @property
    def frontend_dist(self) -> Path:
        return PROJECT_ROOT / "frontend" / "dist"

    @property
    def ai_configured(self) -> bool:
        return bool(self.ai_enabled and self.ai_api_key and self.ai_base_url and self.ai_model)

    def ensure_runtime_dirs(self) -> None:
        for path in (self.upload_path, self.log_path):
            path.mkdir(parents=True, exist_ok=True)
        db_path = self.database_path
        if db_path is not None:
            db_path.parent.mkdir(parents=True, exist_ok=True)

    def validate_production(self) -> list[str]:
        """Return a list of production-readiness problems (empty when fine)."""
        problems: list[str] = []
        if not self.is_production:
            return problems
        if self.jwt_secret == DEFAULT_DEV_JWT_SECRET or len(self.jwt_secret) < 32:
            problems.append("JWT_SECRET 必须是至少 32 字符的随机值（openssl rand -hex 32）")
        if "*" in self.cors_origin_list:
            problems.append("生产环境不允许 CORS_ORIGINS 为通配符 *")
        return problems


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    # Allow DATABASE_URL to be given relative to the current working directory
    # handled by ``_resolve_path``; nothing else to do here.
    os.environ.setdefault("APP_NAME", settings.app_name)
    return settings


settings = get_settings()
