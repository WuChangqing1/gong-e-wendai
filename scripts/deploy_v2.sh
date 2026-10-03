#!/usr/bin/env bash
# 增强 v2 生产部署：备份 → 更新代码 → 依赖 → 迁移 → 构建 → 重启 → 验证。
#
# 设计原则：
#   * 先备份数据库与 Nginx 配置，再动任何东西
#   * 备份当前代码目录为 tar 包（.git 之外的服务器本地改动不会丢失）
#   * 先迁移后重启：新代码启动后第一次写入事项就会访问新表
#   * 不 kill 任何非本项目的进程，不改动其他 Nginx location
#   * 每一步失败立即退出，不做半成品状态
set -euo pipefail

APP_DIR="${APP_DIR:-$HOME/apps/gong-e-wendai}"
DATA_DIR="${DATA_DIR:-$HOME/apps/gong-e-wendai-data}"
PY="$APP_DIR/.venv/bin/python"
STAMP="$(date +%Y%m%d%H%M%S)"
BASE_PATH_VALUE="${BASE_PATH:-/wendai/}"

log() { printf '\n=== %s ===\n' "$1"; }

cd "$APP_DIR"

log "1/8 备份数据库"
"$PY" scripts/backup_db.py
ls -la "$DATA_DIR/backups/" | tail -3

log "2/8 备份 Nginx 配置（不改动其他 location）"
sudo cp /etc/nginx/conf.d/mysite.conf "/etc/nginx/conf.d/mysite.conf.bak-before-v2-$STAMP"
ls -la /etc/nginx/conf.d/ | grep mysite

log "3/8 备份当前代码目录"
tar czf "$HOME/apps/gong-e-wendai-pre-v2-$STAMP.tar.gz" \
  --exclude=.git --exclude=node_modules --exclude=.venv --exclude=dist \
  --exclude=__pycache__ --exclude='*.pyc' \
  -C "$HOME/apps" gong-e-wendai
ls -la "$HOME/apps/gong-e-wendai-pre-v2-$STAMP.tar.gz"

log "4/8 更新代码（保留数据目录）"
git fetch origin --quiet
git reset --hard origin/main
git log --oneline -1

log "5/8 安装 Python 依赖"
"$PY" -m pip install --quiet --upgrade pip
"$PY" -m pip install --quiet -r backend/requirements.txt
"$PY" -c 'import fastapi, sqlalchemy, pydantic, alembic, httpx; print("deps ok")'

log "6/8 数据库迁移（必须在重启前完成）"
cd "$APP_DIR/backend"
set -a; . "$APP_DIR/.env.production"; set +a
"$PY" -m alembic current
"$PY" -m alembic upgrade head
"$PY" -m alembic current
cd "$APP_DIR"

log "7/8 构建前端（子路径 $BASE_PATH_VALUE）"
cd "$APP_DIR/frontend"
export VITE_BASE_PATH="$BASE_PATH_VALUE"
npm run build
ls -la dist/assets/ | head -5
cd "$APP_DIR"

log "8/8 重启服务"
systemctl --user restart gong-e-wendai
sleep 5
systemctl --user is-active gong-e-wendai

log "健康检查"
curl -fsS http://127.0.0.1:18089/api/v1/health; echo
curl -s -o /dev/null -w "nginx-18088=%{http_code}\n" http://127.0.0.1:18088/api/v1/health
curl -s -o /dev/null -w "public-wendai=%{http_code}\n" https://ccqspace.site/wendai/
curl -s -o /dev/null -w "existing-18082=%{http_code}\n" http://127.0.0.1:18082/

log "完成"
echo "备份：$DATA_DIR/backups/ 与 $HOME/apps/gong-e-wendai-pre-v2-$STAMP.tar.gz"
