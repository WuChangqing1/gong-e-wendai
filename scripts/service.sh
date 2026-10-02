#!/usr/bin/env bash
# 启动 / 停止 / 重启 工 e 稳袋 生产服务（无 systemd 环境的可靠方案）
#
# 用法：
#   bash scripts/start.sh
#   bash scripts/stop.sh
#   bash scripts/restart.sh
#   bash scripts/status.sh
#
# 依赖 .env.production 提供 APP_HOST / APP_PORT / DATABASE_URL 等配置。

set -euo pipefail

APP_NAME="gong-e-wendai"
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_DIR="${DATA_DIR:-$HOME/apps/$APP_NAME-data}"
PID_FILE="$DATA_DIR/app.pid"
LOG_FILE="$DATA_DIR/logs/app.out.log"

mkdir -p "$DATA_DIR/logs" "$DATA_DIR/uploads" "$DATA_DIR/backups"

if [ -f "$APP_DIR/.env.production" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$APP_DIR/.env.production"
  set +a
fi

HOST="${APP_HOST:-0.0.0.0}"
PORT="${APP_PORT:-18082}"

is_running() {
  [ -f "$PID_FILE" ] || return 1
  local pid
  pid="$(cat "$PID_FILE" 2>/dev/null || echo)"
  [ -n "$pid" ] || return 1
  kill -0 "$pid" 2>/dev/null
}

case "${1:-start}" in
  start)
    if is_running; then
      echo "服务已在运行（PID $(cat "$PID_FILE")）"
      exit 0
    fi
    echo "启动 $APP_NAME（$HOST:$PORT）"
    cd "$APP_DIR/backend"
    # SQLite 模式保持单 worker，避免并发写入冲突
    nohup "$APP_DIR/.venv/bin/python" -m uvicorn app.main:app \
      --host "$HOST" --port "$PORT" --workers 1 \
      >>"$LOG_FILE" 2>&1 &
    echo $! >"$PID_FILE"
    sleep 3
    if is_running; then
      echo "已启动，PID $(cat "$PID_FILE")，日志 $LOG_FILE"
    else
      echo "启动失败，请查看 $LOG_FILE" >&2
      exit 1
    fi
    ;;
  stop)
    if ! is_running; then
      echo "服务未在运行"
      rm -f "$PID_FILE"
      exit 0
    fi
    pid="$(cat "$PID_FILE")"
    echo "停止服务（PID $pid）"
    kill "$pid" 2>/dev/null || true
    for _ in $(seq 1 20); do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.5
    done
    if kill -0 "$pid" 2>/dev/null; then
      echo "优雅停止超时，强制结束" >&2
      kill -9 "$pid" 2>/dev/null || true
    fi
    rm -f "$PID_FILE"
    echo "已停止"
    ;;
  restart)
    "$0" stop
    "$0" start
    ;;
  status)
    if is_running; then
      echo "运行中（PID $(cat "$PID_FILE")）"
    else
      echo "未运行"
      exit 1
    fi
    ;;
  *)
    echo "用法：$0 {start|stop|restart|status}" >&2
    exit 2
    ;;
esac
