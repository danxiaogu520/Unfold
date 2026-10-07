"""Budgeted M4 calls against the retained real narrow/HTTP learning nodes."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
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
from unfold.app.resources import ImageExecutor, Resources
from unfold.app.security import sanitized
from unfold.app.service import Claim
from unfold.app.worker import Worker
from unfold.provider import Settings, load_env_file

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--model-env", required=True, type=Path)
    args = parser.parse_args()
    destination = args.output.resolve()
    if destination.exists():
        raise SystemExit("Real experiment records are never overwritten")
    load_app_env(ROOT / ".env.app.local")
    load_env_file(args.model_env)
    config = replace(AppSettings.from_env(), model_mode="deepseek", max_attempts=1)
    model = Settings.from_env()
    database = Database(config)
    migrate(database)
    app = create_app(config, database)
    service = app.state.service
    generator = DeepSeekGenerator(database, service, model, ROOT)
    worker = Worker(service, generator)
    report: dict[str, Any] = {
        "version": "m4-live.v1",
        "started_at": datetime.now(UTC).isoformat(),
        "model": model.model,
        "max_model_tasks": 3,
        "max_repairs": 1,
        "max_provider_calls": 6,
        "max_output_tokens": model.max_output_tokens,
        "max_attempts": 1,
        "independent_human_review": None,
        "image_suitability": "unverified",
        "cases": [],
        "image_runs": [],
    }
    destination.mkdir(parents=True)
    sources = {}
    for path in sorted(
        {
            *ROOT.glob("src/**/*.py"),
            *ROOT.glob("src/**/*.sql"),
            *ROOT.glob("evals/prompts/*.json"),
            ROOT / "pyproject.toml",
            ROOT / "uv.lock",
            Path(__file__).resolve(),
        }
    ):
        name = str(path.relative_to(ROOT))
        sources[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        target = destination / "source-snapshot" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    report["source_sha256"] = sources
    existing = json.loads(
        (ROOT / "evals/evidence/m2-learning/m2-persistent-v2/report.json").read_text()
    )
    narrow_space = next(
        row["space_id"] for row in existing["cases"] if row["scenario_id"] == "midpoint-1"
    )
    http_trace = json.loads(
        (
            ROOT
            / "evals/evidence/m2-learning/m2-review-v2/run_8d8a750ca12f49c1ad678a2ae0752d92.json"
        ).read_text()
    )
    http_space, http_node = http_trace["request"]["space_id"], http_trace["input"]["target"]["key"]
    narrow_node = service.get_space(narrow_space)["root_id"]
    report["selection_frozen_before_calls"] = {
        "narrow": [narrow_space, narrow_node],
        "http": [http_space, http_node],
    }
    runs: list[str] = []
    with database.transaction() as session:
        before = session.get(BudgetLedger, "model")
        assert before
        report["ledger_before_cny"] = str(before.accounted_cost)

    def save_report() -> None:
        calls = []
        with database.transaction() as session:
            calls = list(session.scalars(select(ModelCall).where(ModelCall.run_id.in_(runs))))
            ledger = session.get(BudgetLedger, "model")
            assert ledger
            report["ledger_after_cny"] = str(ledger.accounted_cost)
            report["ledger_limit_cny"] = str(ledger.limit_cost)
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
        assert len(calls) <= 6
        (destination / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        )

    try:
        save_report()
        with TestClient(app) as client:

            def invoke(
                space_id: str,
                node_id: str,
                operation: str,
                instruction: str = "结合所提供正文生成三题，至少一题单选、一题多选，包含边界辨析。",
            ) -> bool:
                space = service.get_space(space_id)
                node = service.get_node(space_id, node_id)
                payload = {
                    "operation": operation,
                    "expected_profile_revision": space["profile_revision"],
                    "expected_base_revision": node["revision"],
                    "target_id": node_id,
                    "instruction": instruction,
                    "count": 3,
                }
                response = client.post(
                    f"/api/v1/spaces/{space_id}/actions",
                    json=payload,
                    headers={
                        "Idempotency-Key": "m4:"
                        + destination.name
                        + ":"
                        + operation
                        + ":"
                        + node_id
                    },
                )
                response.raise_for_status()
                run_id = response.json()["run_id"]
                runs.append(run_id)
                claim = service.claim(run_id)
                assert isinstance(claim, Claim)
                worker.execute(claim)
                trace = sanitized(service.trace(run_id), (model.api_key,))
                (destination / f"{run_id}.json").write_text(
                    json.dumps(trace, ensure_ascii=False, indent=2) + "\n"
                )
                saved = service.get_node(space_id, node_id)
                (destination / f"{operation}-{node_id}.json").write_text(
                    json.dumps(saved, ensure_ascii=False, indent=2) + "\n"
                )
                report["cases"].append(
                    {
                        "operation": operation,
                        "space_id": space_id,
                        "node_id": node_id,
                        "run_id": run_id,
                        "status": trace["snapshot"]["status"],
                    }
                )
                save_report()
                return trace["snapshot"]["status"] == "succeeded"

            instruction = (
                "保留当前窄范围：二分查找中点公式为什么写成 l+(r-l)//2；"
                "说明整数溢出条件和 Python 的区别。"
                "用 Markdown、LaTeX 和 Python 代码，并给出一个简洁 Mermaid 流程图。"
                "还请提出一项 image 请求说明普通二分查找区间缩小，"
                "specification 必须是短检索词 binary search；"
                "图片仅辅助看流程，溢出解释以正文为准，保留文字替代。"
            )
            if invoke(narrow_space, narrow_node, "explanation", instruction):
                node = service.get_node(narrow_space, narrow_node)
                for item in node["contents"][0]["resources"]:
                    snapshot = Resources.retry(service, narrow_space, item["id"])
                    claim = service.claim(snapshot.run_id)
                    if claim:
                        image_worker = Worker(service, ImageExecutor(service))
                        image_worker.execute(claim)
                    trace = service.trace(snapshot.run_id)
                    report["image_runs"].append(
                        {"run_id": snapshot.run_id, "status": trace["snapshot"]["status"]}
                    )
                    (destination / f"{snapshot.run_id}.json").write_text(
                        json.dumps(trace, ensure_ascii=False, indent=2) + "\n"
                    )
                invoke(narrow_space, narrow_node, "exercises")
            invoke(http_space, http_node, "exercises")
            report["final_narrow_node"] = service.get_node(narrow_space, narrow_node)
            report["final_http_node"] = service.get_node(http_space, http_node)
    finally:
        save_report()
        database.close()
    print(
        json.dumps(
            {
                "real_calls": report["real_calls"],
                "cost_cny": report["conservative_cost_cny"],
                "cases": report["cases"],
                "images": report["image_runs"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
