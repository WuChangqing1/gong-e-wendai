#!/usr/bin/env bash
# start 生产服务
exec "$(dirname "$0")/service.sh" start "$@"