# 2026-02-06 策略思路与执行计划（美股）

## 目标
- 聚焦 Power Pool 类型，优先验证高潜力模板
- 维持相关性约束，提升有效通过率
- 保持市场与中性化设置不变，减少变量干扰

## 背景（对比 2026-02-05）
- 2026-02-05 全量 50（Power Pool + Regular + ATOMs）通过率为 0
- 模拟过程中存在超时与代理连接波动，导致部分指标为 0

## 今日调整
- 只跑 Power Pool 50 次，减少类型混杂
- 利用更新后的超时重试机制与更稳模板策略

## 生成策略
- 类型与数量：Power Pool 50（共 50）
- 多样性：diversify=true
- 随机种子：seed=20260206

## 模拟设置（AlphaConfig）
- Region: USA
- Universe: TOP3000
- Delay: 1
- Neutralization: SUBINDUSTRY
- Truncation: 0.08
- Pasteurization: ON
- Decay: 0

## 提交策略
- 满足类型标准且 is_submittable=true 才提交
- 提交前进行相关性检查（max_correlation=0.7）

## 执行步骤
1. 生成策略模板（Alpha 表达式列表）
2. 执行模拟并自动提交符合标准的 Alpha
3. 生成复盘文档

执行命令参考（先模板，后执行）：
- `python main.py strategy -f docs/strategies/2026-02-06/strategy.yaml --templates docs/strategies/2026-02-06/templates.json`
- `python main.py strategy -f docs/strategies/2026-02-06/strategy.yaml --run --report docs/strategies/2026-02-06/report.txt`
