# 2026-02-25 USA Regular Alpha 总结（研报驱动）

## 目标
- 基于最新研报提炼可交易信息。
- 生成并回测 Regular Alpha。
- 在今天（2026-02-25）提交 5 个有效 Regular Alpha。

## 最新研报扫描与价值信息
1. RFS（2025-08-07）  
   [International Return Predictability in the Era of AI](https://academic.oup.com/rfs/advance-article/doi/10.1093/rfs/hhaf057/8254093)  
   价值信息：跨市场可预测性存在，但稳定 alpha 需要结构化组合，而不是单信号。

2. Finance Research Letters（2025-03）  
   [Stock return anomalies in the modern era: model confidence sets and quantile effects](https://www.sciencedirect.com/science/article/pii/S1544612325001082)  
   价值信息：异常收益在新时期并未完全消失，但应做“置信筛选+分位过滤”。

3. Journal of Empirical Finance（2025-09）  
   [Maxing out short-term reversals in weekly stock returns](https://www.sciencedirect.com/science/article/pii/S0927539825000533)  
   价值信息：短期反转信号在合理约束下仍可显著有效。

4. NBER Working Paper 34420（2025-06）  
   [Information, Trading, and Momentum](https://www.nber.org/papers/w34420)  
   价值信息：动量/反转效应受信息扩散与交易结构影响，组合过滤可提升稳健性。

5. NBER Working Paper 33037（2024-09）  
   [Trading Volume and the Cross-Section of Stock Returns](https://www.nber.org/papers/w33037)  
   价值信息：成交量变量适合作为横截面收益的过滤和增强维度。

6. arXiv（2026-02-16）  
   [AlphaPROBE: Explainable multi-factor alpha generation via machine learning and robust optimization in quantitative finance](https://arxiv.org/abs/2602.11000)  
   价值信息：多因子可解释组合优于孤立因子，且更适配自动化迭代框架。

## 因子映射与策略
执行脚本：`/Users/jiaqianjing/workspace/quant/wq/scripts/usa_regular_research_tuning.py`

主策略簇：
- `reversal_fundamental`（短期反转 + 基本面质量）
- 参数网格：`decay in {4,6,8}`, `neutralization in {SUBINDUSTRY, INDUSTRY}`
- Regular 门槛：`Sharpe>=1.25`, `Fitness>=0.7`, `0.01<=Turnover<=0.7`, `Drawdown<=0.1`

本轮关键调整：
- 将候选顺序改为 `reversal_fundamental` 优先，降低低命中主题的预算消耗。

## 回测与提交结果
结果文件：`/Users/jiaqianjing/workspace/quant/wq/docs/strategies/2026-02-25/usa_regular_research_results.json`

统计：
- 回测尝试：18
- 通过阈值：18
- 提交成功：**5 / 5（达标）**

提交成功的 Regular Alpha：
1. `omA9LXw6`  
   `lin_rev_fund`, decay=8, INDUSTRY  
   Sharpe=2.08, Fitness=1.34, Turnover=0.241, Drawdown=0.038
2. `XgPYbxA8`  
   `lin_rev_fund`, decay=4, SUBINDUSTRY  
   Sharpe=2.26, Fitness=1.19, Turnover=0.364, Drawdown=0.040
3. `zqQJ8EAV`  
   `lin_rev_fund`, decay=6, SUBINDUSTRY  
   Sharpe=2.16, Fitness=1.24, Turnover=0.283, Drawdown=0.032
4. `3q3nVoK6`  
   `lin_rev_fund`, decay=4, SUBINDUSTRY  
   Sharpe=2.02, Fitness=1.02, Turnover=0.410, Drawdown=0.055
5. `vRxJ2pR3`  
   `lin_rev_fund`, decay=8, INDUSTRY  
   Sharpe=1.86, Fitness=1.17, Turnover=0.276, Drawdown=0.048

## 结论
- 今天目标已完成：提交 5 个有效 Regular Alpha。
- 当前窗口内，`reversal_fundamental` 显著优于其他主题。
- 下一轮建议固定该主题为主线，再做权重与窗口微调。
