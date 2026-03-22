#!/usr/bin/env python3
"""USA 定向组合微调：技术高 Sharpe 因子 + 基本面高 Fitness 因子。"""

from __future__ import annotations

import argparse
import itertools
import json
import os
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


def build_candidates(max_candidates: int) -> List[Dict[str, Any]]:
    tech_factors = [
        {
            "name": "tech_vwap_z",
            "expr": "rank( - (close - vwap) / ts_std_dev(close - vwap, 20) )",
        },
        {
            "name": "tech_hlc3_z",
            "expr": "rank( - (close - ((high + low + close) / 3)) / ts_std_dev(close - ((high + low + close) / 3), 20) )",
        },
    ]

    fund_factors = [
        {
            "name": "fund_acox_assets",
            "expr": "group_rank((fnd6_acox)/assets, subindustry)",
        },
        {
            "name": "fund_acdo_cap",
            "expr": "group_rank((fnd6_acdo)/cap, subindustry)",
        },
        {
            "name": "fund_esopct_cap",
            "expr": "group_rank((fnd6_esopct)/cap, subindustry)",
        },
        {
            "name": "fund_intc_cap",
            "expr": "group_rank((fnd6_intc)/cap, subindustry)",
        },
        {
            "name": "fund_drlt_revenue",
            "expr": "group_rank((fnd6_drlt)/revenue, subindustry)",
        },
        {
            "name": "fund_ts_acdo_60",
            "expr": "group_rank(ts_rank(fnd6_acdo, 60), subindustry)",
        },
    ]

    candidates: List[Dict[str, Any]] = []

    # baseline: 基本面单因子
    for f in fund_factors:
        candidates.append(
            {
                "name": f"baseline_{f['name']}",
                "expression": f["expr"],
                "mix": "baseline",
            }
        )

    # mix: 线性组合 + 乘积组合
    for t, f, w in itertools.product(tech_factors, fund_factors, [0.45, 0.55, 0.65, 0.75]):
        wt = w
        wf = 1 - w
        candidates.append(
            {
                "name": f"lin_{t['name']}_{f['name']}_w{int(w*100)}",
                "expression": f"({wt} * ({t['expr']})) + ({wf} * ({f['expr']}))",
                "mix": "linear",
            }
        )
        candidates.append(
            {
                "name": f"ranklin_{t['name']}_{f['name']}_w{int(w*100)}",
                "expression": f"rank(({wt} * ({t['expr']})) + ({wf} * ({f['expr']})))",
                "mix": "rank_linear",
            }
        )
        candidates.append(
            {
                "name": f"prod_{t['name']}_{f['name']}_w{int(w*100)}",
                "expression": f"rank(({t['expr']}) * ({f['expr']}))",
                "mix": "rank_product",
            }
        )

    # 去重并截断
    dedup: Dict[str, Dict[str, Any]] = {}
    for c in candidates:
        dedup[c["expression"]] = c
    out = list(dedup.values())
    out.sort(key=lambda x: x["name"])
    return out[:max_candidates]


def create_sim(client: WorldQuantBrainClient, expression: str, settings: Dict[str, Any]) -> Tuple[str, str]:
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
        r = client._request("post", f"{client.BASE_URL}/simulations", json=payload, timeout=30)
        if r.status_code == 201:
            return r.headers.get("Location", ""), ""
        if r.status_code == 429:
            time.sleep(4)
            continue
        return "", f"HTTP {r.status_code}: {r.text[:200]}"
    return "", "HTTP 429"


def resolve_batch(
    client: WorldQuantBrainClient,
    in_flight: List[Tuple[Dict[str, Any], Dict[str, Any], str]],
    max_wait: int,
) -> List[Tuple[Dict[str, Any], Dict[str, Any], SimulateResult]]:
    pending = {i: item for i, item in enumerate(in_flight)}
    resolved: List[Tuple[Dict[str, Any], Dict[str, Any], SimulateResult]] = []
    deadline = time.time() + max_wait

    while pending and time.time() < deadline:
        for i in list(pending.keys()):
            cand, settings, url = pending[i]
            try:
                r = client._request("get", url, timeout=20)
            except Exception:
                continue
            if r.status_code != 200:
                continue
            if r.headers.get("Retry-After"):
                continue
            data = r.json()
            aid = data.get("alpha", "")
            if aid:
                sim = client._get_alpha_result(aid)
                resolved.append((cand, settings, sim))
                pending.pop(i, None)
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
                            error_message=data.get("message", "sim failed"),
                        ),
                    )
                )
                pending.pop(i, None)
        if pending:
            time.sleep(3)

    for i in list(pending.keys()):
        cand, settings, _ = pending[i]
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


def submit_alpha(
    client: WorldQuantBrainClient, alpha_id: str, name: str = ""
) -> Tuple[bool, str]:
    result = client.submit_alpha_with_checks(
        alpha_id=alpha_id,
        name=name or None,
    )
    return result.submitted, result.reason


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=int, default=5)
    ap.add_argument("--max-candidates", type=int, default=120)
    ap.add_argument("--max-total", type=int, default=140)
    ap.add_argument("--batch-size", type=int, default=4)
    ap.add_argument("--wait-seconds", type=int, default=320)
    ap.add_argument("--disable-proxy", action="store_true")
    args = ap.parse_args()

    if args.disable_proxy:
        os.environ["WQB_DISABLE_PROXY"] = "1"

    load_dotenv(".env")
    client = WorldQuantBrainClient(os.getenv("WQB_USERNAME", ""), os.getenv("WQB_PASSWORD", ""))
    if not client.authenticate():
        raise RuntimeError("auth failed")

    criteria = Criteria()
    candidates = build_candidates(args.max_candidates)

    settings_grid = [
        {"decay": 6, "neutralization": "SUBINDUSTRY", "truncation": 0.08, "pasteurization": "ON", "nanHandling": "ON"},
        {"decay": 8, "neutralization": "SUBINDUSTRY", "truncation": 0.08, "pasteurization": "ON", "nanHandling": "ON"},
        {"decay": 10, "neutralization": "SUBINDUSTRY", "truncation": 0.08, "pasteurization": "ON", "nanHandling": "ON"},
        {"decay": 8, "neutralization": "INDUSTRY", "truncation": 0.08, "pasteurization": "ON", "nanHandling": "ON"},
    ]

    worklist: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    for cand in candidates:
        for s in settings_grid:
            worklist.append((cand, s))

    run_date = datetime.now().strftime("%Y-%m-%d")
    out_dir = Path(f"docs/strategies/{run_date}")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "usa_combo_tuning_results.json"

    submitted: List[str] = []
    attempts: List[Dict[str, Any]] = []

    print(f"worklist={len(worklist)}", flush=True)

    idx = 0
    while idx < len(worklist) and len(attempts) < args.max_total and len(submitted) < args.target:
        batch = worklist[idx : idx + args.batch_size]
        idx += args.batch_size
        print(f"batch={len(batch)} tried={len(attempts)} submitted={len(submitted)}", flush=True)

        in_flight: List[Tuple[Dict[str, Any], Dict[str, Any], str]] = []
        for cand, settings in batch:
            if len(attempts) + len(in_flight) >= args.max_total:
                break
            url, err = create_sim(client, cand["expression"], settings)
            if url:
                in_flight.append((cand, settings, url))
            else:
                attempts.append(
                    {
                        "candidate": cand,
                        "settings": settings,
                        "simulate_error": err,
                        "timestamp": datetime.now().isoformat(),
                    }
                )
            time.sleep(0.2)

        resolved = resolve_batch(client, in_flight, args.wait_seconds)
        for cand, settings, sim in resolved:
            passed = criteria.check(sim)
            ok = False
            submit_reason = ""
            if passed and sim.alpha_id:
                ok, submit_reason = submit_alpha(
                    client,
                    sim.alpha_id,
                    name=str(cand.get("name", "")).strip(),
                )
                if ok:
                    submitted.append(sim.alpha_id)
            attempts.append(
                {
                    "candidate": cand,
                    "settings": settings,
                    "simulate": asdict(sim),
                    "passed": passed,
                    "submitted": ok,
                    "submit_reason": submit_reason,
                    "timestamp": datetime.now().isoformat(),
                }
            )
            print(
                f"{cand['name']} | d={settings['decay']} n={settings['neutralization']} | "
                f"sh={sim.sharpe:.2f} fit={sim.fitness:.2f} to={sim.turnover:.3f} dd={sim.drawdown:.3f} "
                f"pass={passed} submit={ok}",
                flush=True,
            )

        out_file.write_text(
            json.dumps(
                {
                    "run_date": run_date,
                    "criteria": asdict(criteria),
                    "submitted_ids": submitted,
                    "submitted_count": len(submitted),
                    "attempts": attempts,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    analysis_md = out_dir / "usa_combo_submission_failure_analysis.md"
    analysis_json = out_dir / "usa_combo_submission_failure_summary.json"
    try:
        analysis = generate_submission_failure_report(
            input_path=client.submission_log_path,
            output_md=analysis_md,
            output_json=analysis_json,
        )
        print(f"submission_analysis={analysis['output_md']}", flush=True)
    except Exception as e:
        print(f"submission_analysis_failed: {e}", flush=True)

    print(f"done submitted={len(submitted)} file={out_file}", flush=True)


if __name__ == "__main__":
    main()
