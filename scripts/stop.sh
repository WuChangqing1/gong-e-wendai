#!/usr/bin/env bash
# stop 生产服务
exec "$(dirname "$0")/service.sh" stop "$@"