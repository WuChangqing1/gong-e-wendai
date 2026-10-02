"""测试用环境变量设置。

必须在导入 ``app.*`` 之前完成配置，避免测试污染开发数据库。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_TEST_ROOT = Path(tempfile.mkdtemp(prefix="gew-test-"))
_TEST_DB = _TEST_ROOT / "test.db"
_TEST_UPLOADS = _TEST_ROOT / "uploads"
_TEST_LOGS = _TEST_ROOT / "logs"

os.environ.setdefault("APP_ENV", "test")
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB.as_posix()}"
os.environ["UPLOAD_DIR"] = str(_TEST_UPLOADS)
os.environ["LOG_DIR"] = str(_TEST_LOGS)
os.environ["JWT_SECRET"] = "test-secret-key-for-pytest-only-0123456789abcdef"
os.environ["JWT_ACCESS_EXPIRE_MINUTES"] = "30"
os.environ["JWT_REFRESH_EXPIRE_DAYS"] = "14"
os.environ["COOKIE_SECURE"] = "false"
os.environ["CORS_ORIGINS"] = ""
os.environ["AI_ENABLED"] = "false"
os.environ["AI_API_KEY"] = ""
os.environ["AI_BASE_URL"] = ""
os.environ["AI_MODEL"] = ""
os.environ["LOG_LEVEL"] = "WARNING"

TEST_ROOT = _TEST_ROOT
TEST_DB_PATH = _TEST_DB
TEST_UPLOAD_DIR = _TEST_UPLOADS
