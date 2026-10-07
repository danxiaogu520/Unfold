"""Real domain-only entry over a NEW public synthetic space, at most six calls."""

import argparse
import hashlib
import json
import shutil
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

from unfold.api_contract import CreateRun
from unfold.app.config import AppSettings, load_app_env
from unfold.app.db import Database
from unfold.app.generation import DeepSeekGenerator
from unfold.app.learning import IntakeAction
from unfold.app.migrate import migrate
from unfold.app.models import BudgetLedger, ModelCall
from unfold.app.service import Claim, RunService
from unfold.app.worker import Worker
from unfold.contracts import Answer
from unfold.provider import GenerationError, Settings, load_env_file

ROOT = Path(__file__).resolve().parents[1]


class LimitedGenerator(DeepSeekGenerator):
    def __init__(self, *args, run_ids: list[str], space_id: str):
        super().__init__(*args)
        self.run_ids, self.public_space_id = run_ids, space_id

    def reserve_call(self, claim: Claim, step: int, body: dict, **kwargs) -> str:
        assert self.service.trace(claim.run_id)["request"]["space_id"] == self.public_space_id
        with self.database.transaction() as session:
            calls = list(
                session.scalars(
                    select(ModelCall.id).where(
                        ModelCall.run_id.in_(self.run_ids), ModelCall.provider == "deepseek"
                    )
                )
            )
        if len(calls) >= 6:
            raise GenerationError("budget_exceeded", "public domain-entry call cap")
        return super().reserve_call(claim, step, body, **kwargs)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model-env", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("Every paid experiment requires a new output directory")
    load_app_env(ROOT / ".env.app.local")
    load_env_file(args.model_env)
    config = replace(AppSettings.from_env(), model_mode="deepseek", max_attempts=1)
    database = Database(config)
    migrate(database)
    service = RunService(database, config)
    args.output.mkdir(parents=True)
    sources = {}
    for source in sorted(
        {
            *ROOT.glob("src/**/*.py"),
            *ROOT.glob("src/**/*.sql"),
            *ROOT.glob("evals/prompts/*.json"),
            Path(__file__).resolve(),
        }
    ):
        name = str(source.relative_to(ROOT))
        sources[name] = hashlib.sha256(source.read_bytes()).hexdigest()
        target = args.output / "source-snapshot" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    space = service.create_space("公开合成入口验收：HTTP/1.1 基础", "")
    run_ids: list[str] = []
    generator = LimitedGenerator(
        database, service, Settings.from_env(), ROOT, run_ids=run_ids, space_id=space["id"]
    )
    worker = Worker(service, generator)
    report = {
        "version": "m7-public-domain-entry.v1",
        "data_classification": "public_synthetic_evaluation.v1",
        "reads_existing_learning_spaces": False,
        "source_sha256": sources,
        "prompt_version": generator.prompts["version"],
        "max_provider_calls": 6,
        "all_answers_unknown": True,
        "independent_human_review": None,
        "stages": [],
        "technical_success": False,
    }

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
        (args.output / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        )

    def execute(run_id: str) -> bool:
        run_ids.append(run_id)
        claim = service.claim(run_id)
        assert claim
        worker.execute(claim)
        trace = service.trace(run_id)
        (args.output / (run_id + ".json")).write_text(
            json.dumps(trace, ensure_ascii=False, indent=2) + "\n"
        )
        report["stages"].append(
            {
                "operation": claim.operation,
                "status": trace["snapshot"]["status"],
                "run_id": run_id,
                "error": trace["snapshot"]["error"],
            }
        )
        save()
        return trace["snapshot"]["status"] == "succeeded"

    try:
        save()  # Freeze sources/protocol before the first call.
        initial = service.create_run(
            CreateRun(
                schema_version="api.v1",
                space_id=space["id"],
                operation="questionnaire",
                profile_revision=None,
                base_revision=None,
                input={
                    "schema_version": "questionnaire-input.v2",
                    "domain": space["domain"],
                    "user_brief": "",
                    "known_targets": [],
                    "previous_question_keys": [],
                    "questions_asked": 0,
                    "round": 1,
                },
            ),
            "public-domain-entry",
        )
        if not execute(initial.run_id):
            return
        intake = service.get_space(space["id"])["intakes"][0]
        profile = service.create_intake_action(
            space["id"],
            IntakeAction(
                operation="profile",
                expected_intake_id=intake["id"],
                answers=[
                    Answer(question_key=q["key"], selected_keys=[], free_text=None, unknown=True)
                    for q in intake["questionnaire"]["questions"]
                ],
            ),
            "public-unknown-answers",
        )
        if not execute(profile.run_id):
            return
        saved = service.get_space(space["id"])
        assert saved["profile"]["primary_purpose"]["source"] != "user"
        report["primary_purpose"] = saved["profile"]["primary_purpose"]
        outline = next(r for r in saved["runs"] if r["operation"] == "outline")
        if not execute(outline["run_id"]):
            return
        saved = service.get_space(space["id"])
        report["technical_success"] = bool(saved["root_id"])
        report["formal_nodes"] = len(saved["nodes"])
    finally:
        save()
        database.close()
    print(json.dumps({k: v for k, v in report.items() if k not in ("source_sha256", "stages")}))


if __name__ == "__main__":
    main()
