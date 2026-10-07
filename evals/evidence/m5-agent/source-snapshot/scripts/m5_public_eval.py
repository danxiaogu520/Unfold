"""Real comparison over public synthetic fixtures only; never reads existing learning spaces."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import shutil
import time
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import select

from unfold.app.config import AppSettings, load_app_env
from unfold.app.db import Database
from unfold.app.files import FileStore
from unfold.app.generation import DeepSeekGenerator
from unfold.app.migrate import migrate
from unfold.app.models import (
    BudgetLedger,
    GenerationRun,
    ModelCall,
    ResourceCandidate,
)
from unfold.app.resource_agent import DeepSeekDecisionModel, ResourceAgent
from unfold.app.resources import Resources
from unfold.app.service import Claim, RunService, entity_id
from unfold.app.worker import Worker
from unfold.eval_resources import public_space
from unfold.provider import GenerationError, Settings, load_env_file

ROOT = Path(__file__).resolve().parents[1]
CASES = ("search_component-1", "function_plot-1", "python_database-1")
MODES = ("baseline_single", "baseline_fixed", "agent")
MAX_BATCH_CALLS = 36


class BatchLimitedModel(DeepSeekDecisionModel):
    def __init__(self, generator: DeepSeekGenerator, run_ids: list[str]):
        super().__init__(generator, max_calls=8)
        self.run_ids = run_ids

    def decide(self, claim: Claim, number: int, observations: list[dict[str, Any]], mode: str):
        with self.generator.database.transaction() as session:
            calls = list(
                session.scalars(
                    select(ModelCall).where(
                        ModelCall.run_id.in_(self.run_ids), ModelCall.provider == "deepseek"
                    )
                )
            )
        if len(calls) >= MAX_BATCH_CALLS:
            raise GenerationError("budget_exceeded", "predeclared public comparison call limit")
        # No existing learning-space lookup occurs in this provider path.
        assert claim.input["content"]["text"].startswith("公开合成评测资料：")
        return super().decide(claim, number, observations, mode)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model-env", type=Path, required=True)
    args = parser.parse_args()
    destination = args.output.resolve()
    if destination.exists():
        raise SystemExit("Every real comparison is immutable; use a new output directory")
    load_app_env(ROOT / ".env.app.local")
    load_env_file(args.model_env)
    config = replace(AppSettings.from_env(), model_mode="deepseek", max_attempts=1)
    settings = Settings.from_env()
    database = Database(config)
    migrate(database)
    service = RunService(database, config)
    generator = DeepSeekGenerator(database, service, settings, ROOT)
    dataset = json.loads((ROOT / "evals/datasets/resources.v1.json").read_text())
    selected = [row for row in dataset["tasks"] if row["id"] in CASES]
    assert len(selected) == 3 and all(row["split"] == "dev" for row in selected)
    order = [(row, mode) for row in selected for mode in MODES]
    random.Random(20261007).shuffle(order)
    destination.mkdir(parents=True)
    sources = {}
    for source in sorted(
        {
            *ROOT.glob("src/**/*.py"),
            *ROOT.glob("src/**/*.sql"),
            *ROOT.glob("src/**/*.html"),
            *ROOT.glob("web/runtime/*.mjs"),
            ROOT / "evals/datasets/resources.v1.json",
            ROOT / "pyproject.toml",
            ROOT / "uv.lock",
            ROOT / "web/package-lock.json",
            Path(__file__).resolve(),
        }
    ):
        name = str(source.relative_to(ROOT))
        sources[name] = hashlib.sha256(source.read_bytes()).hexdigest()
        target = destination / "source-snapshot" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    report: dict[str, Any] = {
        "version": "m5-public-comparison.v1",
        "started_at": datetime.now(UTC).isoformat(),
        "data_classification": "public_synthetic_evaluation.v1",
        "reads_existing_learning_spaces": False,
        "source_sha256": sources,
        "max_provider_calls": MAX_BATCH_CALLS,
        "max_calls_per_run": 8,
        "max_steps_per_run": 8,
        "max_workflow_seconds": 150,
        "order_frozen_before_calls": [{"case_id": row["id"], "mode": mode} for row, mode in order],
        "model": settings.model,
        "model_snapshot_locked": False,
        "temperature": 0.2,
        "reasoning_effort": "none",
        "independent_human_review": None,
        "trials": [],
    }
    run_ids: list[str] = []

    def save() -> None:
        with database.transaction() as session:
            calls = list(
                session.scalars(
                    select(ModelCall).where(
                        ModelCall.run_id.in_(run_ids), ModelCall.provider == "deepseek"
                    )
                )
            )
            ledger = session.get(BudgetLedger, "model")
            assert ledger
            report["ledger_after_cny"] = str(ledger.accounted_cost)
        report["real_calls"] = len(calls)
        report["unknown_usage_calls"] = sum(
            c.input_tokens is None or c.output_tokens is None for c in calls
        )
        report["conservative_cost_cny"] = str(
            sum(
                (
                    c.accounted_cost if c.accounted_cost is not None else c.reserved_cost
                    for c in calls
                ),
                Decimal(0),
            )
        )
        (destination / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        )

    try:
        save()
        for index, (case, mode) in enumerate(order, 1):
            prefix = f"m5:{destination.name}:{index}"
            space_id, task_id = public_space(service, case, prefix)
            snapshot = Resources.retry(service, space_id, task_id)
            run_ids.append(snapshot.run_id)
            claim = service.claim(snapshot.run_id)
            assert claim
            actor = ResourceAgent(service, BatchLimitedModel(generator, run_ids), mode=mode)
            if case["family"] == "function_plot":
                html = (ROOT / "src/unfold/app/templates/function-plot-broken.html").read_text()
                file = FileStore(config.data_directory).write_web(html)
                with database.transaction() as session:
                    candidate = ResourceCandidate(
                        id=entity_id("candidate"),
                        run_id=claim.run_id,
                        attempt=claim.attempt,
                        file=file.dump(),
                        source={
                            "origin": "public_benchmark_bootstrap",
                            "teaching_suitability": "synthetic",
                        },
                        proposal=actor.proposal(claim),
                    )
                    session.add(candidate)
                    run = session.get(GenerationRun, claim.run_id)
                    assert run
                    context = {
                        **run.context_dependencies["selected_context"],
                        "initial_candidate": {"candidate_id": candidate.id, "html": html},
                    }
                    run.context_dependencies = {
                        **run.context_dependencies,
                        "selected_context": context,
                    }
                claim = replace(claim, context=context)
            began = time.monotonic()
            Worker(service, actor).execute(claim)
            trace = service.trace(claim.run_id)
            (destination / f"{claim.run_id}.json").write_text(
                json.dumps(trace, ensure_ascii=False, indent=2) + "\n"
            )
            expected_exit = case["expected_behavior"] == "unsupported"
            status = trace["snapshot"]["status"]
            acceptable_exit = (
                expected_exit
                and status == "failed"
                and trace["snapshot"]["error"]["code"] == "capability_limit"
            )
            report["trials"].append(
                {
                    "case_id": case["id"],
                    "mode": mode,
                    "run_id": claim.run_id,
                    "space_id": space_id,
                    "status": status,
                    "expected_capability_exit": expected_exit,
                    "acceptable_capability_exit": acceptable_exit,
                    "technical_success": status == "succeeded",
                    "seconds": round(time.monotonic() - began, 3),
                    "model_calls": len(trace["calls"]),
                    "steps": len(trace["agent_steps"]),
                    "tool_failures": sum(t["status"] == "failed" for t in trace["tools"]),
                }
            )
            with database.transaction() as session:
                candidates = list(
                    session.scalars(
                        select(ResourceCandidate).where(ResourceCandidate.run_id == claim.run_id)
                    )
                )
                for candidate in candidates:
                    source = FileStore(config.data_directory).path(candidate.file["relative_path"])
                    shutil.copyfile(source, destination / (candidate.id + ".html"))
            save()
            print(json.dumps(report["trials"][-1], ensure_ascii=False), flush=True)
    finally:
        save()
        database.close()
    print(
        json.dumps(
            {"real_calls": report["real_calls"], "cost_cny": report["conservative_cost_cny"]}
        )
    )


if __name__ == "__main__":
    main()
