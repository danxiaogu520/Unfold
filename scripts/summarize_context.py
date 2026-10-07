"""Recompute context metrics from frozen trials without inference."""

import argparse
import json
import math
import statistics
from decimal import Decimal
from pathlib import Path
from typing import Any


def metrics(report: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "model": report["model"],
        "total": {
            k: report[k]
            for k in (
                "real_calls",
                "conservative_cost_cny",
                "input_tokens",
                "output_tokens",
                "unknown_usage_calls",
                "ledger_after_cny",
            )
        },
        "strategies": {},
    }
    for strategy in ("all_history", "current_path", "structural_relevance"):
        cells = [row for row in report["trials"] if row["strategy"] == strategy]
        paid = [row for row in cells if row.get("run_id")]
        valid = [row for row in paid if row["technical_success"]]
        times = sorted(row["seconds"] for row in paid)
        result["strategies"][strategy] = {
            "cells": len(cells),
            "evaluated": len(paid),
            "candidate_valid": len(valid),
            "first_valid": sum(row["first_call_valid"] for row in paid),
            "pre_call_overflow": sum(row["status"] == "not_started" for row in cells),
            "real_calls": sum(row["real_calls"] for row in cells),
            "mean_input_tokens_including_repairs": round(
                statistics.mean(row["input_tokens"] for row in paid), 2
            ),
            "mean_selected_input_bytes": round(
                statistics.mean(row["selection"]["input_bytes"] for row in paid), 2
            ),
            "median_full_seconds": statistics.median(times),
            "p95_full_seconds_nearest_rank": times[math.ceil(len(times) * 0.95) - 1],
            "cost_cny": str(sum((Decimal(row["cost_cny"]) for row in paid), Decimal(0))),
            "mean_lexical_coverage_proxy": round(
                statistics.mean(row["quality"]["lexical_coverage_proxy"] for row in valid), 4
            ),
            "duplicate_blocks": sum(row["quality"]["duplicate_blocks"] for row in valid),
            "unexpected_resources": sum(row["quality"]["unexpected_resources"] for row in valid),
        }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--check", type=Path)
    args = parser.parse_args()
    result = metrics(json.loads(args.report.read_text()))
    if args.check:
        assert result == json.loads(args.check.read_text())
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
