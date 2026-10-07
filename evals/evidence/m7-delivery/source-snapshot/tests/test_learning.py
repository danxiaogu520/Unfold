from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select
from test_app import request, settings

from unfold.api_contract import CreateRun
from unfold.app.db import Database
from unfold.app.generation import DeepSeekGenerator, FixtureGenerator
from unfold.app.learning import FeedbackInput, IntakeAction, LearningAction, ProfileEdit
from unfold.app.migrate import migrate
from unfold.app.models import ProfileRevision
from unfold.app.service import RunService, ServiceError
from unfold.app.worker import Worker
from unfold.contracts import Answer
from unfold.provider import ProviderResult, Settings

ROOT = Path(__file__).resolve().parents[1]


def seed(database: Database) -> tuple[RunService, str]:
    service = RunService(database, settings(database))
    worker = Worker(service, FixtureGenerator(database, ROOT))
    first = request(service)
    service.create_run(first, "questionnaire")
    assert worker.tick()
    intake = service.get_space(first.space_id)["intakes"][-1]
    raw = {
        "schema_version": "profile-input.v1",
        "domain": first.input["domain"],
        "user_brief": first.input["user_brief"],
        "questionnaire": intake["questionnaire"],
        "answers": [
            {"question_key": q["key"], "selected_keys": [], "free_text": None, "unknown": True}
            for q in intake["questionnaire"]["questions"]
        ],
    }
    service.create_run(
        CreateRun(
            schema_version="api.v1",
            space_id=first.space_id,
            operation="profile",
            profile_revision=None,
            base_revision=None,
            input=raw,
        ),
        "profile",
    )
    assert worker.tick()
    service.create_action(
        first.space_id, LearningAction(operation="outline", expected_profile_revision=1), "outline"
    )
    assert worker.tick()
    return service, first.space_id


def test_outline_one_layer_and_read_never_generates(database: Database) -> None:
    service, space_id = seed(database)
    space = service.get_space(space_id)
    assert space["outline_revision"] == 1 and len(space["nodes"]) == 3
    root = service.get_node(space_id, space["root_id"])
    assert len(root["children"]) == 2
    for child in root["children"]:
        assert service.get_node(space_id, child)["children"] == []
    assert not Worker(service, FixtureGenerator(database, ROOT)).tick()
    assert service.get_space(space_id)["runs"] == space["runs"]


def test_leaf_forbids_all_expansion_paths_but_allows_explanation(database: Database) -> None:
    service, space_id = seed(database)
    space = service.get_space(space_id)
    leaf = next(node for node in space["nodes"] if node["decision"]["state"] == "leaf")
    for regenerate in (False, True):
        with pytest.raises(ServiceError, match="leaf cannot"):
            service.create_action(
                space_id,
                LearningAction(
                    operation="expansion",
                    expected_profile_revision=1,
                    expected_base_revision=1,
                    target_id=leaf["id"],
                    regenerate=regenerate,
                ),
                uuid4().hex,
            )
    result = service.create_action(
        space_id,
        LearningAction(
            operation="explanation",
            expected_profile_revision=1,
            expected_base_revision=1,
            target_id=leaf["id"],
        ),
        "explain",
    )
    assert Worker(service, FixtureGenerator(database, ROOT)).tick()
    assert service.get_run(result.run_id).status == "succeeded"
    updated = service.get_node(space_id, leaf["id"])
    assert updated["children"] == [] and updated["decision"]["state"] == "leaf"
    assert updated["revision"] == 2 and len(updated["contents"]) == 2
    with pytest.raises(ServiceError, match="leaf cannot"):
        service.create_action(
            space_id,
            LearningAction(
                operation="expansion",
                expected_profile_revision=1,
                expected_base_revision=2,
                target_id=leaf["id"],
            ),
            "still-leaf",
        )


def test_profile_edit_preserves_content_marks_stale_and_conflicts_pending_run(
    database: Database,
) -> None:
    service, space_id = seed(database)
    leaf = next(
        node for node in service.get_space(space_id)["nodes"] if node["decision"]["state"] == "leaf"
    )
    run = service.create_action(
        space_id,
        LearningAction(
            operation="explanation",
            expected_profile_revision=1,
            expected_base_revision=1,
            target_id=leaf["id"],
        ),
        "old-profile",
    )
    claim = service.claim()
    assert claim
    proposal = FixtureGenerator(database, ROOT).generate(claim)
    edit = service.edit_profile(
        space_id, ProfileEdit(expected_revision=1, fields={"primary_purpose": "为真实项目补基础"})
    )
    assert edit["revision"] == 2
    assert not service.finish_success(claim, proposal)
    assert service.get_run(run.run_id).status == "conflicted"
    updated = service.get_node(space_id, leaf["id"])
    assert updated["contents"] == leaf["contents"]
    assert updated["decision"]["status"] == "stale"
    with pytest.raises(ServiceError, match="stale decision"):
        service.create_action(
            space_id,
            LearningAction(
                operation="expansion",
                expected_profile_revision=2,
                expected_base_revision=1,
                target_id=leaf["id"],
            ),
            "stale",
        )
    service.create_action(
        space_id,
        LearningAction(
            operation="reassessment",
            expected_profile_revision=2,
            expected_base_revision=1,
            target_id=leaf["id"],
        ),
        "new-decision",
    )
    assert Worker(service, FixtureGenerator(database, ROOT)).tick()
    reassessed = service.get_node(space_id, leaf["id"])
    assert reassessed["decision"]["profile_revision"] == 2
    assert reassessed["contents"][0]["profile_revision"] == 1
    assert reassessed["contents"][0]["source"]["content_carried_from"] == leaf["contents"][0]["id"]
    with database.transaction() as session:
        versions = list(session.scalars(select(ProfileRevision).order_by(ProfileRevision.revision)))
        assert [v.revision for v in versions] == [1, 2]
        assert versions[0].proposal != versions[1].proposal


def test_reassessment_is_independent_and_action_identity_survives_changed_state(
    database: Database,
) -> None:
    service, space_id = seed(database)
    leaf = next(
        node for node in service.get_space(space_id)["nodes"] if node["decision"]["state"] == "leaf"
    )
    action = LearningAction(
        operation="reassessment",
        expected_profile_revision=1,
        expected_base_revision=1,
        target_id=leaf["id"],
        instruction="希望继续比较边界",
    )
    run = service.create_action(space_id, action, "reassess")
    assert service.create_action(space_id, action, "reassess-alias").run_id == run.run_id
    assert Worker(service, FixtureGenerator(database, ROOT)).tick()
    assert service.create_action(space_id, action, "reassess-alias").run_id == run.run_id
    updated = service.get_node(space_id, leaf["id"])
    assert updated["decision"]["state"] == "expandable" and updated["children"] == []
    assert updated["revision"] == 2
    service.create_action(
        space_id,
        LearningAction(
            operation="expansion",
            expected_profile_revision=1,
            expected_base_revision=2,
            target_id=leaf["id"],
        ),
        "now-expand",
    )
    assert Worker(service, FixtureGenerator(database, ROOT)).tick()
    assert len(service.get_node(space_id, leaf["id"])["children"]) == 1


def test_reassessment_of_existing_subtree_retains_explicit_review(database: Database) -> None:
    service, space_id = seed(database)
    root = service.get_node(space_id, service.get_space(space_id)["root_id"])
    run = service.create_action(
        space_id,
        LearningAction(
            operation="reassessment",
            expected_profile_revision=1,
            expected_base_revision=1,
            target_id=root["id"],
        ),
        "review",
    )
    claim = service.claim()
    assert claim
    assert service.finish_success(
        claim,
        {
            "schema_version": "reassessment.v1",
            "target_key": root["id"],
            "decision": {"state": "leaf", "reason": "新用途下需要重新组织"},
            "restructuring_note": "保留原内容，建议审核子树归属。",
        },
    )
    updated = service.get_node(space_id, root["id"])
    assert updated["children"] == root["children"] and updated["revision"] == 1
    assert updated["decision"]["state"] == "expandable"
    assert updated["suggestions"][0]["id"] == service.get_run(run.run_id).result_ref


def test_expansion_regeneration_archives_descendants_and_checks_context(database: Database) -> None:
    service, space_id = seed(database)
    node = next(
        node
        for node in service.get_space(space_id)["nodes"]
        if node["parent_id"] and node["decision"]["state"] == "expandable"
    )
    worker = Worker(service, FixtureGenerator(database, ROOT))
    action = LearningAction(
        operation="expansion",
        expected_profile_revision=1,
        expected_base_revision=1,
        target_id=node["id"],
    )
    run = service.create_action(space_id, action, "expand")
    assert worker.tick()
    expanded = service.get_node(space_id, node["id"])
    with pytest.raises(ServiceError, match="children exist"):
        service.create_action(
            space_id, action.model_copy(update={"expected_base_revision": 2}), "repeat"
        )
    old_child = expanded["children"][0]
    replacement = service.create_action(
        space_id,
        action.model_copy(update={"expected_base_revision": 2, "regenerate": True}),
        "regenerate",
    )
    assert worker.tick()
    preview = service.get_run(replacement.run_id)
    assert preview.result_kind == "structure_preview" and preview.result_ref
    assert service.get_node(space_id, node["id"])["children"] == [old_child]
    service.review_structure(space_id, preview.result_ref, "apply")
    assert service.review_structure(space_id, preview.result_ref, "apply")["status"] == "applied"
    regenerated = service.get_node(space_id, node["id"])
    assert regenerated["children"][0] != old_child
    assert not service.get_node(space_id, old_child)["active"]
    assert len(regenerated["contents"]) == 3
    trace = service.trace(run.run_id)
    assert trace["dependencies"]["context_strategy"] == "current_path"
    assert trace["input"]["path"][-1]["key"] == node["id"]
    forged = trace["request"]
    forged["base_revision"] = 3
    forged["input"]["target"]["content_revision"] = 3
    forged["input"]["path"][-1] = forged["input"]["target"]
    forged["input"]["target"]["overview"] = "伪造的概述"
    with pytest.raises(ServiceError, match="saved state"):
        service.create_run(CreateRun.model_validate(forged), "forge")


def test_feedback_has_versions_and_never_changes_decision(database: Database) -> None:
    service, space_id = seed(database)
    leaf = next(
        node for node in service.get_space(space_id)["nodes"] if node["decision"]["state"] == "leaf"
    )
    feedback = service.feedback(
        space_id,
        leaf["id"],
        FeedbackInput(
            expected_profile_revision=1,
            expected_base_revision=1,
            kind="need_example",
            text="需要一个更具体的例子",
        ),
    )
    assert feedback["id"]
    assert service.get_node(space_id, leaf["id"]) == leaf
    run = service.create_action(
        space_id,
        LearningAction(
            operation="explanation",
            expected_profile_revision=1,
            expected_base_revision=1,
            target_id=leaf["id"],
        ),
        "example",
    )
    assert (
        service.trace(run.run_id)["dependencies"]["selected_context"]["feedback"][0]["text"]
        == feedback["text"]
    )


def test_followup_preserves_both_rounds_caps_and_request_identity(database: Database) -> None:
    service = RunService(database, settings(database))
    worker = Worker(service, FixtureGenerator(database, ROOT))
    first = request(service)
    service.create_run(first, "first-round")
    assert worker.tick()
    intake = service.get_space(first.space_id)["intakes"][0]
    answers = [
        Answer(question_key=q["key"], selected_keys=[], free_text=None, unknown=True)
        for q in intake["questionnaire"]["questions"]
    ]
    action = IntakeAction(
        operation="questionnaire", expected_intake_id=intake["id"], answers=answers
    )
    run = service.create_intake_action(first.space_id, action, "followup")
    assert worker.tick()
    assert service.create_intake_action(first.space_id, action, "followup").run_id == run.run_id
    saved = service.get_space(first.space_id)["intakes"]
    assert len(saved) == 2 and saved[0]["answers"] == [a.model_dump() for a in answers]
    assert saved[-1]["questions_asked"] <= 5
    assert not (
        {q["key"] for q in saved[0]["questionnaire"]["questions"]}
        & {q["key"] for q in saved[1]["questionnaire"]["questions"]}
    )
    second_answers = [
        Answer(question_key=q["key"], selected_keys=[], free_text=None, unknown=True)
        for q in saved[1]["questionnaire"]["questions"]
    ]
    with pytest.raises(ServiceError, match="two rounds"):
        service.create_intake_action(
            first.space_id,
            IntakeAction(
                operation="questionnaire", expected_intake_id=saved[1]["id"], answers=second_answers
            ),
            "third-round",
        )
    profile = service.create_intake_action(
        first.space_id,
        IntakeAction(
            operation="profile", expected_intake_id=saved[1]["id"], answers=second_answers
        ),
        "from-two-rounds",
    )
    assert worker.tick()
    assert service.get_run(profile.run_id).status == "succeeded"
    trace = service.trace(profile.run_id)
    assert sum(len(item["answers"]) for item in trace["input"]["rounds"]) == len(answers) + len(
        second_answers
    )
    assert trace["dependencies"]["selected_context"]["previous_intakes"][0]["answers"]


def test_direct_api_cannot_spoof_a_leaf_decision(database: Database) -> None:
    service, space_id = seed(database)
    leaf = next(
        node for node in service.get_space(space_id)["nodes"] if node["decision"]["state"] == "leaf"
    )
    run = service.create_action(
        space_id,
        LearningAction(
            operation="explanation",
            expected_profile_revision=1,
            expected_base_revision=1,
            target_id=leaf["id"],
        ),
        "read-context",
    )
    raw = service.trace(run.run_id)["request"]
    raw["operation"] = "expansion"
    raw["input"] = {
        key: value
        for key, value in raw["input"].items()
        if key not in {"instruction", "allowed_resource_kinds", "available_exercise_refs"}
    }
    raw["input"].update(schema_version="expansion-input.v1", operation="expand")
    raw["input"]["target"]["decision"] = {"state": "expandable", "reason": "client spoof"}
    raw["input"]["path"][-1] = raw["input"]["target"]
    with pytest.raises(ServiceError, match="saved state"):
        service.create_run(CreateRun.model_validate(raw), "spoof-leaf")


def test_cross_operation_race_keeps_only_current_base_result(database: Database) -> None:
    service, space_id = seed(database)
    node = next(
        node
        for node in service.get_space(space_id)["nodes"]
        if node["parent_id"] and node["decision"]["state"] == "expandable"
    )
    actions = [
        LearningAction(
            operation=operation,
            expected_profile_revision=1,
            expected_base_revision=1,
            target_id=node["id"],
        )
        for operation in ("expansion", "explanation")
    ]
    runs = [
        service.create_action(space_id, action, "race-" + action.operation) for action in actions
    ]
    claims = [service.claim(), service.claim()]
    assert all(claims)
    generator = FixtureGenerator(database, ROOT)
    proposals = [generator.generate(claim) for claim in claims if claim]
    assert claims[0] and claims[1]
    assert service.finish_success(claims[0], proposals[0])
    assert not service.finish_success(claims[1], proposals[1])
    assert [service.get_run(run.run_id).status for run in runs] == ["succeeded", "conflicted"]
    assert service.get_node(space_id, node["id"])["revision"] == 2


def test_wrong_parent_concept_reuse_is_repaired_before_publication(
    database: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, space_id = seed(database)
    node = next(
        node
        for node in service.get_space(space_id)["nodes"]
        if node["parent_id"] and node["decision"]["state"] == "expandable"
    )
    run = service.create_action(
        space_id,
        LearningAction(
            operation="expansion",
            expected_profile_revision=1,
            expected_base_revision=1,
            target_id=node["id"],
        ),
        "repair-reuse",
    )
    calls = 0

    class Provider:
        def __init__(self, config: Settings) -> None:
            pass

        def generate(self, body: dict) -> ProviderResult:
            nonlocal calls
            calls += 1
            child = {
                "key": "child",
                "title": "更窄的子概念",
                "kind": "concept",
                "concept_ref": node["concept_id"] if calls == 1 else None,
                "scope": "更窄的范围",
                "overview": "子概念的概述",
                "decision": {"state": "leaf", "reason": "测试终点"},
            }
            proposal = {
                "schema_version": "expansion.v1",
                "target_key": node["id"],
                "decision": {"state": "expandable", "reason": "需要细分"},
                "division_basis": "测试范围",
                "children": [child],
                "relations": [],
            }
            if calls == 2:
                assert "new or narrower sub-concepts" in body["instructions"]
                assert (
                    json.loads(body["input"])["previous_candidate"]["children"][0]["concept_ref"]
                    == node["concept_id"]
                )
            return ProviderResult(
                "response", "deepseek-flash", json.dumps(proposal), 100, 100, None, None
            )

        def close(self) -> None:
            pass

    monkeypatch.setattr("unfold.app.generation.ResponsesProvider", Provider)
    config = Settings(
        api_key="test-secret",
        model="deepseek-flash",
        base_url="https://api.deepseek.com",
        max_cost=Decimal(1),
        input_rate=Decimal(2),
        output_rate=Decimal(8),
    )
    assert Worker(service, DeepSeekGenerator(database, service, config, ROOT)).tick()
    trace = service.trace(run.run_id)
    assert trace["snapshot"]["status"] == "succeeded" and calls == 2
    assert [call["error_code"] for call in trace["calls"]] == ["domain_validation", None]
    child_id = service.get_node(space_id, node["id"])["children"][0]
    assert service.get_node(space_id, child_id)["concept_id"] != node["concept_id"]


def test_outline_preview_apply_and_legacy_migration_backfill(database: Database) -> None:
    service, space_id = seed(database)
    before = service.get_space(space_id)
    old_root = service.get_node(space_id, before["root_id"])
    run = service.create_action(
        space_id,
        LearningAction(
            operation="outline",
            expected_profile_revision=1,
            expected_base_revision=1,
            regenerate=True,
        ),
        "new-outline",
    )
    assert Worker(service, FixtureGenerator(database, ROOT)).tick()
    preview = service.get_run(run.run_id)
    assert preview.result_kind == "structure_preview" and preview.result_ref
    unchanged = service.get_space(space_id)
    assert unchanged["root_id"] == before["root_id"] and unchanged["outline_revision"] == 1
    assert service.get_node(space_id, old_root["id"])["children"] == old_root["children"]
    migrate(database, "0002", downgrade=True)
    migrate(database)
    restored = service.get_run(run.run_id)
    assert restored.result_kind == "structure_preview"
    assert service.review_structure(space_id, preview.result_ref, "apply")["status"] == "applied"
    changed = service.get_space(space_id)
    assert changed["root_id"] != old_root["id"] and changed["outline_revision"] == 2
    assert not service.get_node(space_id, old_root["id"])["active"]
    assert service.get_node(space_id, old_root["id"])["contents"] == old_root["contents"]
    assert service.review_structure(space_id, preview.result_ref, "apply")["status"] == "applied"
    assert service.get_space(space_id)["outline_revision"] == 2


def test_preview_cannot_overwrite_changed_content_or_publish_after_dismissal(
    database: Database,
) -> None:
    service, space_id = seed(database)
    root_id = service.get_space(space_id)["root_id"]
    worker = Worker(service, FixtureGenerator(database, ROOT))
    run = service.create_action(
        space_id,
        LearningAction(
            operation="outline",
            expected_profile_revision=1,
            expected_base_revision=1,
            regenerate=True,
        ),
        "preview",
    )
    assert worker.tick()
    preview = service.get_run(run.run_id)
    assert preview.result_ref
    service.create_action(
        space_id,
        LearningAction(
            operation="explanation",
            expected_profile_revision=1,
            expected_base_revision=1,
            target_id=root_id,
        ),
        "changed-root",
    )
    assert worker.tick()
    with pytest.raises(ServiceError, match="dependencies changed"):
        service.review_structure(space_id, preview.result_ref, "apply")
    assert service.get_node(space_id, root_id)["suggestions"][0]["status"] == "stale"
    assert (
        service.review_structure(space_id, preview.result_ref, "dismiss")["status"] == "dismissed"
    )
    with pytest.raises(ServiceError, match="dismissed"):
        service.review_structure(space_id, preview.result_ref, "apply")
    assert service.get_space(space_id)["root_id"] == root_id


def test_explicitly_accepted_leaf_preview_archives_subtree_without_deleting(
    database: Database,
) -> None:
    service, space_id = seed(database)
    root = service.get_node(space_id, service.get_space(space_id)["root_id"])
    run = service.create_action(
        space_id,
        LearningAction(
            operation="reassessment",
            expected_profile_revision=1,
            expected_base_revision=1,
            target_id=root["id"],
        ),
        "leaf-preview",
    )
    claim = service.claim()
    assert claim
    assert service.finish_success(
        claim,
        {
            "schema_version": "reassessment.v1",
            "target_key": root["id"],
            "decision": {"state": "leaf", "reason": "当前用途下可在本层承载"},
            "restructuring_note": "归档已有子项，保留历史内容。",
        },
    )
    preview = service.get_run(run.run_id)
    assert preview.result_ref and preview.result_kind == "structure_preview"
    assert service.get_node(space_id, root["id"])["children"] == root["children"]
    service.review_structure(space_id, preview.result_ref, "apply")
    updated = service.get_node(space_id, root["id"])
    assert updated["children"] == [] and updated["decision"]["state"] == "leaf"
    assert all(not service.get_node(space_id, child)["active"] for child in root["children"])
    assert len(updated["contents"]) == 2


def test_reported_content_status_follows_copy_but_new_explanation_is_unverified(
    database: Database,
) -> None:
    service, space_id = seed(database)
    node = next(
        node for node in service.get_space(space_id)["nodes"] if node["decision"]["state"] == "leaf"
    )
    service.feedback(
        space_id,
        node["id"],
        FeedbackInput(
            expected_profile_revision=1,
            expected_base_revision=1,
            kind="factual_error",
            text="固定示例存在内容疑点",
        ),
    )
    assert service.get_node(space_id, node["id"])["contents"][0]["review_status"] == "reported"
    worker = Worker(service, FixtureGenerator(database, ROOT))
    service.create_action(
        space_id,
        LearningAction(
            operation="reassessment",
            expected_profile_revision=1,
            expected_base_revision=1,
            target_id=node["id"],
        ),
        "retain-issue",
    )
    assert worker.tick()
    assert service.get_node(space_id, node["id"])["contents"][0]["review_status"] == "reported"
    service.create_action(
        space_id,
        LearningAction(
            operation="explanation",
            expected_profile_revision=1,
            expected_base_revision=2,
            target_id=node["id"],
        ),
        "new-content",
    )
    assert worker.tick()
    updated = service.get_node(space_id, node["id"])
    assert updated["contents"][0]["review_status"] == "unverified"
    assert updated["contents"][1]["review_status"] == "reported"


def test_shared_concept_can_use_another_path_title_without_changing_identity(
    database: Database,
) -> None:
    service, space_id = seed(database)
    nodes = service.get_space(space_id)["nodes"]
    target = next(n for n in nodes if n["parent_id"] and n["decision"]["state"] == "expandable")
    shared = next(n for n in nodes if n["parent_id"] and n["decision"]["state"] == "leaf")
    run = service.create_action(
        space_id,
        LearningAction(
            operation="expansion",
            expected_profile_revision=1,
            expected_base_revision=1,
            target_id=target["id"],
        ),
        "reuse-path",
    )
    claim = service.claim()
    assert claim
    proposal = {
        "schema_version": "expansion.v1",
        "target_key": target["id"],
        "decision": {"state": "expandable", "reason": "在当前路径联系应用"},
        "division_basis": "当前路径的实践重点",
        "relations": [],
        "children": [
            {
                "key": "shared",
                "title": "实践路径中的边界",
                "kind": "concept",
                "concept_ref": shared["concept_id"],
                "scope": shared["scope"],
                "overview": "在新的路径展示相同概念的应用重点。",
                "decision": {"state": "leaf", "reason": "当前目标无需再分"},
            }
        ],
    }
    from unfold.app.generation import validate_candidate_context
    from unfold.contracts import validate_pair

    validate_candidate_context(validate_pair("expansion", claim.input, proposal), claim.context)
    assert service.finish_success(claim, proposal)
    child = service.get_node(space_id, service.get_node(space_id, target["id"])["children"][0])
    assert child["concept_id"] == shared["concept_id"] and child["title"] != shared["title"]
    assert service.get_node(space_id, shared["id"])["title"] == shared["title"]
    assert service.get_run(run.run_id).status == "succeeded"


def test_same_title_different_scope_does_not_automatically_merge(database: Database) -> None:
    service, space_id = seed(database)
    nodes = service.get_space(space_id)["nodes"]
    target = next(n for n in nodes if n["parent_id"] and n["decision"]["state"] == "expandable")
    existing = next(n for n in nodes if n["parent_id"] and n["decision"]["state"] == "leaf")
    run = service.create_action(
        space_id,
        LearningAction(
            operation="expansion",
            target_id=target["id"],
            expected_profile_revision=1,
            expected_base_revision=1,
        ),
        "same-title-different-meaning",
    )
    claim = service.claim(run.run_id)
    assert claim
    proposal = {
        "schema_version": "expansion.v1",
        "target_key": target["id"],
        "decision": {"state": "expandable", "reason": "另一范围需要独立承载"},
        "division_basis": "人工同名异义回归",
        "relations": [],
        "children": [
            {
                "key": "different_meaning",
                "title": existing["title"],
                "kind": "concept",
                "concept_ref": None,
                "scope": "人工样例：社会网络关系，与协议网络边界不同",
                "overview": "标题相同，范围不同；不借用已有概念身份。",
                "decision": {"state": "leaf", "reason": "这个用途下无需继续细分"},
            }
        ],
    }
    assert service.finish_success(claim, proposal)
    child = service.get_node(space_id, service.get_node(space_id, target["id"])["children"][0])
    assert child["title"] == existing["title"]
    assert child["concept_id"] != existing["concept_id"]
    assert child["scope"] != existing["scope"]
