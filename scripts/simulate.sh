#!/usr/bin/env bash
# 快捷模拟脚本
# 用法: ./scripts/simulate.sh [type] [count] [region]
#   type:   atom|regular|power_pool|superalpha|all (默认 regular)
#   count:  数量 (默认 10)
#   region: USA|CHN|EUR|JPN|TWN|KOR|GBR|DEU (默认 USA)

cd "$(dirname "$0")/.." || exit 1

TYPE="${1:-regular}"
COUNT="${2:-10}"
REGION="${3:-USA}"

python main.py simulate -t "$TYPE" -c "$COUNT" -r "$REGION"
