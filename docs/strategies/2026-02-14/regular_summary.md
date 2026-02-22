# 2026-02-14 USA Regular Alpha 研报驱动回测总结

## 目标
- 搜索最新研报并提炼可交易信息。
- 在 **USA / TOP3000 / Delay=1** 下生成策略并回测。
- 当天提交至少 5 个有效 Regular Alpha。

## 最新研报与价值信息（按时间）
1. **RFS, 2025-04-08**  
   *Short-Term Reversals and Longer-Term Momentum*  
   https://academic.oup.com/rfs/advance-article/doi/10.1093/rfs/hhaf024/8096590  
   价值点：短期反转与中期动量并非矛盾，可通过状态变量（如交易活跃度）切换。

2. **JEF, 2025-02-13**  
   *Maxing out short-term reversals in stock returns*  
   https://www.sciencedirect.com/science/article/pii/S0927539825000132  
   价值点：短窗反转信号在合理过滤后可显著增强。

3. **NBER Working Paper 34104, 2025-07**  
   *Complexity and Regime-Based Return Predictability*  
   https://www.nber.org/papers/w34104  
   价值点：市场状态变化下，单一信号鲁棒性下降，需要组合信号提升稳定性。

4. **NBER Working Paper 33037, 2024-09**  
   *Trading Volume and the Cross-Section of Stock Returns*  
   https://www.nber.org/papers/w33037  
   价值点：成交量相关变量可作为横截面收益预测与过滤器。

5. **arXiv, 2026-02-21**  
   *QuantaAlpha: A scalable alpha mining platform integrating machine learning and optimization for quantitative finance*  
   https://arxiv.org/abs/2602.15912  
   价值点：系统化 alpha 挖掘强调“信号池 + 筛选 + 反馈”的迭代流程。

6. **arXiv, 2026-02-16**  
   *AlphaPROBE: Explainable multi-factor alpha generation via machine learning and robust optimization in quantitative finance*  
   https://arxiv.org/abs/2602.11000  
   价值点：多因子组合优于孤立因子，且可解释性有助于稳定迭代。

## 信号映射与策略设计
基于上述研报，构建了三类候选：
- `reversal_fundamental`：短期价格反转 × 基本面质量（最有效）
- `reversal_volume`：短期反转 × 相对成交量（本轮未进入最终提交集）
- `day_night_fundamental`：日内/隔夜代理 × 基本面（本轮显著失效）

执行脚本：
- `/Users/jiaqianjing/workspace/quant/wq/scripts/usa_regular_research_tuning.py`

结果文件：
- `/Users/jiaqianjing/workspace/quant/wq/docs/strategies/2026-02-14/usa_regular_research_results.json`

## 回测与提交结果
- 回测尝试数：14
- 通过标准数（Regular）：5
- 成功提交数：**5 / 5（达标）**

提交 ID：
- `ZYwzQxLj`
- `d58zWmWK`
- `LLbzMnav`
- `QPrzvOQM`
- `le6GAGgl`

关键统计：
- `reversal_fundamental`：6 次回测，平均 Sharpe `2.29`，平均 Fitness `1.108`，提交 `5`
- `day_night_fundamental`：8 次回测，平均 Sharpe `-0.833`，平均 Fitness `-0.299`，提交 `0`

## 最有价值的信息（本轮验证后）
1. **反转 + 基本面质量组合** 在当前 USA 窗口表现最稳，显著优于单一技术或日内代理信号。  
2. **适度 decay + 行业/子行业中性化** 对控制换手与稳定 Fitness 很关键。  
3. **日内/隔夜代理在本窗口失效**，说明近期环境不支持该类 alpha 的直接迁移。

## 下一轮建议
1. 保留 `reversal_fundamental` 主线，扩展权重与窗口（如 `w in [0.3, 0.7]`，`delta in [1,3,5,10]`）。
2. 对通过但未提交的样本增加延迟重提队列（处理 pending checks）。
3. 将 `day_night_fundamental` 暂时降权，避免浪费并发配额。
