#!/usr/bin/env bash
# 安全写入 GLM 凭据到 .env.production。
#
# 用法（密钥通过 stdin 传入，绝不走命令行参数）：
#   printf '%s' "$GLM_VALUE" | bash scripts/update_glm_env.sh
#
# 行为：
#   * 只替换/追加 GLM= 这一行，其他配置原样保留
#   * 文件权限强制为 600
#   * 只输出 "GLM credential configured"，**绝不**输出密钥或其片段
set -euo pipefail

ENV_FILE="${ENV_FILE:-$HOME/apps/gong-e-wendai/.env.production}"

if [ ! -f "$ENV_FILE" ]; then
  echo "找不到 $ENV_FILE" >&2
  exit 2
fi

# 从 stdin 读取密钥（去掉首尾空白与换行）
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

# 复制除 GLM= 之外的所有行
grep -v -E '^[[:space:]]*GLM[[:space:]]*=' "$ENV_FILE" > "$TMP" || true
printf 'GLM=%s\n' "$SECRET" >> "$TMP"

cat "$TMP" > "$ENV_FILE"
chmod 600 "$ENV_FILE"

# 只确认变量存在，不输出值
if grep -q -E '^GLM=.+$' "$ENV_FILE"; then
  echo "GLM credential configured"
else
  echo "写入后未找到 GLM 变量" >&2
  exit 1
fi
