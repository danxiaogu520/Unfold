"""Real Agent recovery on a new public synthetic case; scoped process death only."""

from __future__ import annotations

import argparse
import json
import os
import selectors
import subprocess
import sys
import threading
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select, text

from unfold.app.config import AppSettings, load_app_env
from unfold.app.db import Database
from unfold.app.generation import DeepSeekGenerator
from unfold.app.models import ModelCall
from unfold.app.resource_agent import AgentDecision, AgentStep, DeepSeekDecisionModel, ResourceAgent
from unfold.app.resources import Resources
from unfold.app.service import RunService
from unfold.app.worker import Worker
from unfold.eval_resources import public_space
from unfold.provider import Settings, load_env_file

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-env", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--child-run-id")
    args = parser.parse_args()
    load_app_env(ROOT / ".env.app.local")
    load_env_file(args.model_env)
    config = replace(AppSettings.from_env(), model_mode="deepseek", retry_base_seconds=0)
    database = Database(config)
    service = RunService(database, config)
    model = Settings.from_env()
    generator = DeepSeekGenerator(database, service, model, ROOT)
    actor = ResourceAgent(service, DeepSeekDecisionModel(generator))
    if args.child_run_id:
        claim = service.claim(args.child_run_id)
        assert claim and claim.input["content"]["text"].startswith("公开合成评测资料：")
        execute = actor.execute_step

        def stop_after_candidate(current: object, step: AgentStep) -> dict:
            result = execute(current, step)
            command = AgentDecision.model_validate(step.decision)
            if command.tool == "make_component" and "candidate_id" in result:
                print("CHECKPOINT_READY", flush=True)
                threading.Event().wait(30)
            return result

        actor.execute_step = stop_after_candidate
        Worker(service, actor).execute(claim)
        raise SystemExit("required candidate checkpoint was not selected")
    assert args.output
    destination = args.output.resolve()
    if destination.exists():
        raise SystemExit("Never overwrite real recovery evidence")
    destination.mkdir(parents=True)
    case = next(
        row
        for row in json.loads((ROOT / "evals/datasets/resources.v1.json").read_text())["tasks"]
        if row["id"] == "search_component-1"
    )
    space_id, task_id = public_space(service, case, "m5-real-recovery:" + destination.name)
    snapshot = Resources.retry(service, space_id, task_id)
    process = subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--model-env",
            str(args.model_env.resolve()),
            "--child-run-id",
            snapshot.run_id,
        ],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=os.environ.copy(),
    )
    try:
        assert process.stdout
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            if not selector.select(timeout=90):
                raise RuntimeError("real candidate checkpoint was not reached")
            if process.stdout.readline().strip() != "CHECKPOINT_READY":
                raise RuntimeError("unexpected child checkpoint output")
        process.kill()
        process.wait(timeout=5)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
    before = service.trace(snapshot.run_id)
    (destination / "before-recovery.json").write_text(
        json.dumps(before, ensure_ascii=False, indent=2) + "\n"
    )
    with database.transaction() as session:
        session.execute(
            text(
                "UPDATE generation_runs SET lease_until=clock_timestamp()-interval '1 second' "
                "WHERE id=:id"
            ),
            {"id": snapshot.run_id},
        )
    assert service.recover_expired() == 1
    claim = service.claim(snapshot.run_id)
    assert claim and claim.attempt == 2
    Worker(service, actor).execute(claim)
    after = service.trace(snapshot.run_id)
    (destination / "after-recovery.json").write_text(
        json.dumps(after, ensure_ascii=False, indent=2) + "\n"
    )
    saved_numbers = [
        row["number"] for row in before["agent_steps"] if row["status"] == "tool_completed"
    ]
    after_steps = {row["number"]: row for row in after["agent_steps"]}
    assert all(
        after_steps[number] == next(row for row in before["agent_steps"] if row["number"] == number)
        for number in saved_numbers
    )
    assert all(tool["attempt"] == 1 for tool in after["tools"] if tool["step"] in saved_numbers)
    with database.transaction() as session:
        calls = list(
            session.scalars(
                select(ModelCall).where(
                    ModelCall.run_id == snapshot.run_id, ModelCall.provider == "deepseek"
                )
            )
        )
    report = {
        "version": "m5-real-recovery.v1",
        "public_synthetic_data_only": True,
        "run_id": snapshot.run_id,
        "child_sigkill": True,
        "completed_steps_not_replayed": saved_numbers,
        "before_calls": len(before["calls"]),
        "after_calls": len(after["calls"]),
        "real_calls": len(calls),
        "unknown_usage_calls": sum(
            c.input_tokens is None or c.output_tokens is None for c in calls
        ),
        "conservative_cost_cny": str(
            sum(
                (
                    c.accounted_cost if c.accounted_cost is not None else c.reserved_cost
                    for c in calls
                ),
                Decimal(0),
            )
        ),
        "final_status": after["snapshot"]["status"],
        "source_space_id": space_id,
        "assertions_passed": after["snapshot"]["status"] == "succeeded",
        "restoration_integrity_passed": True,
    }
    (destination / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    )
    database.close()
    print(json.dumps(report, ensure_ascii=False))
    if after["snapshot"]["status"] != "succeeded":
        raise SystemExit("recovery integrity retained; final technical task did not succeed")


if __name__ == "__main__":
    main()
