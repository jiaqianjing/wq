#!/usr/bin/env bash
# 快捷生成脚本（仅生成表达式，不模拟）
# 用法: ./scripts/generate.sh [type] [count] [output]
#   type:   atom|regular|power_pool|superalpha|101|all (默认 regular)
#   count:  数量 (默认 10)
#   output: 输出文件路径 (可选)

cd "$(dirname "$0")/.." || exit 1

TYPE="${1:-regular}"
COUNT="${2:-10}"
OUTPUT="$3"

if [ -n "$OUTPUT" ]; then
    python main.py generate -t "$TYPE" -c "$COUNT" -o "$OUTPUT"
else
    python main.py generate -t "$TYPE" -c "$COUNT"
fi
