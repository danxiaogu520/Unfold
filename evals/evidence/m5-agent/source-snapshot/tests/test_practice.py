import copy
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from test_app import settings
from test_learning import seed

from unfold.app.api import create_app
from unfold.app.db import Database
from unfold.app.generation import FixtureGenerator
from unfold.app.learning import LearningAction
from unfold.app.models import ExerciseAnswer, ExerciseQuestion, GenerationRun, RequestIdentity
from unfold.app.practice import AnswerSubmission, Practice, QuestionIssue
from unfold.app.service import RunService, ServiceError
from unfold.app.worker import Worker
from unfold.contracts import validate_pair
from unfold.provider import json_bytes

ROOT = Path(__file__).resolve().parents[1]


def test_existing_action_identity_survives_new_optional_count(database: Database) -> None:
    service, space_id = seed(database)
    node_id = service.get_space(space_id)["root_id"]
    action = LearningAction(
        operation="explanation",
        target_id=node_id,
        expected_profile_revision=1,
        expected_base_revision=1,
    )
    run = service.create_action(space_id, action, "legacy-action")
    old_digest = hashlib.sha256(
        json_bytes(
            {"space_id": space_id, "action": action.model_dump(mode="json", exclude={"count"})}
        )
    ).hexdigest()
    with database.transaction() as session:
        identity = session.get(RequestIdentity, (space_id, "legacy-action"))
        assert identity and identity.request_digest == old_digest
        row = session.get(GenerationRun, run.run_id)
        assert row
        old_active = hashlib.sha256(
            json_bytes(
                {
                    "operation": row.operation,
                    "profile": row.profile_revision,
                    "base": row.base_revision,
                    "target": row.target_id,
                }
            )
        ).hexdigest()
        assert row.active_key == old_active
    assert Worker(service, FixtureGenerator(database, ROOT)).tick()
    assert service.create_action(space_id, action, "legacy-action").run_id == run.run_id


def test_guided_mode_requirement_is_checked_against_actual_proposal(database: Database) -> None:
    service, space_id, node_id, run_id = make_practice(database)
    trace = service.trace(run_id)
    assert trace["input"]["schema_version"] == "exercises-input.v2"
    assert trace["input"]["required_modes"] == ["single", "multiple"]
    altered = copy.deepcopy(trace["proposal"])
    for exercise in altered["exercises"]:
        exercise["mode"] = "single"
        exercise["correct_keys"] = exercise["correct_keys"][:1]
    with pytest.raises(ValueError, match="mode mix"):
        validate_pair("exercises", trace["input"], altered)


def make_practice(database: Database, count: int = 3) -> tuple[RunService, str, str, str]:
    service, space_id = seed(database)
    node = service.get_space(space_id)["nodes"][0]
    run = service.create_action(
        space_id,
        LearningAction(
            operation="exercises",
            expected_profile_revision=1,
            expected_base_revision=1,
            target_id=node["id"],
            count=count,
        ),
        "exercises",
    )
    assert Worker(service, FixtureGenerator(database, ROOT)).tick()
    assert service.get_run(run.run_id).status == "succeeded"
    assert service.get_run(run.run_id).result_kind == "exercise_set"
    return service, space_id, node["id"], run.run_id


@pytest.mark.parametrize("count", [1, 3, 5])
def test_saved_public_questions_hide_answers_and_preserve_content(
    database: Database, count: int
) -> None:
    service, space_id, node_id, run_id = make_practice(database, count)
    node = service.get_node(space_id, node_id)
    assert node["revision"] == 1 and len(node["contents"]) == 1
    questions = node["exercise_sets"][0]["questions"]
    assert len(questions) == count
    assert "correct_keys" not in json.dumps(questions) and "explanation" not in json.dumps(
        questions
    )
    with TestClient(create_app(settings(database), database)) as client:
        view = client.get(f"/api/v1/spaces/{space_id}/nodes/{node_id}")
        assert "correct_keys" not in view.text
        trace = client.get(f"/api/v1/runs/{run_id}/trace")
        assert "correct_keys" not in trace.text
        assert trace.json()["proposal"] is None
        assert all(call["response"] is None for call in trace.json()["calls"])


def test_single_and_multiple_grade_saved_ids_not_display_order(database: Database) -> None:
    service, space_id, node_id, _ = make_practice(database)
    questions = service.get_node(space_id, node_id)["exercise_sets"][0]["questions"]
    with database.transaction() as session:
        for question in session.scalars(select(ExerciseQuestion)):
            question.proposal = {
                **question.proposal,
                "options": list(reversed(question.proposal["options"])),
            }
    with database.transaction() as session:
        single = Practice.submit(
            session,
            space_id,
            questions[0]["exercise_revision_ref"],
            AnswerSubmission(selected_keys=["scope"]),
            "single",
        )
        partial = Practice.submit(
            session,
            space_id,
            questions[1]["exercise_revision_ref"],
            AnswerSubmission(selected_keys=["scope"]),
            "partial",
        )
        exact = Practice.submit(
            session,
            space_id,
            questions[1]["exercise_revision_ref"],
            AnswerSubmission(selected_keys=["purpose", "scope"]),
            "exact",
        )
    assert single["correct"] and not partial["correct"] and exact["correct"]
    assert single["content_refs"] == [service.get_node(space_id, node_id)["contents"][0]["id"]]
    assert single["explanation"] and single["grading"] == "exact-set.v1"
    for keys in (["scope", "scope"], ["invented"], ["scope", "purpose"]):
        with pytest.raises(ServiceError):
            with database.transaction() as session:
                Practice.submit(
                    session,
                    space_id,
                    questions[0]["exercise_revision_ref"],
                    AnswerSubmission(selected_keys=keys),
                    "invalid",
                )


def test_answer_idempotence_under_concurrency_and_disabled_question(database: Database) -> None:
    service, space_id, node_id, _ = make_practice(database)
    question = service.get_node(space_id, node_id)["exercise_sets"][0]["questions"][0]
    question_id = question["exercise_revision_ref"]

    def submit(_: int) -> dict:
        with database.transaction() as session:
            return Practice.submit(
                session,
                space_id,
                question_id,
                AnswerSubmission(selected_keys=["scope"]),
                "same-answer",
            )

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(submit, range(16)))
    assert len({row["answer_id"] for row in results}) == 1
    with database.transaction() as session:
        assert session.scalar(select(func.count()).select_from(ExerciseAnswer)) == 1
        assert (
            Practice.report(session, space_id, question_id, QuestionIssue(text="答案可能有歧义"))[
                "status"
            ]
            == "disabled"
        )
    assert submit(0) == results[0]  # Retained result can still be replayed after disable.
    with pytest.raises(ServiceError, match="disabled"):
        with database.transaction() as session:
            Practice.submit(
                session, space_id, question_id, AnswerSubmission(selected_keys=["scope"]), "new"
            )
    with pytest.raises(ServiceError, match="different selection"):
        with database.transaction() as session:
            Practice.submit(
                session,
                space_id,
                question_id,
                AnswerSubmission(selected_keys=["unknown"]),
                "same-answer",
            )


def test_regeneration_preserves_question_answer_versions_and_leaf(database: Database) -> None:
    service, space_id, node_id, _ = make_practice(database)
    before = service.get_node(space_id, node_id)
    first = before["exercise_sets"][0]["questions"][0]
    with database.transaction() as session:
        answer = Practice.submit(
            session,
            space_id,
            first["exercise_revision_ref"],
            AnswerSubmission(selected_keys=["scope"]),
            "historical",
        )
    action = LearningAction(
        operation="exercises",
        expected_profile_revision=1,
        expected_base_revision=1,
        target_id=node_id,
    )
    with pytest.raises(ServiceError, match="exercises exist"):
        service.create_action(space_id, action, "not-explicit")
    service.create_action(space_id, action.model_copy(update={"regenerate": True}), "new-version")
    assert Worker(service, FixtureGenerator(database, ROOT)).tick()
    after = service.get_node(space_id, node_id)
    assert after["decision"] == before["decision"] and after["contents"] == before["contents"]
    assert [item["revision"] for item in after["exercise_sets"]] == [2, 1]
    assert after["exercise_sets"][1]["questions"][0]["attempts"] == [answer]
    assert after["exercise_sets"][0]["questions"][0]["attempts"] == []


def test_answer_question_cannot_cross_space(database: Database) -> None:
    service, space_id, node_id, _ = make_practice(database)
    question = service.get_node(space_id, node_id)["exercise_sets"][0]["questions"][0]
    other = service.create_space("其它", "隔离")
    with pytest.raises(ServiceError, match="not found"):
        with database.transaction() as session:
            Practice.submit(
                session,
                other["id"],
                question["exercise_revision_ref"],
                AnswerSubmission(selected_keys=["scope"]),
                "cross-space",
            )
