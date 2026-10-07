"""Frozen heldout comparison over newly created PUBLIC synthetic inputs only."""

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
from unfold.app.context import STRATEGIES, basis, extract, select_context, terms
from unfold.app.db import Database
from unfold.app.generation import DeepSeekGenerator, FixtureGenerator, Generator
from unfold.app.learning import LearningAction
from unfold.app.migrate import migrate
from unfold.app.models import BudgetLedger, GenerationRun, ModelCall
from unfold.app.service import Claim, RunService
from unfold.app.worker import Worker
from unfold.eval_context import SIZES, new_public_learning_space, public_snapshot
from unfold.provider import GenerationError, Settings, load_env_file

ROOT = Path(__file__).resolve().parents[1]
MAX_CALLS = 144  # 8 heldout cases * 3 sizes * 3 strategies * (initial + one repair)


def dump(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


class CandidateOnly:
    """Use actual durable calls/heartbeats, then cancel before formal publication."""

    def __init__(self, generator: Generator, service: RunService, run_ids: list[str]):
        self.generator, self.service, self.run_ids = generator, service, run_ids
        self.candidate: dict[str, Any] | None = None

    def generate(self, claim: Claim) -> dict[str, Any]:
        with self.service.database.transaction() as session:
            count = len(
                list(
                    session.scalars(
                        select(ModelCall.id).where(
                            ModelCall.run_id.in_(self.run_ids), ModelCall.provider == "deepseek"
                        )
                    )
                )
            )
        if count >= MAX_CALLS - 1:
            raise GenerationError("budget_exceeded", "predeclared public evaluation call cap")
        self.candidate = self.generator.generate(claim)
        self.service.cancel(claim.run_id)
        return self.candidate


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model-env", type=Path, required=True)
    parser.add_argument(
        "--dry-run", action="store_true", help="Use deterministic fake, zero inference"
    )
    args = parser.parse_args()
    destination = args.output.resolve()
    if destination.exists():
        raise SystemExit("Do not replace an existing real evaluation")
    load_app_env(ROOT / ".env.app.local")
    load_env_file(args.model_env)
    config = replace(AppSettings.from_env(), model_mode="deepseek", max_attempts=1)
    settings = Settings.from_env()
    database = Database(config)
    migrate(database)
    service = RunService(database, config)
    generator: Generator = (
        FixtureGenerator(database, ROOT)
        if args.dry_run
        else DeepSeekGenerator(database, service, settings, ROOT)
    )
    cases = [
        row
        for row in json.loads((ROOT / "evals/datasets/learning.v1.json").read_text())["scenarios"]
        if row["split"] == "holdout"
    ]
    assert len(cases) == 8
    destination.mkdir(parents=True)
    sources = {}
    for source in sorted(
        {
            *ROOT.glob("src/**/*.py"),
            *ROOT.glob("src/**/*.sql"),
            *ROOT.glob("evals/prompts/*.json"),
            ROOT / "evals/datasets/learning.v1.json",
            ROOT / "pyproject.toml",
            ROOT / "uv.lock",
            Path(__file__).resolve(),
        }
    ):
        name = str(source.relative_to(ROOT))
        sources[name] = hashlib.sha256(source.read_bytes()).hexdigest()
        target = destination / "source-snapshot" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    prepared = {}
    # All scale snapshots and inputs are frozen BEFORE any real inference.
    for case in cases:
        space_id = new_public_learning_space(service, case, destination.name + ":" + case["id"])
        node_id = service.get_space(space_id)["root_id"]
        first = service.create_action(
            space_id,
            LearningAction(
                operation="explanation",
                target_id=node_id,
                expected_profile_revision=1,
                expected_base_revision=1,
                instruction=case["user_brief"] + "；只用文字讲解，不制作资源。",
            ),
            "prepare:" + case["id"],
        )
        with database.transaction() as session:
            run = session.get(GenerationRun, first.run_id)
            assert run
            request = run.input_payload
            base = run.context_dependencies["selected_context"]
            base = {
                k: v for k, v in base.items() if k not in ("context_pages", "context_relations")
            }
        service.cancel(first.run_id)
        for label, size in SIZES.items():
            snapshot = public_snapshot(request, size)
            name = case["id"] + ":" + label
            frozen = {
                "case": case,
                "space_id": space_id,
                "request": request,
                "base": base,
                "snapshot": snapshot,
            }
            prepared[name] = frozen
            dump(destination / (name.replace(":", "-") + ".snapshot.json"), frozen)
    order = [(name, strategy) for name in prepared for strategy in STRATEGIES]
    random.Random(20261007).shuffle(order)
    report: dict[str, Any] = {
        "version": "m6-public-context-comparison.v1",
        "started_at": datetime.now(UTC).isoformat(),
        "data_classification": "public_synthetic_evaluation.v1",
        "reads_existing_learning_spaces": False,
        "candidate_only": True,
        "formal_results_published": 0,
        "source_sha256": sources,
        "model": "fixture.v2" if args.dry_run else settings.model,
        "real_model_calls_enabled": not args.dry_run,
        "model_snapshot_locked": False,
        "temperature": 0.2,
        "replications_per_cell": 1,
        "max_provider_calls": MAX_CALLS,
        "input_budget_bytes": 24000,
        "output_tokens_reserved": 4000,
        "quality_protocol": {
            "contract": "existing local input/output and domain validation",
            "lexical_coverage": "domain term intersection / domain terms; proxy only",
            "duplicate_blocks": "identical nonempty block text repeated",
            "unexpected_resources": "any resource_requests despite frozen text-only instruction",
            "independent_human_review": None,
            "fact_accuracy": None,
        },
        "order_frozen_before_calls": [{"snapshot": n, "strategy": s} for n, s in order],
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
        report["input_tokens"] = sum(c.input_tokens or 0 for c in calls)
        report["output_tokens"] = sum(c.output_tokens or 0 for c in calls)
        report["conservative_cost_cny"] = str(
            sum(
                (
                    c.accounted_cost if c.accounted_cost is not None else c.reserved_cost
                    for c in calls
                ),
                Decimal(0),
            )
        )
        dump(destination / "report.json", report)

    try:
        save()
        for index, (name, strategy) in enumerate(order):
            frozen = prepared[name]
            began = time.monotonic()
            row: dict[str, Any] = {
                "snapshot": name,
                "strategy": strategy,
                "technical_success": False,
                "quality": None,
                "real_calls": 0,
            }
            try:
                data, request = frozen["snapshot"], frozen["request"]
                versions = {p["key"]: p["content_revision"] for p in request["path"]}
                summaries = {p["id"]: extract(p, basis(p, 1, 1, versions)) for p in data["pages"]}
                chosen = select_context(
                    request,
                    frozen["base"],
                    data["pages"],
                    data["relations"],
                    data["profile_history"],
                    strategy,
                    summaries=summaries,
                )
                row["selection"] = chosen.log
                action = LearningAction(
                    operation="explanation",
                    target_id=request["target"]["key"],
                    expected_profile_revision=1,
                    expected_base_revision=1,
                    instruction=request["instruction"],
                )
                queued = service.create_action(
                    frozen["space_id"], action, f"trial:{destination.name}:{index}"
                )
                with database.transaction() as session:
                    run = session.get(GenerationRun, queued.run_id)
                    assert run and run.input_payload == request
                    run.context_dependencies = {
                        **run.context_dependencies,
                        "selected_context": chosen.material,
                        "evaluation_selection": chosen.log,
                        "evaluation_snapshot": name,
                        "candidate_only": True,
                    }
                claim = service.claim(queued.run_id)
                assert claim
                run_ids.append(claim.run_id)
                actor = CandidateOnly(generator, service, run_ids)
                Worker(service, actor).execute(claim)
                trace = service.trace(claim.run_id)
                dump(destination / (claim.run_id + ".json"), trace)
                calls = [c for c in trace["calls"] if c["provider"] == "deepseek"]
                row.update(
                    run_id=claim.run_id,
                    status=trace["snapshot"]["status"],
                    real_calls=len(calls),
                    input_tokens=sum(c["input_tokens"] or 0 for c in calls),
                    output_tokens=sum(c["output_tokens"] or 0 for c in calls),
                    cost_cny=str(
                        sum(
                            (
                                Decimal(
                                    c["accounted_cost"]
                                    if c["accounted_cost"] is not None
                                    else c["reserved_cost"]
                                )
                                for c in calls
                            ),
                            Decimal(0),
                        )
                    ),
                    first_call_valid=bool(calls) and calls[0]["error_code"] is None,
                    error=trace["snapshot"]["error"],
                )
                if actor.candidate:
                    candidate = actor.candidate
                    dump(destination / (claim.run_id + ".candidate.json"), candidate)
                    row["technical_success"] = True
                    texts = [b.get("text", b.get("fallback_text", "")) for b in candidate["blocks"]]
                    words = terms(frozen["case"]["domain"])
                    row["quality"] = {
                        "lexical_coverage_proxy": len(words & terms(" ".join(texts)))
                        / max(1, len(words)),
                        "duplicate_blocks": len(texts) - len(set(texts)),
                        "unexpected_resources": len(candidate["resource_requests"]),
                        "fact_accuracy": None,
                        "independent_human_review": None,
                    }
            except GenerationError as exc:
                row["error"] = {"code": exc.code, "message": str(exc)}
                row["status"] = "not_started"  # selection overflow is not a paid inference failure
            row["seconds"] = round(time.monotonic() - began, 3)
            report["trials"].append(row)
            save()
            print(
                json.dumps({k: v for k, v in row.items() if k != "selection"}, ensure_ascii=False),
                flush=True,
            )
    finally:
        save()
        database.close()


if __name__ == "__main__":
    main()
