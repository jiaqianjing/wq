# 2026-02-04 策略思路与执行计划（美股）

## 目标
- 先覆盖高潜力（Power Pool）与原子（ATOMs）两个层级
- 通过更宽的因子覆盖提升有效率，同时控制相关性
- 以 USA 市场为主，先验证稳定性再扩展到其他市场

## 逻辑与假设
- 美股短周期动量与反转的可用性较高，ATOMs 可提供快速信号探索
- Power Pool 采用价量/波动/均值回归组合，提高中期稳定性
- 相关性控制优先，避免重复提交与同质化因子

## 生成策略
- 类型与数量
  - Power Pool: 10
  - ATOMs: 10
- 多样性
  - 启用分类分层采样（diversify=true）
- 随机种子
  - `seed=20260204`，确保可复现

## 模拟设置（AlphaConfig）
- Region: USA
- Universe: TOP3000
- Delay: 1
- Neutralization: SUBINDUSTRY
- Truncation: 0.08
- Pasteurization: ON
- Decay: 0

## 提交策略
- 若满足类型对应标准且 `is_submittable=true`，自动提交
- 提交前进行相关性检查（max_correlation=0.7）

## 执行步骤
1. 生成策略模板（Alpha 表达式列表）
2. 执行模拟并自动提交符合标准的 Alpha
3. 生成复盘文档

执行命令参考（先模板，后执行）：
- `python main.py strategy -f docs/strategies/2026-02-04/strategy.yaml --templates docs/strategies/2026-02-04/templates.json`
- `python main.py strategy -f docs/strategies/2026-02-04/strategy.yaml --run --report docs/strategies/2026-02-04/report.txt`
