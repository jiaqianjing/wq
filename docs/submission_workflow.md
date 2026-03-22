# Alpha 提交流程（Check + Submit）

从 2026-02-26 起，项目内提交逻辑统一为以下流程，避免 API 返回成功但网页仍显示 `UNSUBMITTED`：

1. `PATCH /alphas/{id}`：自动补齐提交属性（`name`、`category`）
2. `GET /alphas/{id}/check`：触发并轮询 Check Submission（处理 `Retry-After`）
3. `POST /alphas/{id}/submit`：仅在 check 无 `FAIL/PENDING` 时执行
4. `GET /alphas/{id}`：确认最终是否真的 `SUBMITTED`

## 本地失败日志

- 默认文件：`results/submission_checks.jsonl`
- 可通过环境变量覆盖：`WQB_SUBMISSION_LOG=/your/path/submission_checks.jsonl`
- 格式：JSON Lines（一行一条事件）

关键字段：

- `alpha_id`: Alpha ID
- `submitted`: 是否最终提交成功
- `phase`: 失败阶段（`set_properties` / `check` / `submit`）
- `reason`: 失败原因（含 FAIL/PENDING 检查项汇总）
- `name`, `category`: 当次提交使用的属性
- `check_result`: 完整 check 结果（包含 `failed_checks`、`pending_checks`）
- `submit_http_status`: submit 请求状态码（若有）

## 当前已接入脚本

- `wq_brain/client.py`（`submit_alpha_with_checks`）
- `scripts/usa_regular_research_tuning.py`
- `scripts/usa_combo_tuning.py`
- `scripts/replay_top_alphas.py`
- `scripts/power_pool_research_run.py`

## 失败分析工具

可直接分析 `submission_checks.jsonl` 并输出修正建议报告：

```bash
uv run python scripts/analyze_submission_failures.py
```

默认输出：

- `docs/strategies/<today>/submission_failure_analysis.md`

可选输出 JSON 摘要：

```bash
uv run python scripts/analyze_submission_failures.py \
  --output-json results/submission_failure_summary.json
```

以下研究脚本在运行结束时会自动生成失败分析报告（无需手动执行）：

- `scripts/usa_regular_research_tuning.py` -> `docs/strategies/<today>/usa_regular_submission_failure_analysis.md`
- `scripts/usa_combo_tuning.py` -> `docs/strategies/<today>/usa_combo_submission_failure_analysis.md`
- `scripts/replay_top_alphas.py` -> `docs/strategies/<today>/replay_submission_failure_analysis.md`
- `scripts/power_pool_research_run.py` -> `docs/strategies/<today>/power_pool_submission_failure_analysis.md`

主入口命令同样会自动生成：

- `python main.py submit ...` -> `docs/strategies/<today>/main_submit_submission_failure_analysis.md`
- `python main.py pending` -> `docs/strategies/<today>/main_pending_submission_failure_analysis.md`
