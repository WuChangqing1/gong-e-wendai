#!/usr/bin/env bash
# restart 生产服务
exec "$(dirname "$0")/service.sh" restart "$@"