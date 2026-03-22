# Submission Failure Analysis

- Generated At: 2026-02-26 16:52:46
- Input Log: `results/submission_checks.jsonl`
- Total Records: 89
- Failed Records: 89
- Submitted Records: 0

## Failure By Phase
- `check`: 88
- `submit`: 1

## Top Failure Reasons
- `PENDING=PROD_CORRELATION`: 19
- `PENDING=CHECK_TIMEOUT`: 10
- `HTTP 429: {"detail":"THROTTLED"}`: 10
- `FAIL=PROD_CORRELATION`: 8
- `FAIL=LOW_FITNESS,IS_LADDER_SHARPE; PENDING=SELF_CORRELATION,PROD_CORRELATION`: 8
- `FAIL=LOW_SHARPE,LOW_FITNESS,CONCENTRATED_WEIGHT,LOW_SUB_UNIVERSE_SHARPE,OLD_SIMULATION,LOW_2Y_SHARPE; PENDING=SELF_CORRELATION,PROD_CORRELATION`: 6
- `FAIL=LOW_SHARPE,LOW_FITNESS,CONCENTRATED_WEIGHT,OLD_SIMULATION,LOW_2Y_SHARPE; PENDING=SELF_CORRELATION,PROD_CORRELATION`: 6
- `FAIL=SELF_CORRELATION; PENDING=PROD_CORRELATION`: 4
- `FAIL=LOW_SHARPE,LOW_FITNESS,LOW_SUB_UNIVERSE_SHARPE,OLD_SIMULATION,LOW_2Y_SHARPE; PENDING=SELF_CORRELATION,PROD_CORRELATION`: 3
- `FAIL=LOW_SHARPE,LOW_FITNESS,HIGH_TURNOVER,LOW_SUB_UNIVERSE_SHARPE,LOW_2Y_SHARPE; PENDING=SELF_CORRELATION,PROD_CORRELATION`: 3

## Key Check Failures
- `LOW_FITNESS`: 34
  - alpha `xA9Rz8gm` value=0.9 limit=1.0
  - alpha `vRxdzMnG` value=0.91 limit=1.0
  - alpha `583M60AX` value=0.9 limit=1.0
- `LOW_SHARPE`: 26
  - alpha `1lJzXLK` value=0.28 limit=1.58
  - alpha `NJ9J8vL` value=0.87 limit=1.58
  - alpha `1YVVY1rX` value=0.94 limit=1.58
- `LOW_2Y_SHARPE`: 21
  - alpha `1lJzXLK` value=-0.91 limit=1.58
  - alpha `1YVVY1rX` value=0.85 limit=1.58
  - alpha `kWL01Rl` value=-0.32 limit=1.58
- `OLD_SIMULATION`: 20
  - alpha `1lJzXLK` value=None limit=None
  - alpha `NJ9J8vL` value=None limit=None
  - alpha `kWL01Rl` value=None limit=None
- `LOW_SUB_UNIVERSE_SHARPE`: 15
  - alpha `1lJzXLK` value=-0.3 limit=0.12
  - alpha `1YVVY1rX` value=0.19 limit=0.41
  - alpha `kWL01Rl` value=-0.22 limit=-0.13
- `CONCENTRATED_WEIGHT`: 12
  - alpha `1lJzXLK` value=0.203271 limit=0.1
  - alpha `7vdmqqv` value=0.13311 limit=0.1
  - alpha `QNQ8mqG` value=0.368506 limit=0.1
- `IS_LADDER_SHARPE`: 11
  - alpha `xA9Rz8gm` value=1.52 limit=1.58
  - alpha `vRxdzMnG` value=1.48 limit=1.58
  - alpha `583M60AX` value=1.57 limit=1.58
- `PROD_CORRELATION`: 8
  - alpha `omA9LXw6` value=0.9117 limit=0.7
  - alpha `QPrzvOQM` value=0.9267 limit=0.7
  - alpha `3q3nVoK6` value=0.925 limit=0.7
- `SELF_CORRELATION`: 4
  - alpha `VkqOqMow` value=0.7084 limit=0.7
  - alpha `qMvgPrZV` value=0.8364 limit=0.7
  - alpha `qMvgPrZV` value=0.8364 limit=0.7
- `HIGH_TURNOVER`: 3
  - alpha `wpww82Ad` value=0.7429 limit=0.7
  - alpha `RRZbRVZ0` value=1.2087 limit=0.7
  - alpha `ak2Vd13W` value=1.3358 limit=0.7

## Pending / Warning Checks
- Pending:
  - `PROD_CORRELATION`: 60
  - `SELF_CORRELATION`: 37
  - `CHECK_TIMEOUT`: 10
  - `POWER_POOL_CORRELATION`: 3
- Warning:
  - `OSMOSIS_ALLOCATION`: 69
  - `MATCHES_THEMES`: 69
  - `UNITS`: 4
  - `LOW_FITNESS`: 3
  - `POWER_POOL_DESCRIPTION_LENGTH`: 3
  - `POWER_POOL_DESCRIPTION_FORMAT`: 3
  - `LOW_SHARPE`: 2

## Repair Playbook
### LOW_FITNESS
- Priority: P3
- Diagnosis: 综合可交易性评分不足。
- Actions:
  - 同步改善 Sharpe 与 turnover：避免只优化单指标。
  - 减少极端仓位，保持信号连续性与稳定性。
  - 对高波动子表达式降权，提升稳态成分占比。

### LOW_SHARPE
- Priority: P3
- Diagnosis: 风险调整收益不足。
- Actions:
  - 收敛到更稳健的信号子集，减少高噪声组合。
  - 增加平滑或延迟（例如更长窗口、适度 decay）压制噪声。
  - 复查中性化粒度，优先 SUBINDUSTRY/INDUSTRY 对比。

### LOW_SUB_UNIVERSE_SHARPE
- Priority: P2
- Diagnosis: 子股票池稳定性不足。
- Actions:
  - 减少对小样本极端行为敏感的项。
  - 引入更普适的慢变量，增强跨子池稳健性。
  - 对表达式做分层测试后再放入批量提交。

### CONCENTRATED_WEIGHT
- Priority: P2
- Diagnosis: 权重集中度过高。
- Actions:
  - 加强行业/子行业中性化，避免少量股票主导。
  - 提高 truncation 稳定仓位分布。
  - 降低极端 rank 放大项权重。

### PROD_CORRELATION
- Priority: P3
- Diagnosis: 与已提交生产池 Alpha 相关性过高。
- Actions:
  - 切换信号家族（例如从纯价量切到基本面/事件代理，或反向）。
  - 在表达式中引入去同质化项：跨行业 group_rank、长短窗混合、非线性组合。
  - 优先尝试不同 decay / neutralization 组合以降低结构性共性。

### LOW_2Y_SHARPE
- Priority: P1
- Diagnosis: 未预置规则，建议基于该检查定义单独扩展。
- Actions:
  - 在日志中抽取该检查项的 value/limit 进行分桶分析。
  - 建立对应模板变异策略并小批量 A/B 回测。

### OLD_SIMULATION
- Priority: P1
- Diagnosis: 未预置规则，建议基于该检查定义单独扩展。
- Actions:
  - 在日志中抽取该检查项的 value/limit 进行分桶分析。
  - 建立对应模板变异策略并小批量 A/B 回测。

### CHECK_TIMEOUT
- Priority: P2
- Diagnosis: Check Submission 超时。
- Actions:
  - 提高 check 最大等待时长（例如 180->300s）。
  - 批次提交时降低并发，减少接口拥堵。

### SELF_CORRELATION
- Priority: P3
- Diagnosis: 与账户已有 Alpha 过于相似。
- Actions:
  - 更换核心 driver（主信号）而非仅微调权重。
  - 替换主要窗口参数（如 5->20, 20->60）并重新中性化。
  - 给表达式加入独立信息源（fundamental 或 regime filter）。

### IS_LADDER_SHARPE
- Priority: P1
- Diagnosis: 未预置规则，建议基于该检查定义单独扩展。
- Actions:
  - 在日志中抽取该检查项的 value/limit 进行分桶分析。
  - 建立对应模板变异策略并小批量 A/B 回测。

### HIGH_TURNOVER
- Priority: P2
- Diagnosis: 换手过高，交易成本风险大。
- Actions:
  - 提升 decay 或使用更长窗口，降低信号抖动。
  - 减少短周期价量项权重，增加慢变量。
  - 对最终信号做温和截断，避免频繁大幅切换。

### POWER_POOL_CORRELATION
- Priority: P1
- Diagnosis: 未预置规则，建议基于该检查定义单独扩展。
- Actions:
  - 在日志中抽取该检查项的 value/limit 进行分桶分析。
  - 建立对应模板变异策略并小批量 A/B 回测。

## Recent Failed Samples
- `2026-02-26 16:48:24` alpha `A1YkPdLR` phase=`check` reason=`FAIL=PROD_CORRELATION`
- `2026-02-26 16:42:53` alpha `XgP125d5` phase=`check` reason=`PENDING=PROD_CORRELATION`
- `2026-02-26 16:41:02` alpha `npY3Z0Ma` phase=`check` reason=`PENDING=PROD_CORRELATION`
- `2026-02-26 16:41:01` alpha `qMvgPrZV` phase=`check` reason=`FAIL=SELF_CORRELATION; PENDING=PROD_CORRELATION`
- `2026-02-26 16:38:28` alpha `YPnQ2KeM` phase=`check` reason=`PENDING=PROD_CORRELATION`
- `2026-02-26 16:32:24` alpha `XgP125d5` phase=`check` reason=`PENDING=PROD_CORRELATION`
- `2026-02-26 16:30:57` alpha `qMvgPrZV` phase=`check` reason=`FAIL=SELF_CORRELATION; PENDING=PROD_CORRELATION`
- `2026-02-26 16:30:56` alpha `npY3Z0Ma` phase=`check` reason=`PENDING=PROD_CORRELATION`
- `2026-02-26 16:29:14` alpha `YPnQ2KeM` phase=`check` reason=`PENDING=PROD_CORRELATION`
- `2026-02-26 16:27:19` alpha `npY3Z0Ma` phase=`check` reason=`PENDING=CHECK_TIMEOUT`
- `2026-02-26 16:22:17` alpha `XgP125d5` phase=`check` reason=`PENDING=PROD_CORRELATION`
- `2026-02-26 16:18:02` alpha `akMoAEQ1` phase=`check` reason=`PENDING=PROD_CORRELATION`
- `2026-02-26 16:18:01` alpha `YPnQklzw` phase=`check` reason=`PENDING=PROD_CORRELATION`
- `2026-02-26 16:17:58` alpha `2rQJv726` phase=`check` reason=`FAIL=LOW_FITNESS,IS_LADDER_SHARPE; PENDING=SELF_CORRELATION,PROD_CORRELATION`
- `2026-02-26 16:17:56` alpha `d5gdnxKJ` phase=`check` reason=`FAIL=LOW_FITNESS,IS_LADDER_SHARPE; PENDING=SELF_CORRELATION,PROD_CORRELATION`
- `2026-02-26 16:17:51` alpha `3q3zE7dN` phase=`check` reason=`FAIL=LOW_FITNESS,IS_LADDER_SHARPE; PENDING=SELF_CORRELATION,PROD_CORRELATION`
- `2026-02-26 16:17:14` alpha `YPnQ2KeM` phase=`check` reason=`PENDING=CHECK_TIMEOUT`
- `2026-02-26 16:16:11` alpha `akMoAEQ1` phase=`check` reason=`PENDING=PROD_CORRELATION`
- `2026-02-26 16:14:36` alpha `YPnQklzw` phase=`check` reason=`PENDING=PROD_CORRELATION`
- `2026-02-26 16:14:35` alpha `N1qAqG8E` phase=`check` reason=`HTTP 429: {"detail":"THROTTLED"}`
