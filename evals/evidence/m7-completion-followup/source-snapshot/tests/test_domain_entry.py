from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError
from test_app import settings

from unfold.api_contract import CreateRun
from unfold.app.db import Database
from unfold.app.generation import FixtureGenerator
from unfold.app.learning import IntakeAction
from unfold.app.service import RunService
from unfold.app.worker import Worker
from unfold.contracts import Answer, parse_input


@pytest.mark.parametrize("answer_purpose", [False, True])
def test_domain_only_entry_preserves_missing_purpose_and_actual_answer(
    database: Database, answer_purpose: bool
) -> None:
    service = RunService(database, settings(database))
    space = service.create_space("只有领域的公开测试", "")
    assert not space["brief_provided"] and space["user_brief"] == ""
    raw = {
        "schema_version": "questionnaire-input.v2",
        "domain": space["domain"],
        "user_brief": "",
        "known_targets": [],
        "previous_question_keys": [],
        "questions_asked": 0,
        "round": 1,
    }
    with pytest.raises(ValidationError):
        parse_input("questionnaire", {**raw, "schema_version": "questionnaire-input.v1"})
    initial = service.create_run(
        CreateRun(
            schema_version="api.v1",
            space_id=space["id"],
            operation="questionnaire",
            profile_revision=None,
            base_revision=None,
            input=raw,
        ),
        "domain-only",
    )
    worker = Worker(service, FixtureGenerator(database, Path(__file__).resolve().parents[1]))
    claim = service.claim(initial.run_id)
    assert claim and worker.execute(claim)
    intake = service.get_space(space["id"])["intakes"][0]
    answers = [
        Answer(
            question_key=q["key"],
            selected_keys=["apply"] if answer_purpose and q["target"] == "purpose" else [],
            free_text=None,
            unknown=not (answer_purpose and q["target"] == "purpose"),
        )
        for q in intake["questionnaire"]["questions"]
    ]
    queued = service.create_intake_action(
        space["id"],
        IntakeAction(operation="profile", expected_intake_id=intake["id"], answers=answers),
        "profile",
    )
    claim = service.claim(queued.run_id)
    assert claim and claim.input["schema_version"] == "profile-input.v3"
    proposal = worker.generator.generate(claim)
    invented = deepcopy(proposal)
    invented["primary_purpose"] = {
        "value": "编造的备考用途",
        "source": "user",
        "evidence_keys": ["brief"],
    }
    with pytest.raises(ValueError, match="brief was not supplied"):
        service.finish_success(claim, invented)
    assert service.get_space(space["id"])["profile_revision"] == 0
    assert service.finish_success(claim, proposal)
    fact = service.get_space(space["id"])["profile"]["primary_purpose"]
    assert fact["source"] == ("user" if answer_purpose else "unknown")
    assert fact["evidence_keys"] == (["answer:purpose"] if answer_purpose else [])
    assert worker.tick() and service.get_space(space["id"])["root_id"]
