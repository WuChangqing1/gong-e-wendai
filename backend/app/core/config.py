"""Application configuration.

All runtime configuration is read from environment variables (optionally via a
``.env`` file).  Nothing secret is ever hard-coded here.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
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
    """Runtime settings for the backend application.

    配置优先级（高 → 低）：

    1. 真实进程环境变量（systemd ``EnvironmentFile`` / ``export`` / docker ``-e``）
    2. ``.env`` 文件（本地开发）
    3. 字段默认值

    生产环境（``APP_ENV=production``）**不读取** ``.env`` 文件，
    避免误把开发配置带进生产；此时所有配置都必须由进程环境变量提供。
    """

    model_config = SettingsConfigDict(
        # 生产环境忽略 .env 文件，只使用真实环境变量
        env_file=None if os.environ.get("APP_ENV", "").lower() in {"production", "prod"} else (
            PROJECT_ROOT / ".env",
            BACKEND_DIR / ".env",
        ),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        # 允许用字段名（GLM_API_KEY）或别名（GLM）配置密钥
        populate_by_name=True,
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
    #
    # 正式接入智谱 GLM。密钥一律用 SecretStr 保存：
    # 它不会出现在 repr、日志、Traceback 或 API 响应里。
    # 兼容旧的 AI_* 变量，但 GLM_* 优先。
    ai_enabled: bool = Field(default=False)

    #: 智谱 API Key（最高敏感信息）。禁止打印、禁止写日志、禁止进 Git。
    glm_api_key: SecretStr | None = Field(default=None, alias="GLM")
    glm_base_url: str = Field(default="https://open.bigmodel.cn/api/paas/v4/")
    glm_text_model: str = Field(default="glm-4.5-air")
    glm_vision_model: str = Field(default="glm-4.6v")
    glm_timeout_seconds: float = Field(default=30.0)
    glm_vision_timeout_seconds: float = Field(default=45.0)
    #: 单次请求最多消耗的 token，避免误用大额度
    glm_max_tokens: int = Field(default=1024)
    #: 失败重试次数（只对 429/5xx 生效，指数退避）
    glm_max_retries: int = Field(default=2)

    #: 兼容旧配置（GLM_* 优先）
    ai_api_key: SecretStr | None = Field(default=None)
    ai_base_url: str = Field(default="")
    ai_model: str = Field(default="")
    ai_timeout_seconds: float = Field(default=30.0)

    #: 图片上传限制：只允许单张，最大 5MB
    ai_vision_max_bytes: int = Field(default=5 * 1024 * 1024)
    ai_vision_max_images: int = Field(default=1)
    #: 文本提取的输入长度上限
    ai_text_max_chars: int = Field(default=4000)

    model_config = SettingsConfigDict(
        # 生产环境忽略 .env 文件，只使用真实环境变量
        env_file=None if os.environ.get("APP_ENV", "").lower() in {"production", "prod"} else (
            PROJECT_ROOT / ".env",
            BACKEND_DIR / ".env",
        ),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

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
        return bool(self.ai_enabled and self.resolved_ai_api_key and self.resolved_ai_base_url)

    # ---------------- GLM 解析（GLM_* 优先，兼容 AI_*） ----------------
    @staticmethod
    def _secret_text(value: object) -> str:
        """读取密钥明文；同时兼容 SecretStr 与测试里直接赋的普通字符串。"""
        if value is None:
            return ""
        getter = getattr(value, "get_secret_value", None)
        if callable(getter):
            return str(getter()).strip()
        return str(value).strip()

    @property
    def resolved_ai_api_key(self) -> str:
        """实际使用的密钥明文。**只在发起请求时读取，禁止打印或记录。**"""
        for candidate in (self.glm_api_key, self.ai_api_key):
            value = self._secret_text(candidate)
            if value:
                return value
        return ""

    @property
    def resolved_ai_base_url(self) -> str:
        return (self._secret_text(self.glm_base_url) or self._secret_text(self.ai_base_url) or "").strip()

    @property
    def text_model(self) -> str:
        return (self._secret_text(self.glm_text_model) or self._secret_text(self.ai_model) or "").strip()

    @property
    def vision_model(self) -> str:
        return self._secret_text(self.glm_vision_model)

    @property
    def vision_configured(self) -> bool:
        return bool(self.ai_enabled and self.resolved_ai_api_key and self.vision_model)

    def ai_status_public(self) -> dict[str, object]:
        """对外暴露的智能服务状态。

        只包含布尔值与模型名：**绝不**包含密钥、密钥前缀、后缀或长度。
        """
        return {
            "enabled": bool(self.ai_enabled),
            "configured": self.ai_configured,
            "available": self.ai_configured,
            "provider": "zhipu-glm" if self.glm_api_key is not None else "openai-compatible",
            "text_model": self.text_model or None,
            "vision_model": self.vision_model or None,
        }

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
