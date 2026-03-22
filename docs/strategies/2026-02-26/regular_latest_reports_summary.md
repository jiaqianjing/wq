# USA Regular Alpha Run Summary (2026-02-26)

## 1) 最新研报检索与可用信息提炼

本轮优先参考 2025-2026 的可获取研究：

1. [RFS 2025: Short-Term Return Reversal](https://academic.oup.com/rfs/article-abstract/38/7/1984/7968526)（2025 年）
2. [NBER w34104: Trading Volume Alpha](https://www.nber.org/papers/w34104)（2025 年）
3. [NBER w33861: Capital Rebalancing and Equity Return Predictability](https://www.nber.org/papers/w33861)（2025 年）
4. [arXiv 2601.13112: Cross-Market Alpha191 Reproduction](https://arxiv.org/abs/2601.13112)（2026-01）
5. [arXiv 2509.06702: Learning Horizon-Aware Alpha Factors](https://arxiv.org/abs/2509.06702)（2025-09）
6. [Finance Research Letters 2025: Intraday and overnight anomalies](https://www.sciencedirect.com/science/article/pii/S1544612325001523)（2025-10）

提炼出的可执行信息：

- 短周期反转仍有效，但需要结合“成交量状态/波动状态”做条件化，否则同质化严重。
- 仅做价格动量或价量相关项，容易命中高 `PROD_CORRELATION`。
- `D1 USA` 下，基本面锚定（subindustry group_rank）有助于提升稳健性，但会带来 `SELF_CORRELATION` 风险。
- 同一类模板换参数（只改窗口/权重）不足以规避生产池相关性，需要切换信号家族。

## 2) 本轮生成的策略族（Regular, USA）

已落地到脚本：

- `/Users/jiaqianjing/workspace/quant/wq/scripts/usa_regular_latest_reports_run.py`

核心策略族：

- `rev_max`（反转 + 排位增强）
- `vol_pred`（成交量预测项）
- `rebalance`（再平衡压力代理）
- `day_night`（隔夜/日内结构）
- `corr_decay`（价量秩相关衰减）
- `horizon_fusion`（短中期融合）
- 与基本面锚点线性/乘积融合（`fnd6_acdo/fnd6_drlt/fnd6_acox/fnd6_intc`）

## 3) 回测与提交执行记录

执行时间：`2026-02-26 16:53:38 CST`

已执行批次（均为 USA, REGULAR, D1）：

1. `replay_top_alphas.py`  
   结果文件：`/Users/jiaqianjing/workspace/quant/wq/docs/strategies/2026-02-26/replay_top_alphas_results.json`  
   `attempts=20`, `submitted_count=0`
2. `usa_regular_research_tuning.py`  
   结果文件：`/Users/jiaqianjing/workspace/quant/wq/docs/strategies/2026-02-26/usa_regular_research_results.json`  
   `attempts=42`, `submitted_count=0`
3. `usa_regular_latest_reports_run.py`  
   结果文件：`/Users/jiaqianjing/workspace/quant/wq/docs/strategies/2026-02-26/usa_regular_latest_reports_results.json`  
   `attempts=50`, `submitted_count=0`
4. 长轮询提交追踪（对当日达标未提交样本反复 check+submit）  
   样本池：134  
   结果：`0` 新提交，主要失败仍为 `PROD_CORRELATION / SELF_CORRELATION`
5. Universe 扩展扫描（尝试 `TOP200/TOP500/...` + 多 neutralization）  
   `TOP100` 在当前环境不可用（接口返回 400）  
   `TOP200` 已测样本未达提交阈值（Sharpe/Fitness 不足）
6. Pending 高分样本长轮询追踪（42 个候选）  
   结果：`0` 新提交；多数从 `PENDING=PROD_CORRELATION` 收敛为 `FAIL=PROD_CORRELATION` 或 `FAIL=SELF_CORRELATION`  
7. 随机家族广撒网（不过滤本地阈值、直接 check+submit）  
   结果：已测样本仍未通过 IS 检查，未新增 `SUBMITTED`

账户当前真实提交计数（REGULAR + SUBMITTED）：

- `submitted_regular_count=0`

## 4) 阻断原因（基于本地 submission checks 日志）

日志分析输出：

- Markdown: `/Users/jiaqianjing/workspace/quant/wq/docs/strategies/2026-02-26/submission_failure_analysis.md`
- JSON: `/Users/jiaqianjing/workspace/quant/wq/results/submission_failure_summary.json`

关键分布（89 条失败记录）：

- `PENDING=PROD_CORRELATION`: 19
- `PENDING=CHECK_TIMEOUT`: 10
- `HTTP 429 THROTTLED`: 10
- `FAIL=PROD_CORRELATION`: 8
- `FAIL=SELF_CORRELATION; PENDING=PROD_CORRELATION`: 4

结论：

- 当前主要瓶颈不是“回测指标不达标”，而是提交流程中的相关性检查未通过/未收敛（`PROD_CORRELATION`, `SELF_CORRELATION`）。
- 在已有生产池状态下，想在当天一次性拿到 5 个 `SUBMITTED`，需要进一步做“去同质化重构 + 低并发长轮询提交”。
- 在本轮结束时，未能将 `SUBMITTED (REGULAR)` 从 `0` 提升到 `5`。
- 后续新增日志统计（2026-02-27 刷新）：`total=144`, `submitted=0`, `failed=144`。

## 5) 下一步（可直接执行）

1. 先降并发并拉长检查窗口，避免 `THROTTLED/CHECK_TIMEOUT` 放大失败噪音。  
2. 把候选池改为“纯新家族”（减少与当前 PV/FUND 生产池重叠），并放弃仅参数微调。  
3. 对 `PENDING=PROD_CORRELATION` 的高分样本做间隔重检，等检查收敛后再 submit。
