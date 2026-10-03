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
os.environ["LOG_LEVEL"] = "WARNING"

# ---------------------------------------------------------------------------
# 智能服务：测试环境必须与开发者的真实凭据完全隔离
#
# 本机可能配置了真实 GLM 环境变量。如果不显式清空，测试会：
#   1. 读到真实密钥，让「未配置」相关的断言随机失败
#   2. 在个别用例中真的向智谱发起请求
# 因此这里强制关闭并删除所有密钥变量，测试只使用注入的测试替身。
# ---------------------------------------------------------------------------
os.environ["AI_ENABLED"] = "false"
for _name in ("GLM", "GLM_API_KEY", "AI_API_KEY", "AI_BASE_URL", "AI_MODEL"):
    os.environ.pop(_name, None)
os.environ["AI_BASE_URL"] = ""
os.environ["AI_MODEL"] = ""

TEST_ROOT = _TEST_ROOT
TEST_DB_PATH = _TEST_DB
TEST_UPLOAD_DIR = _TEST_UPLOADS
