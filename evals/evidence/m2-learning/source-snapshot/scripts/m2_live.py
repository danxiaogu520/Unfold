"""Budgeted real HTTP/service/worker learning smoke; only fixed development cases."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select

from unfold.app.api import create_app
from unfold.app.config import AppSettings, load_app_env
from unfold.app.db import Database
from unfold.app.generation import DeepSeekGenerator
from unfold.app.migrate import migrate
from unfold.app.models import BudgetLedger, ModelCall
from unfold.app.security import sanitized
from unfold.app.worker import Worker
from unfold.provider import Settings, load_env_file

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-env", type=Path, required=True)
    parser.add_argument("--app-env", type=Path, default=ROOT / ".env.app.local")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    destination = args.output.resolve()
    if destination.exists():
        raise SystemExit("Use a new evidence directory; real attempts are never overwritten")
    load_app_env(args.app_env)
    load_env_file(args.model_env)
    model = Settings.from_env()
    config = replace(AppSettings.from_env(), model_mode="deepseek")
    database = Database(config)
    migrate(database)
    app = create_app(config, database)
    service = app.state.service
    generator = DeepSeekGenerator(database, service, model, ROOT)
    worker = Worker(service, generator)
    destination.mkdir(parents=True)
    paths = [
        *ROOT.glob("src/**/*.py"),
        *ROOT.glob("src/**/*.sql"),
        *ROOT.glob("evals/prompts/*.json"),
        *ROOT.glob("evals/datasets/*.json"),
        ROOT / "pyproject.toml",
        ROOT / "uv.lock",
        Path(__file__).resolve(),
    ]
    sources = {}
    for path in sorted(set(paths)):
        relative = str(path.relative_to(ROOT))
        sources[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        target = destination / "source-snapshot" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    rows = json.loads((ROOT / "evals/datasets/learning.v1.json").read_text())["scenarios"]
    selected = [row for row in rows if row["id"] in {"backend-1", "midpoint-1"}]
    if {row["id"] for row in selected} != {"backend-1", "midpoint-1"}:
        raise SystemExit("required development scenarios missing")
    report: dict[str, Any] = {
        "evidence_kind": "real_deepseek_persistent_learning",
        "started_at": datetime.now(UTC).isoformat(),
        "source_sha256": sources,
        "selection_frozen_before_calls": [row["id"] for row in selected],
        "repetitions": 1,
        "max_runs_per_case": 7,
        "max_provider_calls_total": 28,
        "max_repairs": model.max_repairs,
        "model": model.model,
        "prompt_version": generator.prompts["version"],
        "fee_limit_persistent_remaining_cny": str(model.max_cost),
        "temperature": 0.2,
        "reasoning_effort": "none",
        "independent_human_review": None,
        "fact_verification": "pending; structural success does not imply correct knowledge",
        "model_snapshot_locked": False,
        "max_output_tokens_per_call": model.max_output_tokens,
        "cases": [],
    }

    def save() -> None:
        (destination / "report.json").write_text(
            json.dumps(sanitized(report, (model.api_key,)), ensure_ascii=False, indent=2) + "\n"
        )

    save()
    try:
        with TestClient(app) as client:
            for row in selected:
                assert row["split"] == "dev"
                case: dict[str, Any] = {"scenario_id": row["id"], "success": False, "runs": []}
                report["cases"].append(case)
                saved = client.post(
                    "/api/v1/spaces",
                    json={"domain": row["domain"], "user_brief": row["user_brief"]},
                )
                assert saved.status_code == 201
                space = saved.json()
                case["space_id"] = space["id"]

                def execute(
                    path: str,
                    body: dict[str, Any],
                    case: dict[str, Any] = case,
                    scenario_id: str = row["id"],
                    space_id: str = space["id"],
                ) -> dict[str, Any]:
                    if len(case["runs"]) >= report["max_runs_per_case"]:
                        raise RuntimeError("case run limit reached")
                    response = client.post(
                        path,
                        json=body,
                        headers={"Idempotency-Key": f"m2-{scenario_id}-{len(case['runs'])}"},
                    )
                    if response.status_code != 202:
                        case["rejected_request"] = response.json()
                        save()
                        raise RuntimeError("application rejected learning request")
                    run = response.json()
                    case["runs"].append(run["run_id"])
                    save()
                    claim = service.claim(run["run_id"])
                    if claim is None:
                        raise RuntimeError("expected run has no available execution slot")
                    worker.execute(claim)
                    trace = service.trace(run["run_id"])
                    (destination / (run["run_id"] + ".json")).write_text(
                        json.dumps(sanitized(trace, (model.api_key,)), ensure_ascii=False, indent=2)
                        + "\n"
                    )
                    state = service.get_space(space_id)
                    if trace["snapshot"]["status"] != "succeeded":
                        case["error"] = trace["snapshot"]["error"]
                        raise RuntimeError("learning run did not succeed")
                    return state

                try:
                    state = execute(
                        "/api/v1/runs",
                        {
                            "schema_version": "api.v1",
                            "space_id": space["id"],
                            "operation": "questionnaire",
                            "profile_revision": None,
                            "base_revision": None,
                            "input": {
                                "schema_version": "questionnaire-input.v1",
                                "domain": row["domain"],
                                "user_brief": row["user_brief"],
                                "known_targets": row["known_targets"],
                                "previous_question_keys": [],
                                "questions_asked": 0,
                                "round": 1,
                            },
                        },
                    )
                    intake = state["intakes"][-1]
                    answers = []
                    for question in intake["questionnaire"]["questions"]:
                        text = row["answers_by_target"].get(question["target"])
                        answers.append(
                            {
                                "question_key": question["key"],
                                "selected_keys": [],
                                "free_text": text,
                                "unknown": text is None,
                            }
                        )
                    state = execute(
                        f"/api/v1/spaces/{space['id']}/intake",
                        {
                            "operation": "profile",
                            "expected_intake_id": intake["id"],
                            "answers": answers,
                        },
                    )
                    state = execute(
                        f"/api/v1/spaces/{space['id']}/actions",
                        {"operation": "outline", "expected_profile_revision": 1},
                    )
                    root = next(node for node in state["nodes"] if node["id"] == state["root_id"])
                    if row["expected_behavior"] == "root_leaf":
                        if root["decision"]["state"] != "leaf" or root["children"]:
                            raise RuntimeError("narrow scenario did not stop at root")
                        blocked = client.post(
                            f"/api/v1/spaces/{space['id']}/actions",
                            json={
                                "operation": "expansion",
                                "expected_profile_revision": 1,
                                "expected_base_revision": root["revision"],
                                "target_id": root["id"],
                            },
                            headers={"Idempotency-Key": "forbidden-leaf-expansion"},
                        )
                        assert blocked.status_code == 409
                        case["leaf_api_rejected"] = True
                        target = root
                    else:
                        branch = next(
                            node
                            for node in state["nodes"]
                            if node["parent_id"] == root["id"]
                            and node["decision"]["state"] == "expandable"
                        )
                        state = execute(
                            f"/api/v1/spaces/{space['id']}/actions",
                            {
                                "operation": "expansion",
                                "expected_profile_revision": 1,
                                "expected_base_revision": branch["revision"],
                                "target_id": branch["id"],
                            },
                        )
                        target = next(
                            node for node in state["nodes"] if node["parent_id"] == branch["id"]
                        )
                    state = execute(
                        f"/api/v1/spaces/{space['id']}/actions",
                        {
                            "operation": "explanation",
                            "expected_profile_revision": 1,
                            "expected_base_revision": target["revision"],
                            "target_id": target["id"],
                            "instruction": "结合原始用途解释，给出准确的简单例子；"
                            "只使用当前支持的正文块，不申请网页或图片资源。",
                        },
                    )
                    restored = client.get(f"/api/v1/spaces/{space['id']}").json()
                    assert restored["nodes"] == state["nodes"]
                    case.update(
                        success=True,
                        saved_node_count=len(state["nodes"]),
                        restored_without_generation=True,
                    )
                    (destination / (row["id"] + ".space.json")).write_text(
                        json.dumps(sanitized(state, (model.api_key,)), ensure_ascii=False, indent=2)
                        + "\n"
                    )
                except (RuntimeError, StopIteration) as exc:
                    case["failure_reason"] = str(sanitized(str(exc), (model.api_key,)))
                save()
                print(
                    json.dumps(
                        {
                            "scenario": row["id"],
                            "success": case["success"],
                            "runs": len(case["runs"]),
                        }
                    ),
                    flush=True,
                )
    finally:
        with database.transaction() as session:
            ids = [run_id for case in report["cases"] for run_id in case["runs"]]
            calls = list(session.scalars(select(ModelCall).where(ModelCall.run_id.in_(ids))))
            ledger = session.get(BudgetLedger, "model")
            report.update(
                real_calls=len(calls),
                input_tokens=sum(call.input_tokens or 0 for call in calls)
                if all(call.input_tokens is not None for call in calls)
                else None,
                output_tokens=sum(call.output_tokens or 0 for call in calls)
                if all(call.output_tokens is not None for call in calls)
                else None,
                unknown_usage_calls=sum(
                    call.input_tokens is None or call.output_tokens is None for call in calls
                ),
                conservative_cost_cny=str(
                    sum(
                        call.accounted_cost
                        if call.accounted_cost is not None
                        else call.reserved_cost
                        for call in calls
                    )
                ),
                persistent_reserved_and_settled_cny=str(ledger.accounted_cost) if ledger else "0",
            )
        save()
        database.close()
    if not all(case["success"] for case in report["cases"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
