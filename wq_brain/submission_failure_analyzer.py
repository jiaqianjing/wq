"""Submission failure log analyzer."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class CheckAdvice:
    priority: int
    diagnosis: str
    actions: List[str]


CHECK_ADVICE: Dict[str, CheckAdvice] = {
    "PROD_CORRELATION": CheckAdvice(
        priority=3,
        diagnosis="与已提交生产池 Alpha 相关性过高。",
        actions=[
            "切换信号家族（例如从纯价量切到基本面/事件代理，或反向）。",
            "在表达式中引入去同质化项：跨行业 group_rank、长短窗混合、非线性组合。",
            "优先尝试不同 decay / neutralization 组合以降低结构性共性。",
        ],
    ),
    "SELF_CORRELATION": CheckAdvice(
        priority=3,
        diagnosis="与账户已有 Alpha 过于相似。",
        actions=[
            "更换核心 driver（主信号）而非仅微调权重。",
            "替换主要窗口参数（如 5->20, 20->60）并重新中性化。",
            "给表达式加入独立信息源（fundamental 或 regime filter）。",
        ],
    ),
    "LOW_SHARPE": CheckAdvice(
        priority=3,
        diagnosis="风险调整收益不足。",
        actions=[
            "收敛到更稳健的信号子集，减少高噪声组合。",
            "增加平滑或延迟（例如更长窗口、适度 decay）压制噪声。",
            "复查中性化粒度，优先 SUBINDUSTRY/INDUSTRY 对比。",
        ],
    ),
    "LOW_FITNESS": CheckAdvice(
        priority=3,
        diagnosis="综合可交易性评分不足。",
        actions=[
            "同步改善 Sharpe 与 turnover：避免只优化单指标。",
            "减少极端仓位，保持信号连续性与稳定性。",
            "对高波动子表达式降权，提升稳态成分占比。",
        ],
    ),
    "HIGH_TURNOVER": CheckAdvice(
        priority=2,
        diagnosis="换手过高，交易成本风险大。",
        actions=[
            "提升 decay 或使用更长窗口，降低信号抖动。",
            "减少短周期价量项权重，增加慢变量。",
            "对最终信号做温和截断，避免频繁大幅切换。",
        ],
    ),
    "LOW_TURNOVER": CheckAdvice(
        priority=1,
        diagnosis="换手过低，可能信号更新不足。",
        actions=[
            "加入适度短周期项提升更新频率。",
            "缩短部分时间窗，让信号对新信息更敏感。",
        ],
    ),
    "CONCENTRATED_WEIGHT": CheckAdvice(
        priority=2,
        diagnosis="权重集中度过高。",
        actions=[
            "加强行业/子行业中性化，避免少量股票主导。",
            "提高 truncation 稳定仓位分布。",
            "降低极端 rank 放大项权重。",
        ],
    ),
    "LOW_SUB_UNIVERSE_SHARPE": CheckAdvice(
        priority=2,
        diagnosis="子股票池稳定性不足。",
        actions=[
            "减少对小样本极端行为敏感的项。",
            "引入更普适的慢变量，增强跨子池稳健性。",
            "对表达式做分层测试后再放入批量提交。",
        ],
    ),
    "DATA_DIVERSITY": CheckAdvice(
        priority=1,
        diagnosis="数据多样性不足。",
        actions=[
            "补充第二数据源（如 fundamental + price/volume 混合）。",
            "避免模板化表达式只改窗口参数。",
        ],
    ),
    "REGULAR_SUBMISSION": CheckAdvice(
        priority=2,
        diagnosis="常为提交配额或规则限制。",
        actions=[
            "核对当前周期提交配额后再重试。",
            "优先保留最有潜力样本，降低无效提交流量。",
        ],
    ),
    "CHECK_TIMEOUT": CheckAdvice(
        priority=2,
        diagnosis="Check Submission 超时。",
        actions=[
            "提高 check 最大等待时长（例如 180->300s）。",
            "批次提交时降低并发，减少接口拥堵。",
        ],
    ),
}


def _fmt_ts(ts: Any) -> str:
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return "-"


def _parse_jsonl(path: Path) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    if not path.exists():
        return records
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            raw = line.strip()
            if not raw:
                continue
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                records.append(payload)
    return records


def _extract_names(items: Any) -> List[str]:
    if not isinstance(items, list):
        return []
    out: List[str] = []
    for item in items:
        if isinstance(item, dict):
            name = str(item.get("name", "")).strip()
            if name:
                out.append(name)
    return out


def _build_summary(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    phase_counter: Counter[str] = Counter()
    reason_counter: Counter[str] = Counter()
    fail_counter: Counter[str] = Counter()
    pending_counter: Counter[str] = Counter()
    warning_counter: Counter[str] = Counter()
    fail_examples: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    recent_failures: List[Dict[str, Any]] = []

    submitted = 0
    failed = 0

    for r in records:
        if r.get("submitted") is True:
            submitted += 1
            continue

        failed += 1
        phase = str(r.get("phase", "unknown"))
        reason = str(r.get("reason", ""))
        phase_counter[phase] += 1
        if reason:
            reason_counter[reason] += 1

        check_result = r.get("check_result", {})
        if isinstance(check_result, dict):
            failed_checks = check_result.get("failed_checks", [])
            pending_checks = check_result.get("pending_checks", [])
            warning_checks = check_result.get("warning_checks", [])
        else:
            failed_checks = []
            pending_checks = []
            warning_checks = []

        for item in failed_checks if isinstance(failed_checks, list) else []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            if not name:
                continue
            fail_counter[name] += 1
            if len(fail_examples[name]) < 3:
                fail_examples[name].append(
                    {
                        "alpha_id": r.get("alpha_id", ""),
                        "value": item.get("value"),
                        "limit": item.get("limit"),
                    }
                )

        for name in _extract_names(pending_checks):
            pending_counter[name] += 1
        for name in _extract_names(warning_checks):
            warning_counter[name] += 1

        recent_failures.append(
            {
                "ts": r.get("ts"),
                "alpha_id": r.get("alpha_id", ""),
                "phase": phase,
                "reason": reason,
            }
        )

    recent_failures.sort(key=lambda x: (x.get("ts") or 0), reverse=True)

    return {
        "total_records": len(records),
        "submitted_records": submitted,
        "failed_records": failed,
        "phase_counter": dict(phase_counter),
        "reason_counter_top": reason_counter.most_common(10),
        "fail_counter": dict(fail_counter),
        "pending_counter": dict(pending_counter),
        "warning_counter": dict(warning_counter),
        "fail_examples": dict(fail_examples),
        "recent_failures": recent_failures[:20],
    }


def _rank_focus_checks(fail_counter: Dict[str, int], pending_counter: Dict[str, int]) -> List[str]:
    weighted: List[Tuple[int, str]] = []
    seen = set()
    for name, count in sorted(fail_counter.items(), key=lambda x: x[1], reverse=True):
        advice = CHECK_ADVICE.get(name)
        priority = advice.priority if advice else 1
        weighted.append((count * priority, name))
        seen.add(name)
    for name, count in sorted(pending_counter.items(), key=lambda x: x[1], reverse=True):
        if name in seen:
            continue
        advice = CHECK_ADVICE.get(name)
        priority = advice.priority if advice else 1
        weighted.append((count * priority, name))
    weighted.sort(key=lambda x: x[0], reverse=True)
    return [name for _, name in weighted]


def _to_markdown(
    input_path: Path,
    summary: Dict[str, Any],
) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    phase_counter = summary["phase_counter"]
    fail_counter = summary["fail_counter"]
    pending_counter = summary["pending_counter"]
    warning_counter = summary["warning_counter"]
    fail_examples = summary["fail_examples"]
    recent_failures = summary["recent_failures"]
    reason_top = summary["reason_counter_top"]

    lines: List[str] = []
    lines.append("# Submission Failure Analysis")
    lines.append("")
    lines.append(f"- Generated At: {now}")
    lines.append(f"- Input Log: `{input_path}`")
    lines.append(f"- Total Records: {summary['total_records']}")
    lines.append(f"- Failed Records: {summary['failed_records']}")
    lines.append(f"- Submitted Records: {summary['submitted_records']}")
    lines.append("")

    lines.append("## Failure By Phase")
    if phase_counter:
        for phase, cnt in sorted(phase_counter.items(), key=lambda x: x[1], reverse=True):
            lines.append(f"- `{phase}`: {cnt}")
    else:
        lines.append("- no failed records")
    lines.append("")

    lines.append("## Top Failure Reasons")
    if reason_top:
        for reason, cnt in reason_top:
            lines.append(f"- `{reason}`: {cnt}")
    else:
        lines.append("- none")
    lines.append("")

    lines.append("## Key Check Failures")
    if fail_counter:
        for name, cnt in sorted(fail_counter.items(), key=lambda x: x[1], reverse=True):
            lines.append(f"- `{name}`: {cnt}")
            examples = fail_examples.get(name, [])
            for ex in examples:
                lines.append(
                    f"  - alpha `{ex.get('alpha_id')}` value={ex.get('value')} limit={ex.get('limit')}"
                )
    else:
        lines.append("- none")
    lines.append("")

    lines.append("## Pending / Warning Checks")
    if pending_counter:
        lines.append("- Pending:")
        for name, cnt in sorted(pending_counter.items(), key=lambda x: x[1], reverse=True):
            lines.append(f"  - `{name}`: {cnt}")
    else:
        lines.append("- Pending: none")
    if warning_counter:
        lines.append("- Warning:")
        for name, cnt in sorted(warning_counter.items(), key=lambda x: x[1], reverse=True):
            lines.append(f"  - `{name}`: {cnt}")
    else:
        lines.append("- Warning: none")
    lines.append("")

    lines.append("## Repair Playbook")
    focus = _rank_focus_checks(fail_counter, pending_counter)
    if not focus:
        lines.append("- no check failures captured yet")
        return "\n".join(lines) + "\n"

    for name in focus:
        advice = CHECK_ADVICE.get(name)
        lines.append(f"### {name}")
        if advice:
            lines.append(f"- Priority: P{advice.priority}")
            lines.append(f"- Diagnosis: {advice.diagnosis}")
            lines.append("- Actions:")
            for action in advice.actions:
                lines.append(f"  - {action}")
        else:
            lines.append("- Priority: P1")
            lines.append("- Diagnosis: 未预置规则，建议基于该检查定义单独扩展。")
            lines.append("- Actions:")
            lines.append("  - 在日志中抽取该检查项的 value/limit 进行分桶分析。")
            lines.append("  - 建立对应模板变异策略并小批量 A/B 回测。")
        lines.append("")

    lines.append("## Recent Failed Samples")
    if recent_failures:
        for item in recent_failures:
            lines.append(
                f"- `{_fmt_ts(item.get('ts'))}` alpha `{item.get('alpha_id')}` "
                f"phase=`{item.get('phase')}` reason=`{item.get('reason')}`"
            )
    else:
        lines.append("- none")

    return "\n".join(lines) + "\n"


def generate_submission_failure_report(
    input_path: str | Path = "results/submission_checks.jsonl",
    output_md: Optional[str | Path] = None,
    output_json: Optional[str | Path] = None,
) -> Dict[str, Any]:
    input_path = Path(input_path)
    run_date = datetime.now().strftime("%Y-%m-%d")
    md_path = (
        Path(output_md)
        if output_md is not None
        else Path(f"docs/strategies/{run_date}/submission_failure_analysis.md")
    )
    json_path = Path(output_json) if output_json is not None else None

    records = _parse_jsonl(input_path)
    summary = _build_summary(records)
    report = _to_markdown(input_path=input_path, summary=summary)

    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(report, encoding="utf-8")

    if json_path is not None:
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    return {
        "summary": summary,
        "output_md": str(md_path),
        "output_json": str(json_path) if json_path is not None else "",
    }
