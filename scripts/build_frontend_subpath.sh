#!/usr/bin/env bash
# 工 e 稳袋 — 在服务器上构建前端并部署到父站点子路径
#
# 场景：应用挂在父站点子路径下（例如 https://<domain>/wendai/）。
# 此时静态资源必须使用命名空间基路径，否则会与父站点上其他应用的
# `/assets/` 冲突（实测会被对方后端返回 404）。
#
# 用法：
#   BASE_PATH=/wendai/ bash scripts/build_frontend_subpath.sh
#   BASE_PATH=/ bash scripts/build_frontend_subpath.sh     # 恢复根路径构建

set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BASE_PATH="${BASE_PATH:-/wendai/}"

echo "=== 构建前端 (base=${BASE_PATH}) ==="
cd "$APP_DIR/frontend"
npm config set registry https://registry.npmmirror.com >/dev/null 2>&1 || true
if [ ! -d node_modules ]; then
  npm ci --no-audit --no-fund
fi
VITE_BASE_PATH="$BASE_PATH" npm run build

echo
echo "=== 校验产物 ==="
grep -o 'src="[^"]*"' dist/index.html | head -3
echo "资源基路径应为：${BASE_PATH}assets/"

if [ "$BASE_PATH" != "/" ]; then
  if grep -q 'src="/assets/' dist/index.html; then
    echo "错误：仍然出现了根路径资源引用" >&2
    exit 1
  fi
  echo "OK：资源引用已使用命名空间路径"
fi

echo
echo "=== 提示 ==="
echo "前端产物为 $APP_DIR/frontend/dist，由 FastAPI 直接托管，无需重启服务。"
echo "如使用 Nginx 反代，确认父站点已把 ${BASE_PATH} 反代到应用端口。"
