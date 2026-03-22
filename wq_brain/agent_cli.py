"""
CLI for the multi-agent quant runtime.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .agent_runtime import (
    AgentRuntime,
    init_runtime_config,
    runtime_status,
    start_runtime,
    stop_runtime,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

DEFAULT_CONFIG_PATH = Path(".wqa/config.yaml")


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
        print(json.dumps(start_runtime(config_path), ensure_ascii=False, indent=2))
        return

    if args.command == "stop":
        print(json.dumps(stop_runtime(config_path), ensure_ascii=False, indent=2))
        return

    if args.command == "status":
        print(json.dumps(runtime_status(config_path), ensure_ascii=False, indent=2))
        return

    if args.command == "restart":
        print(json.dumps(stop_runtime(config_path), ensure_ascii=False, indent=2))
        print(json.dumps(start_runtime(config_path), ensure_ascii=False, indent=2))
        return

    if args.command == "run-daemon":
        runtime = AgentRuntime(config_path)
        runtime.run_foreground()
        return

    parser.print_help()
    sys.exit(1)


if __name__ == "__main__":
    main()
