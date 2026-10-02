#!/usr/bin/env bash
# status 生产服务
exec "$(dirname "$0")/service.sh" status "$@"