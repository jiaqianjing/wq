#!/usr/bin/env python3
"""USA Regular Alpha: 研报驱动候选 + 自动回测提交，直到达到目标提交数。"""

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


@dataclass
class Criteria:
    min_sharpe: float = 1.25
    min_fitness: float = 0.7
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
    """根据最新研报构建可交易表达式池（USA, D1）。"""

    # 研究主题映射
    # 1) RFS 2025: short-term reversal -> momentum transition
    reversal_core = [
        "rank(-ts_delta(close, 1))",
        "rank(-ts_delta(close, 3))",
        "rank(-ts_delta(close, 5))",
    ]

    # 2) NBER Trading Volume Alpha: volume predictability & cost-aware trading
    volume_core = [
        "rank(volume / (adv20 + 0.001))",
        "rank(volume / (adv60 + 0.001))",
        "rank(ts_delta(volume, 1))",
    ]

    # 3) RFS 2025 Day/Night style (open-close, gap proxies)
    intraday_core = [
        "rank((close - open) / (open + 0.001))",
        "rank((open - ts_delay(close, 1)) / (ts_delay(close, 1) + 0.001))",
    ]

    # 4) 2025-2026 宏观/预期风险相关文献 -> 用可用基本面质量/周期敏感代理
    fundamental_core = [
        "group_rank((fnd6_acox)/assets, subindustry)",
        "group_rank((fnd6_drlt)/revenue, subindustry)",
        "group_rank((fnd6_intc)/cap, subindustry)",
        "group_rank((fnd6_acdo)/cap, subindustry)",
        "group_rank(ts_rank(fnd6_acdo, 60), subindustry)",
        "group_rank((fnd6_esopct)/cap, subindustry)",
    ]

    candidates: List[Dict[str, Any]] = []

    # 基本面 baseline（历史上在 USA 上稳）
    for f in fundamental_core:
        candidates.append({
            "name": "baseline_fundamental",
            "expression": f,
            "theme": "fundamental_quality",
        })

    # 技术 + 成交量
    for r, v in itertools.product(reversal_core, volume_core):
        candidates.append({
            "name": "reversal_x_volume",
            "expression": f"rank(({r}) * ({v}))",
            "theme": "reversal_volume",
        })
        candidates.append({
            "name": "lin_reversal_volume",
            "expression": f"rank(0.6*({r}) + 0.4*({v}))",
            "theme": "reversal_volume",
        })

    # 技术 + 基本面（这类在你账号中命中率高）
    for r, f, w in itertools.product(reversal_core, fundamental_core, [0.35, 0.45, 0.55, 0.65]):
        candidates.append({
            "name": "lin_rev_fund",
            "expression": f"rank(({w}*({r})) + ({1-w}*({f})))",
            "theme": "reversal_fundamental",
        })

    # 日内/隔夜代理 + 基本面
    for i, f, w in itertools.product(intraday_core, fundamental_core, [0.4, 0.5, 0.6]):
        candidates.append({
            "name": "lin_intraday_fund",
            "expression": f"rank(({w}*({i})) + ({1-w}*({f})))",
            "theme": "day_night_fundamental",
        })

    # 去重 + 打散
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
        return "", f"HTTP {resp.status_code}: {resp.text[:200]}"

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
                result = client._get_alpha_result(alpha_id)
                resolved.append((cand, settings, result))
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


def submit_with_reason(client: WorldQuantBrainClient, alpha_id: str) -> Tuple[bool, str]:
    resp = client._request("post", f"{client.BASE_URL}/alphas/{alpha_id}/submit", timeout=30)
    if resp.status_code in (200, 201):
        return True, "submitted"

    reason = f"HTTP {resp.status_code}"
    try:
        data = resp.json()
        checks = data.get("is", {}).get("checks", [])
        failed = [x.get("name") for x in checks if x.get("result") == "FAIL"]
        pending = [x.get("name") for x in checks if x.get("result") == "PENDING"]
        parts = []
        if failed:
            parts.append("FAIL=" + ",".join(failed))
        if pending:
            parts.append("PENDING=" + ",".join(pending))
        if parts:
            reason = "; ".join(parts)
    except Exception:
        if resp.text:
            reason = resp.text[:300]

    return False, reason


def retry_pending_submissions(
    client: WorldQuantBrainClient,
    queue: List[Dict[str, Any]],
    attempts: List[Dict[str, Any]],
    submitted_ids: List[str],
) -> List[Dict[str, Any]]:
    """仅针对 pending checks 重试一轮提交。"""
    if not queue:
        return queue

    next_queue: List[Dict[str, Any]] = []
    for item in queue:
        alpha_id = item["alpha_id"]
        ok, reason = submit_with_reason(client, alpha_id)
        attempts.append(
            {
                "candidate": item["candidate"],
                "settings": item["settings"],
                "simulate": item["simulate"],
                "passed": True,
                "submitted": ok,
                "submit_reason": reason,
                "retry": True,
                "timestamp": datetime.now().isoformat(),
            }
        )
        if ok:
            if alpha_id not in submitted_ids:
                submitted_ids.append(alpha_id)
        else:
            if "PENDING=" in reason:
                next_queue.append(item)

    return next_queue


def main() -> None:
    parser = argparse.ArgumentParser(description="USA Regular research tuning")
    parser.add_argument("--target", type=int, default=5)
    parser.add_argument("--max-candidates", type=int, default=120)
    parser.add_argument("--max-total", type=int, default=180)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--wait-seconds", type=int, default=320)
    parser.add_argument("--seed", type=int, default=20260214)
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
    ]

    worklist: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    for c in candidates:
        for s in settings_grid:
            worklist.append((c, s))

    run_date = datetime.now().strftime("%Y-%m-%d")
    out_dir = Path(f"docs/strategies/{run_date}")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "usa_regular_research_results.json"

    attempts: List[Dict[str, Any]] = []
    submitted_ids: List[str] = []
    pending_submit_queue: List[Dict[str, Any]] = []

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
            time.sleep(0.2)

        resolved = resolve_batch(client, in_flight, args.wait_seconds)
        for cand, settings, sim in resolved:
            passed = criteria.check(sim)
            submitted = False
            submit_reason = ""

            if passed and sim.alpha_id:
                submitted, submit_reason = submit_with_reason(client, sim.alpha_id)
                if submitted:
                    submitted_ids.append(sim.alpha_id)
                elif "PENDING=" in submit_reason and "FAIL=" not in submit_reason:
                    pending_submit_queue.append(
                        {
                            "alpha_id": sim.alpha_id,
                            "candidate": cand,
                            "settings": settings,
                            "simulate": asdict(sim),
                        }
                    )

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

        # 每批后对 pending checks 重试一轮
        if pending_submit_queue and len(submitted_ids) < args.target:
            time.sleep(2)
            pending_submit_queue = retry_pending_submissions(
                client=client,
                queue=pending_submit_queue,
                attempts=attempts,
                submitted_ids=submitted_ids,
            )

        out_path.write_text(
            json.dumps(
                {
                    "run_date": run_date,
                    "criteria": asdict(criteria),
                    "submitted_ids": submitted_ids,
                    "submitted_count": len(submitted_ids),
                    "pending_submit_queue": pending_submit_queue,
                    "attempts": attempts,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    print(f"done submitted={len(submitted_ids)} file={out_path}", flush=True)


if __name__ == "__main__":
    main()
