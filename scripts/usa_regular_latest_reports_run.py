#!/usr/bin/env python3
"""USA Regular: latest-research-driven strategy search + backtest + submit."""

from __future__ import annotations

import argparse
import itertools
import json
import os
import random
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

from dotenv import load_dotenv

from wq_brain.client import SimulateResult, WorldQuantBrainClient
from wq_brain.submission_failure_analyzer import generate_submission_failure_report


@dataclass
class Criteria:
    min_sharpe: float = 1.58
    min_fitness: float = 1.0
    min_turnover: float = 0.01
    max_turnover: float = 0.7
    max_drawdown: float = 0.1
    min_returns: float = 0.0

    def check(self, r: SimulateResult) -> bool:
        return (
            r.sharpe >= self.min_sharpe
            and r.fitness >= self.min_fitness
            and self.min_turnover <= r.turnover <= self.max_turnover
            and r.drawdown <= self.max_drawdown
            and r.returns >= self.min_returns
        )


def build_candidates(max_candidates: int, seed: int) -> List[Dict[str, Any]]:
    """Build candidates from latest 2025-2026 research themes."""
    # RFS 2025 (short-term reversal + MAX family)
    rev_max = [
        "-rank(ts_delta(close, 1)) * rank(ts_rank(returns, 20))",
        "-rank(ts_delta(close, 3)) * rank(ts_rank(returns, 40))",
        "-rank(ts_delta(close, 5)) * rank(ts_rank(returns, 60))",
    ]

    # NBER w34104 (volume-predictability alpha)
    vol_pred = [
        "rank(volume / (adv20 + 0.001)) * rank(ts_sum(returns, 5))",
        "rank(volume / (adv60 + 0.001)) * rank(ts_sum(returns, 10))",
        "rank(ts_delta(volume, 3)) * rank(ts_sum(returns, 20))",
    ]

    # Rebalancing-pressure family
    rebalance = [
        "-rank(ts_delta(close, 1)) * rank(ts_delta(volume, 1)) * (1 - rank(ts_std_dev(returns, 60)))",
        "-rank(ts_delta(close, 3)) * rank(volume / (ts_mean(volume, 60) + 0.001))",
        "-rank(ts_delta(close, 5)) * rank(volume / (ts_mean(volume, 120) + 0.001))",
    ]

    # Day/Night style (intraday vs overnight)
    day_night = [
        "rank((open - ts_delay(close, 1)) / (ts_delay(close, 1) + 0.001)) - rank((close - open) / (open + 0.001))",
        "rank((close - open) / (open + 0.001)) * (1 - rank((open - ts_delay(close, 1)) / (ts_delay(close, 1) + 0.001)))",
    ]

    # arXiv 2601.13112 (cross-market Alpha191 style)
    corr_decay = [
        "-rank(ts_delta(ts_corr(high, volume, 10), 3)) * rank(ts_corr(rank(close), rank(volume), 60))",
        "-ts_rank(ts_corr(rank(close), rank(volume), 20), 10) * rank(ts_std_dev(returns, 60))",
    ]

    # arXiv 2509.06702 (horizon-aware fusion proxy)
    horizon_fusion = [
        "rank(ts_sum(returns, 5)) * (1 - rank(ts_std_dev(returns, 20)))",
        "rank(ts_sum(returns, 20)) * (1 - rank(ts_std_dev(returns, 60)))",
    ]

    # Fundamental anchors for decorrelation
    fund = [
        "group_rank((fnd6_acdo) / cap, subindustry)",
        "group_rank((fnd6_drlt) / revenue, subindustry)",
        "group_rank(ts_rank(fnd6_acox, 120), subindustry)",
        "group_rank((fnd6_intc) / cap, subindustry)",
    ]

    base_blocks = rev_max + vol_pred + rebalance + day_night + corr_decay + horizon_fusion
    candidates: List[Dict[str, Any]] = []

    # Standalone blocks
    for b in base_blocks:
        candidates.append(
            {
                "name": "latest_block",
                "theme": "research_block",
                "expression": f"rank({b})",
            }
        )

    # Block + fundamental (linear)
    for b, f, w in itertools.product(base_blocks, fund, [0.35, 0.5, 0.65]):
        candidates.append(
            {
                "name": "latest_lin_fund",
                "theme": "research_plus_fundamental",
                "expression": f"rank(({w} * ({b})) + ({1-w} * ({f})))",
            }
        )

    # Block x fundamental (nonlinear)
    for b, f in itertools.product(base_blocks, fund):
        candidates.append(
            {
                "name": "latest_prod_fund",
                "theme": "research_x_fundamental",
                "expression": f"rank(({b}) * ({f}))",
            }
        )

    # Dedup + shuffle by theme to keep diversity
    dedup: Dict[str, Dict[str, Any]] = {}
    for c in candidates:
        dedup[c["expression"]] = c
    out = list(dedup.values())
    rnd = random.Random(seed)
    rnd.shuffle(out)
    return out[:max_candidates]


def create_simulation(client: WorldQuantBrainClient, expression: str, settings: Dict[str, Any]) -> Tuple[str, str]:
    payload = {
        "type": "REGULAR",
        "settings": {
            "instrumentType": "EQUITY",
            "region": "USA",
            "universe": "TOP3000",
            "delay": 1,
            "decay": settings["decay"],
            "neutralization": settings["neutralization"],
            "truncation": settings["truncation"],
            "pasteurization": settings["pasteurization"],
            "testPeriod": "P0Y",
            "unitHandling": "VERIFY",
            "nanHandling": settings["nanHandling"],
            "language": "FASTEXPR",
            "visualization": False,
        },
        "regular": expression,
    }

    for _ in range(3):
        resp = client._request("post", f"{client.BASE_URL}/simulations", json=payload, timeout=30)
        if resp.status_code == 201:
            return resp.headers.get("Location", ""), ""
        if resp.status_code == 429:
            time.sleep(4)
            continue
        return "", f"HTTP {resp.status_code}: {resp.text[:300]}"

    return "", "HTTP 429"


def resolve_batch(
    client: WorldQuantBrainClient,
    in_flight: List[Tuple[Dict[str, Any], Dict[str, Any], str]],
    max_wait: int,
) -> List[Tuple[Dict[str, Any], Dict[str, Any], SimulateResult]]:
    pending = {idx: item for idx, item in enumerate(in_flight)}
    resolved: List[Tuple[Dict[str, Any], Dict[str, Any], SimulateResult]] = []
    deadline = time.time() + max_wait

    while pending and time.time() < deadline:
        for idx in list(pending.keys()):
            cand, settings, progress_url = pending[idx]
            try:
                resp = client._request("get", progress_url, timeout=20)
            except Exception:
                continue

            if resp.status_code != 200:
                continue
            if resp.headers.get("Retry-After"):
                continue

            data = resp.json()
            alpha_id = data.get("alpha", "")
            if alpha_id:
                sim = client._get_alpha_result(alpha_id)
                resolved.append((cand, settings, sim))
                pending.pop(idx, None)
                continue

            status = str(data.get("status", "")).upper()
            if status in {"ERROR", "FAILED", "FAIL"}:
                resolved.append(
                    (
                        cand,
                        settings,
                        SimulateResult(
                            alpha_id="",
                            status=status,
                            sharpe=0,
                            fitness=0,
                            turnover=0,
                            returns=0,
                            drawdown=0,
                            margin=0,
                            is_submittable=False,
                            error_message=data.get("message", "simulation failed"),
                        ),
                    )
                )
                pending.pop(idx, None)

        if pending:
            time.sleep(3)

    for idx in list(pending.keys()):
        cand, settings, _ = pending[idx]
        resolved.append(
            (
                cand,
                settings,
                SimulateResult(
                    alpha_id="",
                    status="TIMEOUT",
                    sharpe=0,
                    fitness=0,
                    turnover=0,
                    returns=0,
                    drawdown=0,
                    margin=0,
                    is_submittable=False,
                    error_message=f"timeout>{max_wait}s",
                ),
            )
        )

    return resolved


def submit_with_reason(client: WorldQuantBrainClient, alpha_id: str, name: str) -> Tuple[bool, str]:
    result = client.submit_alpha_with_checks(alpha_id=alpha_id, name=name, check_max_wait=300)
    return result.submitted, result.reason


def main() -> None:
    parser = argparse.ArgumentParser(description="USA Regular latest reports run")
    parser.add_argument("--target", type=int, default=5)
    parser.add_argument("--max-candidates", type=int, default=160)
    parser.add_argument("--max-total", type=int, default=260)
    parser.add_argument("--batch-size", type=int, default=6)
    parser.add_argument("--wait-seconds", type=int, default=320)
    parser.add_argument("--seed", type=int, default=20260226)
    parser.add_argument("--disable-proxy", action="store_true")
    args = parser.parse_args()

    if args.disable_proxy:
        os.environ["WQB_DISABLE_PROXY"] = "1"

    load_dotenv(".env")
    username = os.getenv("WQB_USERNAME")
    password = os.getenv("WQB_PASSWORD")
    if not username or not password:
        raise RuntimeError("missing WQB_USERNAME/WQB_PASSWORD")

    client = WorldQuantBrainClient(username, password)
    if not client.authenticate():
        raise RuntimeError("auth failed")

    criteria = Criteria()
    candidates = build_candidates(args.max_candidates, args.seed)
    settings_grid = [
        {"decay": 4, "neutralization": "SUBINDUSTRY", "truncation": 0.08, "pasteurization": "ON", "nanHandling": "ON"},
        {"decay": 6, "neutralization": "SUBINDUSTRY", "truncation": 0.08, "pasteurization": "ON", "nanHandling": "ON"},
        {"decay": 8, "neutralization": "SUBINDUSTRY", "truncation": 0.08, "pasteurization": "ON", "nanHandling": "ON"},
        {"decay": 8, "neutralization": "INDUSTRY", "truncation": 0.08, "pasteurization": "ON", "nanHandling": "ON"},
        {"decay": 10, "neutralization": "INDUSTRY", "truncation": 0.06, "pasteurization": "ON", "nanHandling": "ON"},
    ]

    worklist: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    for c in candidates:
        for s in settings_grid:
            worklist.append((c, s))

    run_date = datetime.now().strftime("%Y-%m-%d")
    out_dir = Path(f"docs/strategies/{run_date}")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "usa_regular_latest_reports_results.json"

    attempts: List[Dict[str, Any]] = []
    submitted_ids: List[str] = []

    print(f"candidates={len(candidates)} worklist={len(worklist)}", flush=True)
    cursor = 0
    while cursor < len(worklist) and len(attempts) < args.max_total and len(submitted_ids) < args.target:
        batch = worklist[cursor : cursor + args.batch_size]
        cursor += args.batch_size
        print(f"batch={len(batch)} tried={len(attempts)} submitted={len(submitted_ids)}", flush=True)

        in_flight: List[Tuple[Dict[str, Any], Dict[str, Any], str]] = []
        for cand, settings in batch:
            if len(attempts) + len(in_flight) >= args.max_total:
                break
            progress_url, error = create_simulation(client, cand["expression"], settings)
            if progress_url:
                in_flight.append((cand, settings, progress_url))
            else:
                attempts.append(
                    {
                        "candidate": cand,
                        "settings": settings,
                        "simulate_error": error,
                        "timestamp": datetime.now().isoformat(),
                    }
                )
            time.sleep(0.25)

        resolved = resolve_batch(client, in_flight, args.wait_seconds)
        for cand, settings, sim in resolved:
            passed = criteria.check(sim)
            submitted = False
            submit_reason = ""
            if passed and sim.alpha_id:
                alpha_name = f"{cand['name']}_{settings['decay']}_{settings['neutralization']}_{sim.alpha_id[:6]}"
                submitted, submit_reason = submit_with_reason(client, sim.alpha_id, alpha_name)
                if submitted:
                    submitted_ids.append(sim.alpha_id)

            attempts.append(
                {
                    "candidate": cand,
                    "settings": settings,
                    "simulate": asdict(sim),
                    "passed": passed,
                    "submitted": submitted,
                    "submit_reason": submit_reason,
                    "timestamp": datetime.now().isoformat(),
                }
            )
            print(
                f"{cand['name']} | d={settings['decay']} n={settings['neutralization']} | "
                f"sh={sim.sharpe:.2f} fit={sim.fitness:.2f} to={sim.turnover:.3f} dd={sim.drawdown:.3f} "
                f"pass={passed} submit={submitted}",
                flush=True,
            )
            if len(submitted_ids) >= args.target:
                break

        out_path.write_text(
            json.dumps(
                {
                    "run_date": run_date,
                    "criteria": asdict(criteria),
                    "submitted_ids": submitted_ids,
                    "submitted_count": len(submitted_ids),
                    "attempts": attempts,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    analysis_md = out_dir / "usa_regular_latest_reports_submission_failure_analysis.md"
    analysis_json = out_dir / "usa_regular_latest_reports_submission_failure_summary.json"
    try:
        analysis = generate_submission_failure_report(
            input_path=client.submission_log_path,
            output_md=analysis_md,
            output_json=analysis_json,
        )
        print(f"submission_analysis={analysis['output_md']}", flush=True)
    except Exception as e:
        print(f"submission_analysis_failed: {e}", flush=True)

    print(f"done submitted={len(submitted_ids)} file={out_path}", flush=True)


if __name__ == "__main__":
    main()
