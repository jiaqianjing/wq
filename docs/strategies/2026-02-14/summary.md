# 2026-02-14 Power Pool Alpha 研究与回测总结

## 目标
- 基于最新研报提炼有效信息，生成并回测 Power Pool Alpha。
- 目标在 2026-02-14 当天提交 5 个有效 Power Pool Alpha。

## 最新研报与可交易结论
1. RFS (2025-04-08): *Short-Term Reversals and Longer-Term Momentum*  
   链接: https://academic.oup.com/rfs/advance-article/doi/10.1093/rfs/hhaf024/8096590  
   启发: 52 周高点与换手率可区分短期反转和中期动量。

2. JEF (2025-02-13): *Maxing out short-term reversals in stock returns*  
   链接: https://www.sciencedirect.com/science/article/pii/S0927539825000132  
   启发: MAX 异象（近期极端上涨）与短期反转结合可增强信号。

3. NBER Working Paper 33861 (2025-05): *The Unintended Consequences of Rebalancing*  
   链接: https://www.nber.org/papers/w33861  
   启发: 再平衡带来的流动性冲击在短窗内可形成可交易偏差。

4. NBER Working Paper 34104 (2025-07): *Complexity and Regime-Based Return Predictability*  
   链接: https://www.nber.org/papers/w34104  
   启发: 波动率/复杂度可作为动量信号的择时门控。

5. arXiv (2026-01-21): *Cross-Market Alpha: Mining Universal Technical and Fundamental Signals*  
   链接: https://arxiv.org/abs/2601.13112  
   启发: 价量相关衰减与跨市场复用特征可作为稳健因子候选。

## 实验路线
### 路线 A: 研报驱动表达式搜索
- 脚本: `scripts/power_pool_research_run.py`
- 输出: `docs/strategies/2026-02-14/power_pool_research_results.json`
- 实际回测: 60 个
- 提交成功: 0

代表结果（Top）：
- `rebalancing_liquidity_shock`: Sharpe 1.57, Fitness 0.53, Turnover 0.628, Drawdown 0.118
- `alpha_101_var_5`: Sharpe 1.33, Fitness 0.58, Turnover 0.442, Drawdown 0.074
- `alpha_101_var_10`: Sharpe 1.31, Fitness 0.41, Turnover 0.492, Drawdown 0.077

### 路线 B: 历史高分 Alpha 回放（当前窗口重算）
- 脚本: `scripts/replay_top_alphas.py`
- 输出: `docs/strategies/2026-02-14/replay_top_alphas_results.json`
- 实际回测: 60 个（候选库 229 个）
- 提交成功: 0

代表结果（Top）：
- `rank(-(close-vwap)/ts_std_dev(close-vwap,20))`: Sharpe 2.09, Fitness 0.72, Turnover 0.817
- `rank(-(close-hlc3)/ts_std_dev(close-hlc3,20))`: Sharpe 2.09, Fitness 0.84, Turnover 0.820
- `group_rank((fnd6_intc)/cap, subindustry)`: Sharpe 1.44, Fitness 0.83, Turnover 0.023

### 路线 C: 高 Sharpe 表达式 decay 调参
- 输出: `docs/strategies/2026-02-14/decay_tuning_results.json`
- 有效样本: 8 个（其余受并发限制未创建）
- 提交成功: 0

关键观察：
- 提高 decay 可把 Turnover 从 ~0.82 压到 ~0.60 以下，但 Fitness 仍约 0.70，未达到 1.0。

### 路线 D: USA 定向组合微调（最终达标）
- 脚本: `scripts/usa_combo_tuning.py`
- 输出: `docs/strategies/2026-02-14/usa_combo_tuning_results.json`
- 搜索方式: 技术高 Sharpe 因子 + 基本面高 Fitness 因子线性组合/秩组合
- 回测并筛选后提交成功: **5**

达标并成功提交的组合：
1. `baseline_fund_acox_assets` + `decay=8` + `neutralization=INDUSTRY`  
2. `baseline_fund_drlt_revenue` + `decay=8` + `neutralization=INDUSTRY`  
3. `lin_tech_hlc3_z_fund_acdo_cap_w45` + `decay=6` + `neutralization=SUBINDUSTRY`  
4. `lin_tech_hlc3_z_fund_acdo_cap_w45` + `decay=8` + `neutralization=SUBINDUSTRY`  
5. `lin_tech_hlc3_z_fund_acdo_cap_w45` + `decay=10` + `neutralization=SUBINDUSTRY`

提交 alpha_id：
- `wpwG1xb2`
- `KPYz6Y8g`
- `qMkGz9rO`
- `88kJ6bEW`
- `78qmXEN1`

## 最终结果
- 目标提交数: 5
- 实际提交数: **5**（已达标）
- 区域: USA（按要求仅运行 USA）

## 今日交付
- 新增脚本:
  - `scripts/power_pool_research_run.py`
  - `scripts/replay_top_alphas.py`
  - `scripts/usa_combo_tuning.py`
- 改进客户端稳定性与可用性:
  - `wq_brain/client.py`
  - `wq_brain/alpha_submitter.py`
  - `main.py`
  - `smart_generate.py`
- 结果文件:
  - `docs/strategies/2026-02-14/power_pool_research_results.json`
  - `docs/strategies/2026-02-14/replay_top_alphas_results.json`
  - `docs/strategies/2026-02-14/decay_tuning_results.json`
  - `docs/strategies/2026-02-14/usa_combo_tuning_results.json`

## 下一步建议（优先级）
1. 以 `lin_tech_hlc3_z_fund_acdo_cap_w45` 为种子，继续扩展权重和窗口参数（优先收益稳定性）。  
2. 保持低并发批量（4-6）执行，减少 `CONCURRENT_SIMULATION_LIMIT_EXCEEDED`。  
3. 对“达标但提交失败”的候选做二次提交窗口管理（避开短时 429 / pending checks）。
