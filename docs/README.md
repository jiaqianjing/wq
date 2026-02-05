# 批量生成与模拟策略笔记

这里记录每次批量生成与模拟的策略配置、执行结果与心得复盘，用来沉淀经验，指导下一轮生成策略的调整。

## 使用方式

- 每次批量生成与模拟完成后新增一条记录
- 明确本次策略的目标与约束（例如想提升 Sharpe、降低 Turnover）
- 记录可复用的参数组合与明确的失败原因

建议以日期为单位建立目录：
- `docs/strategies/YYYY-MM-DD/strategy.md`
- `docs/strategies/YYYY-MM-DD/strategy.yaml`
- `docs/strategies/YYYY-MM-DD/templates.json`
- `docs/strategies/YYYY-MM-DD/recap.md`

## 记录模板

- 日期：
- 主题：
- 目标与假设：
- 生成策略：
  - 类型与数量：
  - 关键模板/公式：
  - 参数搜索空间：
- 模拟设置：
  - Region/Universe/Delay：
  - 中性化/截尾/去极值：
  - 其他：
- 结果摘要：
  - 通过率：
  - 指标分布（Sharpe/Fitness/Turnover）：
  - 相关性情况：
- 心得与复盘：
- 下一轮调整：

## 经验与心得

- 指标优先级要明确，避免为了提升 Sharpe 牺牲稳健性
- 用最少的自由度解释结果，防止过拟合
- 先小规模验证，再进行批量模拟扩张
- 结果按子周期观察稳定性，不只看单点高值

## 待办清单

- 建立常用模板的“适用市场 + 风格”索引
- 设计统一的实验命名规范与归档策略
- 汇总失败样本的共同模式，减少重复试错
