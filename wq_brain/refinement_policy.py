"""Refinement policy for near-miss and submission-blocked experiments.

CALLING SPEC:
    reason = blocked_submission_reason(experiment) -> str
    refinable = is_refinable_blocked_experiment(experiment) -> bool
    problem = refinement_problem(experiment, criteria) -> str
    strategies = refinement_strategy_lines(experiment, criteria) -> List[str]

Inputs:
    experiment: experiment row dict from runtime.db
    criteria: SubmissionCriteria with current account thresholds

Outputs:
    Short, deterministic descriptions used by the engineer refinement prompt.
"""

from __future__ import annotations

from typing import Any, Dict, List

from .alpha_submitter import SubmissionCriteria


def blocked_submission_reason(experiment: Dict[str, Any]) -> str:
    raw = str(experiment.get("submission_result") or "")
    if raw.startswith("blocked:"):
        return raw.split(":", 1)[1]
    return ""


def is_refinable_blocked_experiment(experiment: Dict[str, Any]) -> bool:
    reason = blocked_submission_reason(experiment)
    return reason.startswith("FAIL=") or reason.startswith("PENDING=")


def refinement_problem(experiment: Dict[str, Any], criteria: SubmissionCriteria) -> str:
    reason = blocked_submission_reason(experiment)
    if "PROD_CORRELATION" in reason:
        return "platform submission blocked by high PROD_CORRELATION"
    if "SELF_CORRELATION" in reason:
        return "platform submission blocked by high SELF_CORRELATION"
    if reason:
        return f"platform submission blocked ({reason})"
    if (experiment.get("turnover") or 0) > criteria.max_turnover:
        return "turnover too high"
    if (experiment.get("fitness") or 0) < criteria.min_fitness:
        return "fitness too low"
    return "improve Sharpe/Fitness while keeping turnover controlled"


def refinement_strategy_lines(experiment: Dict[str, Any], criteria: SubmissionCriteria) -> List[str]:
    reason = blocked_submission_reason(experiment)
    lines = ["- Keep the core signal idea intact and change structure deliberately, not cosmetically."]
    if "PROD_CORRELATION" in reason:
        lines.extend(
            [
                "- Reduce product correlation by changing the signal family, conditioning path, or feature mix.",
                "- Do not rely on small window/decay tweaks alone; add an orthogonal component or swap to a different transformation route.",
                "- Prefer de-correlation tactics such as conditional gating, regime filters, alternate ranking paths, or a second signal from a different data family.",
            ]
        )
        return lines
    if "SELF_CORRELATION" in reason:
        lines.extend(
            [
                "- Reduce self-correlation by changing rebalance cadence, delay sensitivity, or smoothing path.",
                "- Introduce regime-aware gating or a distinct confirmation term so the new alpha is not a near-clone of its parent.",
            ]
        )
        return lines
    if reason:
        lines.append("- Resolve the platform submission blocker while preserving the alpha's edge.")
        return lines
    if (experiment.get("turnover") or 0) > criteria.max_turnover:
        lines.extend(
            [
                "- If turnover is too high, add smoothing, longer windows, or more stable neutralization.",
                "- Avoid brittle short-horizon triggers that create unnecessary churn.",
            ]
        )
        return lines
    lines.extend(
        [
            "- If fitness is too low, combine with a genuinely different second signal or adjust the ranking path.",
            "- Improve robustness instead of just amplifying the same motif.",
        ]
    )
    return lines
