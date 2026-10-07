"""Fixed public synthetic snapshots, separate from private application data."""

from __future__ import annotations

from typing import Any

from unfold.api_contract import CreateRun
from unfold.app.generation import FixtureGenerator
from unfold.app.learning import LearningAction
from unfold.app.service import RunService
from unfold.app.worker import Worker
from unfold.eval_resources import ROOT

SIZES = {"small": 10, "medium": 120, "large": 600}


def new_public_learning_space(service: RunService, case: dict[str, Any], prefix: str) -> str:
    """Create only a new space; never enumerate or select an existing user space."""
    domain, brief = case["domain"], case["user_brief"]
    space_id = str(service.create_space(domain, brief)["id"])
    worker = Worker(service, FixtureGenerator(service.database, ROOT))

    def execute(run_id: str) -> None:
        claim = service.claim(run_id)
        assert claim and worker.execute(claim)
        assert service.get_run(run_id).status == "succeeded"

    intake = service.create_run(
        CreateRun(
            schema_version="api.v1",
            space_id=space_id,
            operation="questionnaire",
            profile_revision=None,
            base_revision=None,
            input={
                "schema_version": "questionnaire-input.v1",
                "domain": domain,
                "user_brief": brief,
                "known_targets": case["known_targets"],
                "previous_question_keys": [],
                "questions_asked": 0,
                "round": 1,
            },
        ),
        prefix + ":intake",
    )
    execute(intake.run_id)
    questionnaire = service.get_space(space_id)["intakes"][0]["questionnaire"]
    profile = service.create_run(
        CreateRun(
            schema_version="api.v1",
            space_id=space_id,
            operation="profile",
            profile_revision=None,
            base_revision=None,
            input={
                "schema_version": "profile-input.v1",
                "domain": domain,
                "user_brief": brief,
                "questionnaire": questionnaire,
                "answers": [
                    {
                        "question_key": q["key"],
                        "selected_keys": [],
                        "free_text": None,
                        "unknown": True,
                    }
                    for q in questionnaire["questions"]
                ],
            },
        ),
        prefix + ":profile",
    )
    execute(profile.run_id)
    outline = service.create_action(
        space_id,
        LearningAction(operation="outline", expected_profile_revision=1),
        prefix + ":outline",
    )
    execute(outline.run_id)
    return space_id


def public_snapshot(request: dict[str, Any], count: int) -> dict[str, Any]:
    """Labelled synthetic scale load; notes are not independently verified teaching."""
    target = request["target"]
    root = target["key"]
    nodes: list[tuple[str, str | None, str, str]] = [(root, None, target["title"], target["scope"])]
    nodes += [
        (row["key"], root, row["title"], row["scope"]) for row in request["existing_children"]
    ]
    related_id = "synthetic_related"
    nodes.append(("synthetic_branch", root, "旁支资料", "独立音乐节奏资料"))
    nodes.append((related_id, "synthetic_branch", target["title"], "另一分支：用途提示"))
    while len(nodes) < count:
        index = len(nodes)
        nodes.append(
            (f"synthetic_{index:04}", "synthetic_branch", f"旁支素材 {index}", "独立音乐节奏资料")
        )
    pages: list[dict[str, Any]] = []
    for index, (node_id, parent_id, title, scope) in enumerate(nodes):
        text = (
            "公开合成上下文记录。当前用途："
            + request["profile"]["profile"]["primary_purpose"]["value"]
            if index < 3 or node_id == related_id
            else "公开合成负载：音乐节奏的独立资料，未作事实核验。"
        )
        pages.append(
            {
                "id": "content_" + node_id,
                "node_id": node_id,
                "parent_id": parent_id,
                "node_revision": 1,
                "content_revision": 1,
                "profile_revision": 1,
                "title": title,
                "scope": scope,
                "text": text + "\n" + ("合成填充。 " * 25),
                "active": True,
                "review_status": "unverified",
                "provenance": "public_synthetic_evaluation.v1",
            }
        )
    return {
        "version": "context-evaluation-snapshot.v1",
        "data_classification": "public_synthetic_evaluation.v1",
        "nodes": count,
        "content_versions": len(pages),
        "body_characters": sum(len(page["text"]) for page in pages),
        "pages": pages,
        "relations": [
            {
                "id": "synthetic_relation",
                "source_id": root,
                "target_id": related_id,
                "kind": "related",
                "explanation": "公开合成关系：同主题的另一用途；保留节点范围，不合并。",
            }
        ],
        "profile_history": [request["profile"]],
    }
