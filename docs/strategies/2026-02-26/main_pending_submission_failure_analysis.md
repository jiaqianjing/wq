# Submission Failure Analysis

- Generated At: 2026-02-26 15:26:26
- Input Log: `results/submission_checks.jsonl`
- Total Records: 1
- Failed Records: 1
- Submitted Records: 0

## Failure By Phase
- `check`: 1

## Top Failure Reasons
- `FAIL=PROD_CORRELATION`: 1

## Key Check Failures
- `PROD_CORRELATION`: 1
  - alpha `omA9LXw6` value=0.9117 limit=0.7

## Pending / Warning Checks
- Pending: none
- Warning:
  - `OSMOSIS_ALLOCATION`: 1
  - `MATCHES_THEMES`: 1

## Repair Playbook
### PROD_CORRELATION
- Priority: P3
- Diagnosis: 与已提交生产池 Alpha 相关性过高。
- Actions:
  - 切换信号家族（例如从纯价量切到基本面/事件代理，或反向）。
  - 在表达式中引入去同质化项：跨行业 group_rank、长短窗混合、非线性组合。
  - 优先尝试不同 decay / neutralization 组合以降低结构性共性。

## Recent Failed Samples
- `2026-02-26 15:04:07` alpha `omA9LXw6` phase=`check` reason=`FAIL=PROD_CORRELATION`
