#!/usr/bin/env bash
# 配置智能服务（智谱 GLM）到 .env.production。
#
# 用法：
#   printf '%s' "$GLM_VALUE" | bash scripts/setup_ai_env.sh   # 密钥走 stdin
#   bash scripts/setup_ai_env.sh --disable                    # 只关闭智能服务
#
# 行为：
#   * 密钥只从 stdin 读取，绝不作为命令行参数
#   * 非敏感变量（模型名、地址、超时）在这里显式写入，便于运维核对
#   * 文件权限强制 600
#   * 输出只确认变量存在，**绝不**输出密钥或其片段
set -euo pipefail

ENV_FILE="${ENV_FILE:-$HOME/apps/gong-e-wendai/.env.production}"

if [ ! -f "$ENV_FILE" ]; then
  echo "找不到 $ENV_FILE" >&2
  exit 2
fi

if [ "${1:-}" = "--disable" ]; then
  sed -i 's/^AI_ENABLED=.*/AI_ENABLED=false/' "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  grep -q '^AI_ENABLED=false$' "$ENV_FILE" && echo "AI_ENABLED=false"
  exit 0
fi

SECRET="$(cat)"
SECRET="${SECRET//$'\r'/}"
SECRET="$(printf '%s' "$SECRET" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"

if [ -z "$SECRET" ]; then
  echo "未收到凭据内容（stdin 为空）" >&2
  exit 2
fi
if [[ "$SECRET" == *$'\n'* ]]; then
  echo "凭据内容包含换行，已拒绝写入" >&2
  exit 2
fi

TMP="$(mktemp)"
chmod 600 "$TMP"
trap 'rm -f "$TMP"' EXIT

# 去掉旧的 AI_/GLM_ 配置行后重写
grep -v -E '^[[:space:]]*(AI_ENABLED|GLM|GLM_BASE_URL|GLM_TEXT_MODEL|GLM_VISION_MODEL|GLM_TIMEOUT_SECONDS|GLM_VISION_TIMEOUT_SECONDS|GLM_MAX_TOKENS|GLM_MAX_RETRIES|AI_API_KEY|AI_BASE_URL|AI_MODEL|AI_TIMEOUT_SECONDS)[[:space:]]*=' "$ENV_FILE" > "$TMP" || true

cat >> "$TMP" <<'CONF'

# ---------- 智能服务（智谱 GLM）----------
AI_ENABLED=true
CONF
printf 'GLM=%s\n' "$SECRET" >> "$TMP"
cat >> "$TMP" <<'CONF'
GLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4/
GLM_TEXT_MODEL=glm-4.5-air
GLM_VISION_MODEL=glm-4.6v
GLM_TIMEOUT_SECONDS=30
GLM_VISION_TIMEOUT_SECONDS=45
GLM_MAX_TOKENS=1024
GLM_MAX_RETRIES=2
CONF

cat "$TMP" > "$ENV_FILE"
chmod 600 "$ENV_FILE"

if grep -q -E '^GLM=.+$' "$ENV_FILE" && grep -q '^AI_ENABLED=true$' "$ENV_FILE"; then
  echo "GLM credential configured"
else
  echo "配置写入不完整" >&2
  exit 1
fi
