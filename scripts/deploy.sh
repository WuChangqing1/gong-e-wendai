#!/usr/bin/env bash
# 工 e 稳袋 — 服务器部署脚本
#
# 适用环境：Ubuntu（用户 ubuntu，已配置 GitHub SSH 访问私有仓库）
# 代码目录与持久数据目录分离，git pull 不会覆盖数据库。
#
# 用法：
#   bash scripts/deploy.sh            # 完整部署（拉取 / 依赖 / 构建 / 迁移 / 重启）
#   bash scripts/deploy.sh --no-pull  # 跳过 git pull（本地已同步）
#   bash scripts/deploy.sh --check    # 只做环境检查

set -euo pipefail

APP_NAME="gong-e-wendai"
REPO_URL="${REPO_URL:-git@github.com:WuChangqing1/gong-e-wendai.git}"
APP_DIR="${APP_DIR:-$HOME/apps/$APP_NAME}"
DATA_DIR="${DATA_DIR:-$HOME/apps/$APP_NAME-data}"
PORT="${PORT:-18088}"

DO_PULL=1
CHECK_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --no-pull) DO_PULL=0 ;;
    --check) CHECK_ONLY=1 ;;
    *) echo "未知参数：$arg" >&2; exit 2 ;;
  esac
done

log() { printf '\n=== %s ===\n' "$1"; }

log "环境检查"
whoami
hostname
uname -srm
echo "HOME       : $HOME"
echo "代码目录   : $APP_DIR"
echo "数据目录   : $DATA_DIR"
echo "目标端口   : $PORT"

echo "--- 磁盘 ---"; df -h "$HOME" || true
echo "--- 内存 ---"; free -h || true
echo "--- 版本 ---"
python3 --version || true
node --version || true
npm --version || true
git --version || true

echo "--- 端口占用 ---"
if command -v ss >/dev/null 2>&1; then
  if ss -lnt | grep -q ":$PORT "; then
    echo "端口 $PORT 已被占用："
    ss -lntp | grep ":$PORT " || true
    echo "请先处理占用，或改用其他端口后重试。" >&2
    exit 3
  fi
  echo "端口 $PORT 未被占用"
fi

if [ "$CHECK_ONLY" -eq 1 ]; then
  log "仅检查模式，结束"
  exit 0
fi

log "准备目录"
mkdir -p "$APP_DIR" "$DATA_DIR" "$DATA_DIR/uploads" "$DATA_DIR/logs" "$DATA_DIR/backups"

if [ -d "$APP_DIR/.git" ]; then
  if [ "$DO_PULL" -eq 1 ]; then
    log "拉取代码"
    git -C "$APP_DIR" fetch --all --prune
    git -C "$APP_DIR" reset --hard origin/main
  fi
else
  log "克隆私有仓库"
  rmdir "$APP_DIR" 2>/dev/null || true
  git clone "$REPO_URL" "$APP_DIR"
fi

cd "$APP_DIR"

log "Python 环境"
if [ ! -d "$APP_DIR/.venv" ]; then
  python3 -m venv "$APP_DIR/.venv"
fi
"$APP_DIR/.venv/bin/pip" install --upgrade pip wheel
"$APP_DIR/.venv/bin/pip" install -r backend/requirements.txt

log "前端依赖与构建"
cd "$APP_DIR/frontend"
npm ci
npm run build
cd "$APP_DIR"

log "生产环境变量"
if [ ! -f "$APP_DIR/.env.production" ]; then
  echo "缺少 $APP_DIR/.env.production" >&2
  echo "请先创建（参考 .env.example），至少设置：" >&2
  echo "  APP_ENV=production" >&2
  echo "  DATABASE_URL=sqlite:///$DATA_DIR/app.db" >&2
  echo "  JWT_SECRET=\$(openssl rand -hex 32)" >&2
  exit 4
fi
chmod 600 "$APP_DIR/.env.production"

log "数据库迁移"
set -a
# shellcheck disable=SC1090
. "$APP_DIR/.env.production"
set +a
cd "$APP_DIR/backend"
"$APP_DIR/.venv/bin/alembic" upgrade head
"$APP_DIR/.venv/bin/alembic" check || true

log "重启服务"
cd "$APP_DIR"
if [ -x "$APP_DIR/scripts/restart.sh" ]; then
  bash "$APP_DIR/scripts/restart.sh"
else
  echo "未找到 scripts/restart.sh，请使用 systemd 或手动启动：" >&2
  echo "  uvicorn app.main:app --host 127.0.0.1 --port 18089 --workers 1" >&2
fi

log "健康检查"
sleep 3
curl -fsS "http://127.0.0.1:$PORT/api/v1/health" || echo "健康检查失败" >&2
echo
curl -fsS -o /dev/null -w 'index.html 状态码: %{http_code}\n' "http://127.0.0.1:$PORT/" || true
if command -v ss >/dev/null 2>&1; then
  ss -lnt | grep ":$PORT " || true
fi

log "部署完成"
