"""Revalidate a retained real response after fixing identity/path-title separation.

This is recorded-response replay, never a new live inference or quality trial.
"""

from __future__ import annotations

import argparse
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import select

from unfold.app.config import AppSettings, load_app_env
from unfold.app.db import Database
from unfold.app.generation import validate_candidate_context
from unfold.app.learning import LearningAction
from unfold.app.migrate import migrate
from unfold.app.models import ModelCall
from unfold.app.service import Claim, RunService, entity_id
from unfold.app.worker import Worker
from unfold.contracts import validate_pair

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("Use a fresh replay evidence directory")
    report = json.loads((args.reference / "report.json").read_text())
    previous = json.loads((args.reference / (report["runs"][-1] + ".json")).read_text())
    recorded = previous["calls"][-1]
    candidate = json.loads(recorded["response"]["output_text"])
    load_app_env(ROOT / ".env.app.local")
    database = Database(AppSettings.from_env())
    migrate(database)
    service = RunService(database, AppSettings.from_env())
    args.output.mkdir(parents=True)

    class Recorded:
        def generate(self, claim: Claim) -> dict[str, Any]:
            if claim.input != previous["input"]:
                raise ValueError(
                    "recorded response input differs from current authoritative snapshot"
                )
            proposal = validate_pair("outline", claim.input, candidate)
            validate_candidate_context(proposal, claim.context)
            with database.transaction() as session:
                session.add(
                    ModelCall(
                        id=entity_id("call"),
                        run_id=claim.run_id,
                        attempt=claim.attempt,
                        step=1,
                        provider="recorded_replay",
                        model=recorded["model"],
                        prompt_version=recorded["prompt_version"],
                        schema_version="outline.v1",
                        status="completed",
                        request=claim.input,
                        response={
                            "recorded_call_id": recorded["id"],
                            "recorded_run_id": previous["snapshot"]["run_id"],
                            "candidate": candidate,
                            "live_inference": False,
                        },
                        input_tokens=None,
                        output_tokens=None,
                        reserved_cost=Decimal(0),
                        accounted_cost=Decimal(0),
                        currency="CNY",
                        latency_seconds=Decimal(0),
                        error_code=None,
                        outcome_unknown=False,
                    )
                )
            return candidate

    try:
        space_id = previous["request"]["space_id"]
        state = service.get_space(space_id)
        run = service.create_action(
            space_id,
            LearningAction(
                operation="outline",
                expected_profile_revision=2,
                expected_base_revision=state["outline_revision"],
                regenerate=True,
            ),
            "m2-recorded-purpose-preview",
        )
        claim = service.claim(run.run_id)
        if not claim:
            raise SystemExit("replay run has no available execution slot")
        Worker(service, Recorded()).execute(claim)
        trace = service.trace(run.run_id)
        if (
            trace["snapshot"]["status"] != "succeeded"
            or trace["snapshot"]["result_kind"] != "structure_preview"
        ):
            raise SystemExit("recorded replay failed")
        after = service.get_space(space_id)
        assert (
            after["root_id"] == state["root_id"]
            and after["outline_revision"] == state["outline_revision"]
        )
        for name, value in {
            "replay.json": trace,
            "space.json": after,
            "report.json": {
                "evidence_kind": "retained_real_response_replay",
                "new_live_model_calls": 0,
                "cost_cny": "0",
                "current_structure_preserved": True,
                "original_failed_run": previous["snapshot"]["run_id"],
                "replay_run": run.run_id,
                "semantic_human_review": None,
            },
        }.items():
            (args.output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
        with database.transaction() as session:
            assert (
                session.scalar(select(ModelCall).where(ModelCall.run_id == run.run_id)).provider
                == "recorded_replay"
            )
        print(json.dumps({"replay": "succeeded", "new_live_calls": 0}))
    finally:
        database.close()


if __name__ == "__main__":
    main()
