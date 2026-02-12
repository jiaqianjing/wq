#!/usr/bin/env bash
# ============================================================
# 每日自动执行脚本
# 功能: 按顺序执行 生成 → 模拟提交 → 提交待处理 → 生成报告
#
# 用法:
#   ./scripts/daily_run.sh              # 使用默认配置
#   ./scripts/daily_run.sh --region CHN # 指定区域
#   ./scripts/daily_run.sh --dry-run    # 仅模拟不提交
#
# 定时执行 (crontab -e):
#   0 9 * * * /Users/jiaqianjing/workspace/quant/wq/scripts/daily_run.sh >> /Users/jiaqianjing/workspace/quant/wq/logs/cron.log 2>&1
# ============================================================

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

# ---------- 默认参数 ----------
REGION="USA"
DRY_RUN=false
DATE=$(date +%Y-%m-%d)
LOG_DIR="$PROJECT_DIR/logs"
REPORT_DIR="$PROJECT_DIR/results/daily"

# ---------- 解析参数 ----------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --region)  REGION="$2"; shift 2 ;;
        --dry-run) DRY_RUN=true; shift ;;
        *) echo "未知参数: $1"; exit 1 ;;
    esac
done

# ---------- 初始化目录 ----------
mkdir -p "$LOG_DIR" "$REPORT_DIR"
LOG_FILE="$LOG_DIR/daily_${DATE}.log"

log() { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG_FILE"; }

log "========== 每日任务开始 =========="
log "区域: $REGION | 模式: $([ "$DRY_RUN" = true ] && echo '仅模拟' || echo '模拟+提交')"

# ---------- Step 1: 提交待处理 Alpha ----------
log "--- Step 1: 提交待处理 Alpha ---"
if [ "$DRY_RUN" = false ]; then
    python main.py pending 2>&1 | tee -a "$LOG_FILE" || log "⚠️ pending 执行出错，继续..."
else
    log "跳过 (dry-run 模式)"
fi

# ---------- Step 2: 各类型 Alpha 模拟/提交 ----------
TYPES=("atom" "regular" "power_pool")
COUNTS=(10 10 5)
CMD=$([ "$DRY_RUN" = true ] && echo "simulate" || echo "submit")

for i in "${!TYPES[@]}"; do
    TYPE="${TYPES[$i]}"
    COUNT="${COUNTS[$i]}"
    log "--- Step 2.${i}: ${CMD} ${TYPE} x${COUNT} (${REGION}) ---"
    python main.py "$CMD" -t "$TYPE" -c "$COUNT" -r "$REGION" 2>&1 | tee -a "$LOG_FILE" || log "⚠️ ${TYPE} 执行出错，继续..."
    sleep 5  # API 限流保护
done

# ---------- Step 3: 生成每日摘要 ----------
SUMMARY="$REPORT_DIR/summary_${DATE}.txt"
{
    echo "===== 每日执行摘要 ====="
    echo "日期: $DATE"
    echo "区域: $REGION"
    echo "模式: $([ "$DRY_RUN" = true ] && echo '仅模拟' || echo '模拟+提交')"
    echo ""
    echo "--- 执行日志 (最后 30 行) ---"
    tail -30 "$LOG_FILE"
} > "$SUMMARY"

log "摘要已保存: $SUMMARY"
log "========== 每日任务完成 =========="
