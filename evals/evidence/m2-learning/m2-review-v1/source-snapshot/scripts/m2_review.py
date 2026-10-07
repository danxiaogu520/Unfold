"""Fixed development follow-up: reported HTTP issue and same-topic goal change."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from dataclasses import replace
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
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    destination = args.output.resolve()
    if destination.exists():
        raise SystemExit("Use a fresh evidence directory")
    reference = json.loads((args.reference / "backend-1.space.json").read_text())
    load_app_env(ROOT / ".env.app.local")
    load_env_file(args.model_env)
    settings = Settings.from_env()
    database = Database(replace(AppSettings.from_env(), model_mode="deepseek"))
    migrate(database)
    app = create_app(replace(AppSettings.from_env(), model_mode="deepseek"), database)
    service = app.state.service
    generator = DeepSeekGenerator(database, service, settings, ROOT)
    worker = Worker(service, generator)
    report: dict[str, Any] = {
        "evidence_kind": "real_deepseek_synthetic_goal_change_and_issue_fix",
        "plan_frozen_before_calls": [
            "correct HTTP/1.1 scope/framing/field-combination and JSON lengths",
            "change same backend topic from recruitment to building a small service",
            "generate replacement outline as preview; do not adopt automatically",
        ],
        "maximum_runs": 2,
        "maximum_provider_calls": 4,
        "runs": [],
        "independent_human_review": None,
        "model": settings.model,
        "prompt_version": generator.prompts["version"],
        "source_sha256": {},
    }
    destination.mkdir(parents=True)
    for path in [
        *ROOT.glob("src/**/*.py"),
        *ROOT.glob("src/**/*.sql"),
        *ROOT.glob("evals/prompts/*.json"),
        ROOT / "uv.lock",
        ROOT / "pyproject.toml",
        Path(__file__).resolve(),
    ]:
        relative = str(path.relative_to(ROOT))
        report["source_sha256"][relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        target = destination / "source-snapshot" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)

    def save(name: str, value: object) -> None:
        (destination / name).write_text(
            json.dumps(sanitized(value, (settings.api_key,)), ensure_ascii=False, indent=2) + "\n"
        )

    save("report.json", report)
    try:
        with TestClient(app) as client:
            space = client.get(f"/api/v1/spaces/{reference['id']}").json()
            if space["profile_revision"] != 1:
                raise SystemExit("reference space changed; do not overwrite newer user work")
            target = next(
                node
                for node in space["nodes"]
                if node["contents"][0]["source"]["operation"] == "explanation"
            )
            issue = (
                "Codex 开发审阅：本版本把无 Content-Length/Transfer-Encoding 的请求与响应混为一谈，"
                "且遗漏 Set-Cookie 不能任意逗号合并的例外；HTTP 文本格式需限定 HTTP/1.1。"
                "参考 RFC 9112 §6.3、RFC 9110 §5.2。"
                "示例 JSON 显示正文的 UTF-8 字节数为 25、40、28。"
            )
            response = client.post(
                f"/api/v1/spaces/{space['id']}/nodes/{target['id']}/feedback",
                json={
                    "expected_profile_revision": 1,
                    "expected_base_revision": target["revision"],
                    "kind": "factual_error",
                    "text": issue,
                },
            )
            assert response.status_code == 201
            save(
                "reported-issue.json",
                {
                    "reviewer": "Codex agent, not independent human",
                    "feedback": response.json(),
                    "sources": [
                        "https://www.rfc-editor.org/rfc/rfc9112.html#section-6.3",
                        "https://www.rfc-editor.org/rfc/rfc9110.html#section-5.2",
                    ],
                    "previous_content": target,
                },
            )

            def execute(body: dict[str, Any]) -> dict[str, Any]:
                response = client.post(
                    f"/api/v1/spaces/{space['id']}/actions",
                    json=body,
                    headers={"Idempotency-Key": f"m2-review-{len(report['runs'])}"},
                )
                assert response.status_code == 202
                run_id = response.json()["run_id"]
                report["runs"].append(run_id)
                save("report.json", report)
                claim = service.claim(run_id)
                if not claim:
                    raise SystemExit("no available execution slot")
                worker.execute(claim)
                trace = service.trace(run_id)
                save(run_id + ".json", trace)
                if trace["snapshot"]["status"] != "succeeded":
                    report["failure"] = trace["snapshot"]["error"]
                    raise SystemExit("review follow-up failed; evidence retained")
                return client.get(f"/api/v1/spaces/{space['id']}").json()

            corrected = execute(
                {
                    "operation": "explanation",
                    "expected_profile_revision": 1,
                    "expected_base_revision": target["revision"],
                    "target_id": target["id"],
                    "instruction": issue + "请生成完整修订讲解，纠正上述问题并明确适用条件。"
                    "请求在没有长度与传输编码时消息体为零；"
                    "响应需先考虑无体状态、HEAD/CONNECT 等规则，适用情况下才由关闭连接定界。"
                    "同名字段合并必须符合列表语法，Set-Cookie 单独处理。"
                    "JSON 示例只计算展示正文、不含 Markdown 收尾换行；"
                    "不能确定字节数就不要填具体 Content-Length。",
                }
            )
            save("corrected.space.json", corrected)
            response = client.patch(
                f"/api/v1/spaces/{space['id']}/profile",
                json={
                    "expected_revision": 1,
                    "fields": {
                        "primary_purpose": "为实践构建一个可以使用的小后端服务，不准备面试。",
                        "target_abilities": [
                            "实现一个小服务的接口、数据库持久化、错误处理与基础测试"
                        ],
                        "included_scope": ["围绕一个小服务的实际开发路径，必要原理随项目引入"],
                        "excluded_scope": ["面试题背诵与无关的系统化扩展"],
                    },
                },
            )
            assert response.status_code == 200
            changed = client.get(f"/api/v1/spaces/{space['id']}").json()
            preview = execute(
                {
                    "operation": "outline",
                    "expected_profile_revision": 2,
                    "expected_base_revision": changed["outline_revision"],
                    "regenerate": True,
                }
            )
            assert preview["root_id"] == corrected["root_id"]
            root = next(node for node in preview["nodes"] if node["id"] == preview["root_id"])
            suggestion = root["suggestions"][0]
            assert suggestion["status"] == "pending"
            save(
                "purpose-preview.json",
                {
                    "previous_outline": corrected,
                    "changed_profile": response.json(),
                    "preview": suggestion,
                    "current_structure_preserved": True,
                },
            )
            report["success"] = True
    finally:
        with database.transaction() as session:
            calls = list(
                session.scalars(select(ModelCall).where(ModelCall.run_id.in_(report["runs"])))
            )
            ledger = session.get(BudgetLedger, "model")
            report.update(
                real_calls=len(calls),
                unknown_usage_calls=sum(
                    c.input_tokens is None or c.output_tokens is None for c in calls
                ),
                conservative_cost_cny=str(
                    sum(
                        c.accounted_cost if c.accounted_cost is not None else c.reserved_cost
                        for c in calls
                    )
                ),
                persistent_reserved_and_settled_cny=str(ledger.accounted_cost) if ledger else "0",
            )
        save("report.json", report)
        database.close()
    print(
        json.dumps(
            {
                "success": report.get("success", False),
                "calls": report["real_calls"],
                "cost_cny": report["conservative_cost_cny"],
            }
        )
    )


if __name__ == "__main__":
    main()
