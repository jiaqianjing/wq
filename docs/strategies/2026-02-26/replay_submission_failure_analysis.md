# Submission Failure Analysis

- Generated At: 2026-02-26 15:37:56
- Input Log: `results/submission_checks.jsonl`
- Total Records: 5
- Failed Records: 5
- Submitted Records: 0

## Failure By Phase
- `check`: 5

## Top Failure Reasons
- `FAIL=PROD_CORRELATION`: 5

## Key Check Failures
- `PROD_CORRELATION`: 5
  - alpha `omA9LXw6` value=0.9117 limit=0.7
  - alpha `QPrzvOQM` value=0.9267 limit=0.7
  - alpha `3q3nVoK6` value=0.925 limit=0.7

## Pending / Warning Checks
- Pending: none
- Warning:
  - `OSMOSIS_ALLOCATION`: 5
  - `MATCHES_THEMES`: 5

## Repair Playbook
### PROD_CORRELATION
- Priority: P3
- Diagnosis: 与已提交生产池 Alpha 相关性过高。
- Actions:
  - 切换信号家族（例如从纯价量切到基本面/事件代理，或反向）。
  - 在表达式中引入去同质化项：跨行业 group_rank、长短窗混合、非线性组合。
  - 优先尝试不同 decay / neutralization 组合以降低结构性共性。

## Recent Failed Samples
- `2026-02-26 15:35:27` alpha `XgPYbxA8` phase=`check` reason=`FAIL=PROD_CORRELATION`
- `2026-02-26 15:34:56` alpha `78qmXEN1` phase=`check` reason=`FAIL=PROD_CORRELATION`
- `2026-02-26 15:34:32` alpha `3q3nVoK6` phase=`check` reason=`FAIL=PROD_CORRELATION`
- `2026-02-26 15:34:10` alpha `QPrzvOQM` phase=`check` reason=`FAIL=PROD_CORRELATION`
- `2026-02-26 15:04:07` alpha `omA9LXw6` phase=`check` reason=`FAIL=PROD_CORRELATION`
