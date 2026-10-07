"""Copy reviewed, synthetic-input M0 traces into reproducible evidence.

No network calls; excludes account balances, configuration and Authorization.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from decimal import Decimal
from pathlib import Path
from typing import Any

from unfold.evaluation import fingerprint, read_json, write_json

ROOT = Path(__file__).resolve().parents[1]
NAMES = ["m0-baseline-v1", "m0-baseline-v2", "m0-baseline-v3", "m0-cross-domain-v3"]


def main() -> None:
    secret = next(
        line.split("=", 1)[1]
        for line in (ROOT / ".env.local").read_text().splitlines()
        if line.startswith("DEEPSEEK_API_KEY=")
    )
    summaries = []
    all_calls = []
    for name in NAMES:
        run = ROOT / "evals/runs" / name
        manifest = read_json(run / "manifest.json")
        assert fingerprint(run / "source-snapshot") == manifest["source_sha256"]
        calls = [json.loads(line) for line in (run / "calls.jsonl").read_text().splitlines()]
        all_calls.extend(calls)
        summary = read_json(run / "summary.json")
        tasks = read_json(run / "tasks.json")
        # v1's historical runner saved only chain tasks; retain that raw file and
        # derive probe counts from call records rather than rewriting history.
        chains = [task for task in tasks if "scenario_id" in task]
        probes = {call["task_id"] for call in calls if call["task_id"].startswith("probe-")}
        summary.update(
            {
                "batch": name,
                "prompt_version": manifest["prompt_version"],
                "learning_tasks": len(chains),
                "successful_learning_tasks": sum(task["success"] for task in chains),
                "stage_probe_tasks": len(probes),
                "successful_stage_probe_tasks": sum(
                    any(c["domain_valid"] for c in calls if c["task_id"] == task_id)
                    for task_id in probes
                ),
            }
        )
        summaries.append(summary)
        destination = ROOT / "evals/evidence" / name
        for file in [
            "manifest.json",
            "selection.json",
            "calls.jsonl",
            "tasks.json",
            "summary.json",
            "compatibility.json",
        ]:
            source = run / file
            if not source.exists():
                continue
            text = source.read_text()
            if secret in text or '"Authorization"' in text or '"api_key"' in text:
                raise ValueError("sensitive data in evidence candidate: " + file)
            destination.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination / file)
        shutil.copytree(
            run / "source-snapshot", destination / "source-snapshot", dirs_exist_ok=True
        )
    normal = [call for call in all_calls if not call.get("intentional_probe")]
    total_peak_cost = sum(
        (Decimal(row["accounted_peak_cost_with_unknown_reserves"]) for row in summaries), Decimal(0)
    )
    aggregate: dict[str, Any] = {
        "evidence_kind": "real_provider",
        "batches": summaries,
        "calls": len(all_calls),
        "normal_calls": len(normal),
        "intentional_probe_calls": len(all_calls) - len(normal),
        "learning_tasks": sum(row["learning_tasks"] for row in summaries),
        "successful_learning_tasks": sum(row["successful_learning_tasks"] for row in summaries),
        "stage_probe_tasks": sum(row["stage_probe_tasks"] for row in summaries),
        "successful_stage_probe_tasks": sum(
            row["successful_stage_probe_tasks"] for row in summaries
        ),
        "input_tokens_known": sum(row["input_tokens_known"] for row in summaries),
        "output_tokens_known": sum(row["output_tokens_known"] for row in summaries),
        "unknown_usage_calls": sum(row["unknown_usage_calls"] for row in summaries),
        "peak_cache_miss_cost_estimate_cny": str(total_peak_cost),
        "budget_cny": "5",
        "remaining_conservative_budget_cny": str(Decimal(5) - total_peak_cost),
        "billing_observation": read_json(ROOT / "evals/runs/billing-receipt.json"),
        "actual_per_call_billed_cost": None,
        "independent_human_quality_scores": None,
        "holdout_results": None,
    }
    assert total_peak_cost < Decimal(5)
    write_json(ROOT / "evals/evidence/m0-summary.json", aggregate)
    # Content digest index permits detecting changed historical evidence.
    index = {
        str(path.relative_to(ROOT / "evals/evidence")): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in sorted((ROOT / "evals/evidence").rglob("*"))
        if path.is_file() and path.name != "sha256.json"
    }
    write_json(ROOT / "evals/evidence/sha256.json", index)
    write_json(
        ROOT / "evals/runs/budget-state.json",
        {
            "authorized_cny": "5",
            "accounted_peak_cost_cny": str(total_peak_cost),
            "remaining_cny": str(Decimal(5) - total_peak_cost),
            "calls": len(all_calls),
        },
    )
    config = ROOT / ".env.local"
    config.write_text(
        "\n".join(
            "UNFOLD_MAX_COST=" + str(Decimal(5) - total_peak_cost)
            if line.startswith("UNFOLD_MAX_COST=")
            else line
            for line in config.read_text().splitlines()
        )
        + "\n"
    )
    print(
        json.dumps(
            {k: value for k, value in aggregate.items() if k != "batches"},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
