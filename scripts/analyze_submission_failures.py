#!/usr/bin/env python3
"""Analyze submission failure logs and generate repair reports."""

from __future__ import annotations

import argparse

from wq_brain.submission_failure_analyzer import generate_submission_failure_report


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze submission failure logs")
    parser.add_argument(
        "--input",
        default="results/submission_checks.jsonl",
        help="submission checks jsonl path",
    )
    parser.add_argument(
        "--output-md",
        default="",
        help="markdown report output path",
    )
    parser.add_argument(
        "--output-json",
        default="",
        help="optional summary json output path",
    )
    args = parser.parse_args()

    result = generate_submission_failure_report(
        input_path=args.input,
        output_md=args.output_md or None,
        output_json=args.output_json or None,
    )
    summary = result["summary"]
    print(f"[ok] markdown report: {result['output_md']}")
    if result["output_json"]:
        print(f"[ok] json summary: {result['output_json']}")
    print(
        f"[summary] total={summary['total_records']} failed={summary['failed_records']} "
        f"submitted={summary['submitted_records']}"
    )


if __name__ == "__main__":
    main()
