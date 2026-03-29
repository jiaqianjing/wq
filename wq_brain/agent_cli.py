"""
CLI for the multi-agent quant runtime.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

from .agent_runtime import (
    AgentRuntime,
    init_runtime_config,
    is_process_alive,
    runtime_status,
    start_runtime,
    stop_runtime,
)

_log_fmt = "%(asctime)s - %(levelname)s - %(name)s - %(message)s"
logging.basicConfig(level=logging.INFO, format=_log_fmt, force=True)

DEFAULT_CONFIG_PATH = Path(".wqa/config.yaml")


def ensure_config_exists(config_path: Path) -> None:
    if config_path.exists():
        return
    raise SystemExit(
        f"Config not found: {config_path}\nRun `uv run wqa --config {config_path} init` first."
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wqa",
        description="WorldQuant multi-agent lab manager",
    )
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help="Path to the runtime config file",
    )
    subparsers = parser.add_subparsers(dest="command")

    init_parser = subparsers.add_parser("init", help="Create a config template")
    init_parser.add_argument("--force", action="store_true", help="Overwrite existing config")

    subparsers.add_parser("start", help="Start the agent runtime in background")
    subparsers.add_parser("stop", help="Stop the agent runtime")
    subparsers.add_parser("status", help="Show daemon and queue status")
    subparsers.add_parser("restart", help="Restart the runtime")
    subparsers.add_parser("sync-knowledge", help="Sync operators and data fields from WorldQuant BRAIN into local knowledge base")
    subparsers.add_parser("account-info", help="Probe WorldQuant account permissions, submission thresholds, and save to knowledge base")
    subparsers.add_parser("run-daemon", help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    config_path = Path(args.config).resolve()

    if args.command == "init":
        created = init_runtime_config(config_path, force=args.force)
        print(f"Created config template at {created}")
        return

    if args.command == "start":
        ensure_config_exists(config_path)
        print(json.dumps(start_runtime(config_path), ensure_ascii=False, indent=2))
        return

    if args.command == "stop":
        ensure_config_exists(config_path)
        print(json.dumps(stop_runtime(config_path), ensure_ascii=False, indent=2))
        return

    if args.command == "status":
        ensure_config_exists(config_path)
        print(json.dumps(runtime_status(config_path), ensure_ascii=False, indent=2))
        return

    if args.command == "restart":
        ensure_config_exists(config_path)
        stop_result = stop_runtime(config_path)
        print(json.dumps(stop_result, ensure_ascii=False, indent=2))
        if stop_result.get("status") == "stop_requested":
            # Process didn't exit in time; wait a bit more before giving up.
            pid = stop_result.get("pid", 0)
            for _ in range(20):
                if not is_process_alive(pid):
                    break
                time.sleep(0.5)
            else:
                print(json.dumps({"status": "error", "message": f"process {pid} still alive after extended wait"}, ensure_ascii=False, indent=2))
                return
        # Brief pause to let the OS release the dashboard port.
        time.sleep(1)
        print(json.dumps(start_runtime(config_path), ensure_ascii=False, indent=2))
        return

    if args.command == "sync-knowledge":
        ensure_config_exists(config_path)
        from .agent_runtime import sync_brain_knowledge
        result = sync_brain_knowledge(config_path)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.command == "account-info":
        ensure_config_exists(config_path)
        from .agent_runtime import sync_account_info
        result = sync_account_info(config_path)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.command == "run-daemon":
        ensure_config_exists(config_path)
        runtime = AgentRuntime(config_path)
        runtime.run_foreground()
        return

    parser.print_help()
    sys.exit(1)


if __name__ == "__main__":
    main()
