#!/usr/bin/env python3
"""
研报驱动的 Power Pool Alpha 批量搜索/回测/提交脚本。

目标：
1) 基于近期研究结论构造候选表达式
2) 批量回测并筛选
3) 自动提交通过阈值的 Alpha
4) 直到达到目标提交数（默认 5）或达到最大测试数
"""

from __future__ import annotations

import argparse
import itertools
import json
import logging
import os
import random
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

from dotenv import load_dotenv

from wq_brain.alpha_generator import AlphaGenerator
from wq_brain.client import SimulateResult, WorldQuantBrainClient


@dataclass
class PowerPoolCriteria:
    min_sharpe: float = 1.58
    min_fitness: float = 1.0
    max_turnover: float = 0.7
    max_drawdown: float = 0.1
    min_returns: float = 0.0

    def check(self, result: SimulateResult) -> bool:
        return (
            result.sharpe >= self.min_sharpe
            and result.fitness >= self.min_fitness
            and result.turnover <= self.max_turnover
            and result.drawdown <= self.max_drawdown
            and result.returns >= self.min_returns
        )


def build_candidates() -> List[Dict[str, Any]]:
    """根据研报结论构造候选表达式。"""
    templates = [
        {
            "name": "max_reversal",
            "theme": "短期反转 x MAX 异象",
            "paper": "SSRn-4949401 / JEF-2025",
            "expression": "-rank(ts_delta(close, {d})) * rank(ts_max(ts_returns(close, 1), {w}))",
            "params": {"d": [1, 3, 5], "w": [10, 20, 40]},
        },
        {
            "name": "max_reversal_52w_high",
            "theme": "短期反转与 52 周高点联动",
            "paper": "RFS-2025 / JEF-2024",
            "expression": "-rank(ts_delta(close, {d})) * rank(ts_max(ts_returns(close, 1), {w1})) * (1 - rank(close / (ts_max(close, {w2}) + 0.001)))",
            "params": {"d": [1, 3], "w1": [20, 40], "w2": [126, 252]},
        },
        {
            "name": "turnover_52w_momentum",
            "theme": "高换手 + 52 周高点动量",
            "paper": "RFS-2025",
            "expression": "rank(ts_delta(close, {d})) * rank(close / (ts_max(close, 252) + 0.001)) * rank(volume / (adv{adv} + 0.001))",
            "params": {"d": [10, 20], "adv": [20, 60, 120]},
        },
        {
            "name": "vol_timed_momentum",
            "theme": "波动率择时动量",
            "paper": "NBER-34104 (2025)",
            "expression": "rank(ts_sum(ts_returns(close, 1), {w1})) * (1 - rank(ts_std(ts_returns(close, 1), {w2}))) * rank(volume / (adv20 + 0.001))",
            "params": {"w1": [5, 10, 20], "w2": [20, 60]},
        },
        {
            "name": "rebalancing_pressure",
            "theme": "再平衡压力反转",
            "paper": "NBER-33861 (2025)",
            "expression": "-rank(ts_delta(close, {d})) * rank(ts_delta(volume, {vd})) * (1 - rank(ts_std(ts_returns(close, 1), {w})))",
            "params": {"d": [1, 3, 5], "vd": [1, 3], "w": [20, 60]},
        },
        {
            "name": "rebalancing_liquidity_shock",
            "theme": "流动性冲击 x 反转",
            "paper": "NBER-33861 (2025)",
            "expression": "-rank(ts_delta(close, {d})) * rank(volume / (ts_mean(volume, {w}) + 0.001))",
            "params": {"d": [1, 3, 5], "w": [20, 60, 120]},
        },
        {
            "name": "cross_market_alpha191",
            "theme": "跨市场价量相关衰减",
            "paper": "arXiv-2601.13112",
            "expression": "-rank(ts_delta(ts_corr(high, volume, {w1}), {d})) * rank(ts_corr(rank(close), rank(volume), {w2}))",
            "params": {"w1": [5, 10], "d": [3, 5], "w2": [60, 120]},
        },
        {
            "name": "price_volume_rank_corr",
            "theme": "价量秩相关",
            "paper": "arXiv-2601.13112",
            "expression": "-ts_rank(ts_corr(rank(close), rank(volume), {w1}), {w2}) * rank(ts_std(ts_returns(close, 1), {w3}))",
            "params": {"w1": [10, 20, 40], "w2": [5, 10], "w3": [20, 60]},
        },
        {
            "name": "vwap_mean_revert_volume",
            "theme": "VWAP 偏离 + 量能过滤",
            "paper": "RFS-2025 / JEF-2024",
            "expression": "rank((vwap - close) / (ts_std(close, {w}) + 0.001)) * rank(volume / (adv20 + 0.001))",
            "params": {"w": [10, 20, 40, 60]},
        },
        {
            "name": "rsi_volume_pressure",
            "theme": "买卖压力与量能",
            "paper": "NBER-33861 (2025)",
            "expression": "rank(ts_sum(max(close - ts_delay(close, 1), 0), {w}) / (ts_sum(abs(close - ts_delay(close, 1)), {w}) + 0.001)) * rank(volume / (adv20 + 0.001))",
            "params": {"w": [10, 14, 20]},
        },
    ]

    candidates: List[Dict[str, Any]] = []
    for spec in templates:
        keys = list(spec["params"].keys())
        values = [spec["params"][k] for k in keys]
        for combo in itertools.product(*values):
            expr = spec["expression"]
            params = {}
            for k, v in zip(keys, combo):
                expr = expr.replace(f"{{{k}}}", str(v))
                params[k] = v
            candidates.append(
                {
                    "name": spec["name"],
                    "theme": spec["theme"],
                    "paper": spec["paper"],
                    "expression": expr,
                    "params": params,
                }
            )

    # 融合 101 变体，提高表达式多样性
    generator = AlphaGenerator()
    for alpha in generator.generate_101_alphas_variations():
        candidates.append(
            {
                "name": alpha["name"],
                "theme": "101_formulaic_alpha",
                "paper": "Alpha101 / 扩展变体",
                "expression": alpha["expression"],
                "params": {},
            }
        )

    # 去重
    unique: Dict[str, Dict[str, Any]] = {}
    for c in candidates:
        unique[c["expression"]] = c
    return list(unique.values())


def build_settings_grid() -> List[Dict[str, Any]]:
    return [
        {
            "region": "USA",
            "universe": "TOP3000",
            "delay": 1,
            "decay": 4,
            "neutralization": "SUBINDUSTRY",
            "truncation": 0.08,
            "pasteurization": "ON",
        },
        {
            "region": "USA",
            "universe": "TOP3000",
            "delay": 1,
            "decay": 6,
            "neutralization": "INDUSTRY",
            "truncation": 0.08,
            "pasteurization": "ON",
        },
        {
            "region": "GLB",
            "universe": "TOP3000",
            "delay": 1,
            "decay": 4,
            "neutralization": "INDUSTRY",
            "truncation": 0.08,
            "pasteurization": "ON",
        },
        {
            "region": "GLB",
            "universe": "TOPDIV3000",
            "delay": 1,
            "decay": 6,
            "neutralization": "SUBINDUSTRY",
            "truncation": 0.08,
            "pasteurization": "ON",
        },
        {
            "region": "GLB",
            "universe": "TOPDIV3000",
            "delay": 1,
            "decay": 8,
            "neutralization": "INDUSTRY",
            "truncation": 0.06,
            "pasteurization": "ON",
        },
    ]


def create_simulation(
    client: WorldQuantBrainClient,
    expression: str,
    settings: Dict[str, Any],
) -> Tuple[str, str]:
    payload = {
        "type": "REGULAR",
        "settings": {
            "instrumentType": "EQUITY",
            "region": settings["region"],
            "universe": settings["universe"],
            "delay": settings["delay"],
            "decay": settings["decay"],
            "neutralization": settings["neutralization"],
            "truncation": settings["truncation"],
            "pasteurization": settings["pasteurization"],
            "testPeriod": "P0Y",
            "unitHandling": "VERIFY",
            "nanHandling": "ON",
            "language": "FASTEXPR",
            "visualization": False,
        },
        "regular": expression,
    }

    for _ in range(3):
        resp = client._request(
            "post", f"{client.BASE_URL}/simulations", json=payload, timeout=30
        )
        if resp.status_code == 201:
            location = resp.headers.get("Location", "")
            return location, ""
        if resp.status_code == 429:
            time.sleep(5)
            continue
        text = resp.text[:400]
        return "", f"HTTP {resp.status_code}: {text}"

    return "", "HTTP 429: rate limited"


def submit_with_details(client: WorldQuantBrainClient, alpha_id: str) -> Tuple[bool, str]:
    for _ in range(2):
        resp = client._request(
            "post", f"{client.BASE_URL}/alphas/{alpha_id}/submit", timeout=30
        )
        if resp.status_code in (200, 201):
            return True, "submitted"
        if resp.status_code == 429:
            time.sleep(5)
            continue

        reason = f"HTTP {resp.status_code}"
        try:
            data = resp.json()
            checks = data.get("is", {}).get("checks", [])
            failed = [c.get("name") for c in checks if c.get("result") == "FAIL"]
            pending = [c.get("name") for c in checks if c.get("result") == "PENDING"]
            chunks = []
            if failed:
                chunks.append(f"FAIL={','.join(failed)}")
            if pending:
                chunks.append(f"PENDING={','.join(pending)}")
            if chunks:
                reason = "; ".join(chunks)
            elif isinstance(data, dict):
                reason = data.get("message", reason)
        except Exception:
            if resp.text:
                reason = resp.text[:200]
        return False, reason

    return False, "HTTP 429: rate limited"


def resolve_batch_results(
    client: WorldQuantBrainClient,
    in_flight: List[Tuple[Dict[str, Any], str]],
    max_wait: int,
) -> List[Tuple[Dict[str, Any], SimulateResult]]:
    """
    并发轮询一批 simulation，避免逐条阻塞等待。
    """
    pending: Dict[int, Tuple[Dict[str, Any], str]] = {
        idx: item for idx, item in enumerate(in_flight)
    }
    resolved: List[Tuple[Dict[str, Any], SimulateResult]] = []
    deadline = time.time() + max_wait

    while pending and time.time() < deadline:
        for idx in list(pending.keys()):
            candidate, progress_url = pending[idx]
            try:
                resp = client._request("get", progress_url, timeout=20)
            except Exception:
                continue

            if resp.status_code != 200:
                continue

            retry_after = resp.headers.get("Retry-After")
            if retry_after:
                continue

            data = resp.json()
            alpha_id = data.get("alpha", "")
            if alpha_id:
                result = client._get_alpha_result(alpha_id)
                resolved.append((candidate, result))
                pending.pop(idx, None)
                continue

            status = str(data.get("status", "")).upper()
            if status in {"FAIL", "FAILED", "ERROR"}:
                resolved.append(
                    (
                        candidate,
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
                            error_message="simulation failed",
                        ),
                    )
                )
                pending.pop(idx, None)

        if pending:
            time.sleep(3)

    for idx in list(pending.keys()):
        candidate, _ = pending[idx]
        resolved.append(
            (
                candidate,
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Power Pool research run")
    parser.add_argument("--target", type=int, default=5, help="目标提交数")
    parser.add_argument("--batch-size", type=int, default=18, help="每批模拟数量")
    parser.add_argument("--max-total", type=int, default=180, help="最大模拟总数")
    parser.add_argument("--seed", type=int, default=20260214, help="随机种子")
    parser.add_argument("--wait-seconds", type=int, default=90, help="单个回测最长等待秒数")
    parser.add_argument("--results-dir", default="", help="结果输出目录")
    parser.add_argument("--disable-proxy", action="store_true", help="禁用系统代理")
    args = parser.parse_args()

    if args.disable_proxy:
        os.environ["WQB_DISABLE_PROXY"] = "1"

    load_dotenv(".env")
    username = os.getenv("WQB_USERNAME")
    password = os.getenv("WQB_PASSWORD")
    if not username or not password:
        raise RuntimeError("缺少 WQB_USERNAME/WQB_PASSWORD")

    random.seed(args.seed)

    run_date = datetime.now().strftime("%Y-%m-%d")
    output_dir = Path(args.results_dir) if args.results_dir else Path(f"docs/strategies/{run_date}")
    output_dir.mkdir(parents=True, exist_ok=True)

    results_path = output_dir / "power_pool_research_results.json"

    client = WorldQuantBrainClient(username, password)
    logging.getLogger("wq_brain.client").setLevel(logging.WARNING)
    if not client.authenticate():
        raise RuntimeError("WorldQuant Brain 认证失败")

    criteria = PowerPoolCriteria()
    candidates = build_candidates()
    random.shuffle(candidates)
    settings_grid = build_settings_grid()

    attempts: List[Dict[str, Any]] = []
    submitted_ids: List[str] = []
    total_simulated = 0

    print(f"候选表达式: {len(candidates)}", flush=True)
    print(f"设置组合: {len(settings_grid)}", flush=True)

    for settings_idx, settings in enumerate(settings_grid, 1):
        if len(submitted_ids) >= args.target or total_simulated >= args.max_total:
            break

        print("\n" + "=" * 80, flush=True)
        print(f"设置 {settings_idx}/{len(settings_grid)}: {settings}", flush=True)
        print("=" * 80, flush=True)

        local_candidates = candidates[:]
        random.shuffle(local_candidates)
        cursor = 0

        while (
            len(submitted_ids) < args.target
            and total_simulated < args.max_total
            and cursor < len(local_candidates)
        ):
            batch = local_candidates[cursor : cursor + args.batch_size]
            cursor += args.batch_size
            if not batch:
                break

            print(
                f"启动批次: size={len(batch)} simulated={total_simulated} submitted={len(submitted_ids)}",
                flush=True,
            )
            in_flight: List[Tuple[Dict[str, Any], str]] = []
            for c in batch:
                if total_simulated >= args.max_total:
                    break
                progress_url, error = create_simulation(client, c["expression"], settings)
                total_simulated += 1
                if progress_url:
                    in_flight.append((c, progress_url))
                else:
                    print(f"simulate_failed: {c['name']} | {error}", flush=True)
                    attempts.append(
                        {
                            "candidate": c,
                            "settings": settings,
                            "simulate_error": error,
                            "timestamp": datetime.now().isoformat(),
                        }
                )
                time.sleep(0.25)

            print(f"批次已创建: in_flight={len(in_flight)}", flush=True)
            batch_results = resolve_batch_results(
                client=client,
                in_flight=in_flight,
                max_wait=args.wait_seconds,
            )

            for c, result in batch_results:
                passed = criteria.check(result)
                submitted = False
                submit_reason = ""
                if passed and result.alpha_id:
                    submitted, submit_reason = submit_with_details(client, result.alpha_id)
                    if submitted:
                        submitted_ids.append(result.alpha_id)

                attempts.append(
                    {
                        "candidate": c,
                        "settings": settings,
                        "simulate": asdict(result),
                        "passed": passed,
                        "submitted": submitted,
                        "submit_reason": submit_reason,
                        "timestamp": datetime.now().isoformat(),
                    }
                )

                print(
                    f"[{len(attempts):03d}] {c['name']} | sh={result.sharpe:.2f} fit={result.fitness:.2f} "
                    f"to={result.turnover:.3f} dd={result.drawdown:.3f} | "
                    f"pass={passed} submit={submitted}"
                , flush=True)

                if len(submitted_ids) >= args.target:
                    break

            payload = {
                "run_date": run_date,
                "criteria": asdict(criteria),
                "target": args.target,
                "submitted_ids": submitted_ids,
                "total_simulated": total_simulated,
                "settings_grid": settings_grid,
                "attempts": attempts,
            }
            results_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

            if len(submitted_ids) >= args.target:
                break

    final = {
        "run_date": run_date,
        "criteria": asdict(criteria),
        "target": args.target,
        "submitted_ids": submitted_ids,
        "submitted_count": len(submitted_ids),
        "total_simulated": total_simulated,
        "settings_grid": settings_grid,
        "attempts": attempts,
    }
    results_path.write_text(json.dumps(final, indent=2), encoding="utf-8")

    print("\n" + "=" * 80, flush=True)
    print("完成", flush=True)
    print(f"总回测: {total_simulated}", flush=True)
    print(f"有效提交: {len(submitted_ids)} / {args.target}", flush=True)
    print(f"结果文件: {results_path}", flush=True)


if __name__ == "__main__":
    main()
