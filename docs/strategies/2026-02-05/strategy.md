# 2026-02-05 策略思路与执行计划（美股）

## 目标
- 基于 2026-02-04 的结果提升通过率与可提交数量
- 扩大样本覆盖并引入 Regular，提升稳定性与多样性
- 保持相关性约束，避免同质化

## 昨日复盘结论（2026-02-04）
- 总数量 20，符合标准 0，成功提交 0
- 平均 Sharpe 0.061，平均 Fitness -0.000
- 结论：样本规模偏小且类型集中，覆盖不足

## 今日调整
- 生成数量提高到 50（增加模拟次数）
- 增加 Regular 类型以提高中等门槛通过率
- 保持市场与中性化设置不变，先用样本量改善结果

## 生成策略
- 类型与数量：Power Pool 20，Regular 20，ATOMs 10（共 50）
- 多样性：diversify=true
- 随机种子：seed=20260205

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
- `python main.py strategy -f docs/strategies/2026-02-05/strategy.yaml --templates docs/strategies/2026-02-05/templates.json`
- `python main.py strategy -f docs/strategies/2026-02-05/strategy.yaml --run --report docs/strategies/2026-02-05/report.txt`
