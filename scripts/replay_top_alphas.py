#!/usr/bin/env python3
"""从账户历史高分 Alpha 回放到当前窗口，并自动提交达标结果。"""

from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

from dotenv import load_dotenv

from wq_brain.client import SimulateResult, WorldQuantBrainClient


@dataclass
class Criteria:
    min_sharpe: float = 1.58
    min_fitness: float = 1.0
    max_turnover: float = 0.7
    min_turnover: float = 0.01
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


def fetch_top_candidates(
    client: WorldQuantBrainClient, max_fetch: int, region_filter: str
) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    offset = 0
    while len(candidates) < max_fetch:
        resp = client._request(
            "get",
            f"{client.BASE_URL}/users/self/alphas",
            params={
                "status": "UNSUBMITTED",
                "type": "REGULAR",
                "order": "-is.sharpe",
                "limit": 100,
                "offset": offset,
            },
            timeout=30,
        )
        if resp.status_code != 200:
            break
        data = resp.json()
        results = data.get("results", [])
        if not results:
            break

        for item in results:
            settings = item.get("settings", {})
            stats = item.get("is", {})
            if region_filter and settings.get("region") != region_filter:
                continue
            if settings.get("delay") != 1:
                continue
            code = item.get("regular", {})
            expression = code.get("code") if isinstance(code, dict) else code
            if not expression:
                continue

            candidates.append(
                {
                    "source_alpha_id": item.get("id"),
                    "expression": expression,
                    "settings": {
                        "region": settings.get("region", "USA"),
                        "universe": settings.get("universe", "TOP3000"),
                        "delay": settings.get("delay", 1),
                        "decay": settings.get("decay", 0),
                        "neutralization": settings.get("neutralization", "SUBINDUSTRY"),
                        "truncation": settings.get("truncation", 0.08),
                        "pasteurization": settings.get("pasteurization", "ON"),
                        "nanHandling": settings.get("nanHandling", "ON"),
                    },
                    "historical": {
                        "sharpe": stats.get("sharpe", 0),
                        "fitness": stats.get("fitness", 0),
                        "turnover": stats.get("turnover", 0),
                        "drawdown": stats.get("drawdown", 0),
                        "returns": stats.get("returns", 0),
                        "dateCreated": item.get("dateCreated", ""),
                    },
                }
            )
            if len(candidates) >= max_fetch:
                break

        offset += len(results)

    # 历史指标优先去重
    dedup: Dict[str, Dict[str, Any]] = {}
    for c in candidates:
        k = c["expression"]
        if k not in dedup:
            dedup[k] = c
    return list(dedup.values())


def create_simulation(client: WorldQuantBrainClient, candidate: Dict[str, Any]) -> Tuple[str, str]:
    s = candidate["settings"]
    payload = {
        "type": "REGULAR",
        "settings": {
            "instrumentType": "EQUITY",
            "region": s["region"],
            "universe": s["universe"],
            "delay": s["delay"],
            "decay": s["decay"],
            "neutralization": s["neutralization"],
            "truncation": s["truncation"],
            "pasteurization": s["pasteurization"],
            "testPeriod": "P0Y",
            "unitHandling": "VERIFY",
            "nanHandling": s["nanHandling"],
            "language": "FASTEXPR",
            "visualization": False,
        },
        "regular": candidate["expression"],
    }

    for _ in range(3):
        resp = client._request("post", f"{client.BASE_URL}/simulations", json=payload, timeout=30)
        if resp.status_code == 201:
            return resp.headers.get("Location", ""), ""
        if resp.status_code == 429:
            time.sleep(5)
            continue
        return "", f"HTTP {resp.status_code}: {resp.text[:300]}"
    return "", "HTTP 429: rate limited"


def resolve_batch(
    client: WorldQuantBrainClient,
    in_flight: List[Tuple[Dict[str, Any], str]],
    max_wait: int,
) -> List[Tuple[Dict[str, Any], SimulateResult]]:
    pending = {i: x for i, x in enumerate(in_flight)}
    resolved: List[Tuple[Dict[str, Any], SimulateResult]] = []
    deadline = time.time() + max_wait

    while pending and time.time() < deadline:
        for i in list(pending.keys()):
            c, url = pending[i]
            try:
                resp = client._request("get", url, timeout=20)
            except Exception:
                continue
            if resp.status_code != 200:
                continue
            if resp.headers.get("Retry-After"):
                continue

            data = resp.json()
            alpha_id = data.get("alpha", "")
            if alpha_id:
                r = client._get_alpha_result(alpha_id)
                resolved.append((c, r))
                pending.pop(i, None)
                continue

            status = str(data.get("status", "")).upper()
            if status in {"ERROR", "FAIL", "FAILED"}:
                resolved.append(
                    (
                        c,
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
                pending.pop(i, None)

        if pending:
            time.sleep(3)

    for i in list(pending.keys()):
        c, _ = pending[i]
        resolved.append(
            (
                c,
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


def submit_with_details(client: WorldQuantBrainClient, alpha_id: str) -> Tuple[bool, str]:
    resp = client._request("post", f"{client.BASE_URL}/alphas/{alpha_id}/submit", timeout=30)
    if resp.status_code in (200, 201):
        return True, "submitted"
    reason = f"HTTP {resp.status_code}"
    try:
        data = resp.json()
        checks = data.get("is", {}).get("checks", [])
        fails = [x.get("name") for x in checks if x.get("result") == "FAIL"]
        pend = [x.get("name") for x in checks if x.get("result") == "PENDING"]
        if fails or pend:
            chunks = []
            if fails:
                chunks.append("FAIL=" + ",".join(fails))
            if pend:
                chunks.append("PENDING=" + ",".join(pend))
            reason = "; ".join(chunks)
    except Exception:
        pass
    return False, reason


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--target", type=int, default=5)
    p.add_argument("--batch-size", type=int, default=15)
    p.add_argument("--max-fetch", type=int, default=250)
    p.add_argument("--max-total", type=int, default=120)
    p.add_argument("--wait-seconds", type=int, default=300)
    p.add_argument("--region", default="USA")
    p.add_argument("--disable-proxy", action="store_true")
    args = p.parse_args()

    if args.disable_proxy:
        os.environ["WQB_DISABLE_PROXY"] = "1"

    load_dotenv(".env")
    client = WorldQuantBrainClient(os.getenv("WQB_USERNAME", ""), os.getenv("WQB_PASSWORD", ""))
    if not client.authenticate():
        raise RuntimeError("auth failed")

    run_date = datetime.now().strftime("%Y-%m-%d")
    out_dir = Path(f"docs/strategies/{run_date}")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "replay_top_alphas_results.json"

    criteria = Criteria()
    candidates = fetch_top_candidates(client, args.max_fetch, args.region)
    candidates.sort(key=lambda x: (x["historical"]["sharpe"], x["historical"]["fitness"]), reverse=True)

    attempts: List[Dict[str, Any]] = []
    submitted: List[str] = []

    print(f"candidates={len(candidates)}", flush=True)

    cursor = 0
    while cursor < len(candidates) and len(submitted) < args.target and len(attempts) < args.max_total:
        batch = candidates[cursor : cursor + args.batch_size]
        cursor += args.batch_size
        print(f"batch size={len(batch)} tried={len(attempts)} submitted={len(submitted)}", flush=True)

        in_flight = []
        for c in batch:
            if len(attempts) + len(in_flight) >= args.max_total:
                break
            url, err = create_simulation(client, c)
            if url:
                in_flight.append((c, url))
            else:
                attempts.append({"candidate": c, "simulate_error": err, "timestamp": datetime.now().isoformat()})
            time.sleep(0.2)

        resolved = resolve_batch(client, in_flight, args.wait_seconds)
        for c, r in resolved:
            passed = criteria.check(r)
            ok = False
            reason = ""
            if passed and r.alpha_id:
                ok, reason = submit_with_details(client, r.alpha_id)
                if ok:
                    submitted.append(r.alpha_id)
            attempts.append(
                {
                    "candidate": c,
                    "simulate": asdict(r),
                    "passed": passed,
                    "submitted": ok,
                    "submit_reason": reason,
                    "timestamp": datetime.now().isoformat(),
                }
            )
            print(
                f"{c['source_alpha_id']} => sh={r.sharpe:.2f} fit={r.fitness:.2f} to={r.turnover:.3f} dd={r.drawdown:.3f} pass={passed} submit={ok}",
                flush=True,
            )
            if len(submitted) >= args.target:
                break

        out_path.write_text(
            json.dumps(
                {
                    "run_date": run_date,
                    "criteria": asdict(criteria),
                    "submitted": submitted,
                    "submitted_count": len(submitted),
                    "attempts": attempts,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    print(f"done submitted={len(submitted)} file={out_path}", flush=True)


if __name__ == "__main__":
    main()
