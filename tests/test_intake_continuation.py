from dataclasses import replace
from pathlib import Path

import pytest
from sqlalchemy import func, select
from test_app import request, settings

from unfold.api_contract import CreateRun
from unfold.app.db import Database
from unfold.app.generation import FixtureGenerator
from unfold.app.learning import IntakeAction
from unfold.app.models import GenerationRun, ProfileRevision
from unfold.app.service import RunService
from unfold.app.worker import Worker
from unfold.contracts import Answer


def test_profile_and_first_outline_register_atomically_without_browser(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = RunService(database, replace(settings(database), queue_capacity=1))
    worker = Worker(service, FixtureGenerator(database, Path(__file__).resolve().parents[1]))
    first = request(service)
    service.create_run(first, "intake")
    assert worker.tick()
    intake = service.get_space(first.space_id)["intakes"][0]
    action = IntakeAction(
        operation="profile",
        expected_intake_id=intake["id"],
        answers=[
            Answer(question_key=q["key"], selected_keys=[], free_text=None, unknown=True)
            for q in intake["questionnaire"]["questions"]
        ],
    )
    queued = service.create_intake_action(first.space_id, action, "profile")
    claim = service.claim(queued.run_id)
    assert claim
    proposal = worker.generator.generate(claim)
    emit = service.emit

    def fail_at_completion(session, run, kind):
        if run.operation == "profile" and kind == "completed":
            raise RuntimeError("profile completion window")
        emit(session, run, kind)

    monkeypatch.setattr(service, "emit", fail_at_completion)
    with pytest.raises(RuntimeError, match="completion window"):
        service.finish_success(claim, proposal)
    with database.transaction() as session:
        assert session.scalar(select(func.count()).select_from(ProfileRevision)) == 0
        assert (
            session.scalar(
                select(func.count())
                .select_from(GenerationRun)
                .where(GenerationRun.operation == "outline")
            )
            == 0
        )
    monkeypatch.setattr(service, "emit", emit)
    assert service.finish_success(claim, proposal)
    assert not service.finish_success(claim, proposal)
    after = service.get_space(first.space_id)
    outlines = [row for row in after["runs"] if row["operation"] == "outline"]
    assert len(outlines) == 1 and outlines[0]["status"] == "queued"
    assert service.create_intake_action(first.space_id, action, "profile").run_id == queued.run_id
    assert worker.tick()
    saved = service.get_space(first.space_id)
    assert saved["root_id"] and saved["outline_revision"] == 1
    assert service.get_space(first.space_id)["runs"] == saved["runs"]


def test_outline_budget_refusal_preserves_full_saved_profile(database: Database) -> None:
    service = RunService(database, settings(database))
    worker = Worker(service, FixtureGenerator(database, Path(__file__).resolve().parents[1]))
    domain, brief = "超长" * 1500, "很长说明" * 1000
    space = service.create_space(domain, brief)
    first = service.create_run(
        CreateRun(
            schema_version="api.v1",
            space_id=space["id"],
            operation="questionnaire",
            profile_revision=None,
            base_revision=None,
            input={
                "schema_version": "questionnaire-input.v1",
                "domain": domain,
                "user_brief": brief,
                "known_targets": ["purpose", "background", "goal", "scope", "constraints"],
                "previous_question_keys": [],
                "questions_asked": 0,
                "round": 1,
            },
        ),
        "empty-questionnaire",
    )
    claim = service.claim(first.run_id)
    assert claim and worker.execute(claim)
    intake = service.get_space(space["id"])["intakes"][0]
    profile = service.create_intake_action(
        space["id"],
        IntakeAction(operation="profile", expected_intake_id=intake["id"], answers=[]),
        "large-profile",
    )
    claim = service.claim(profile.run_id)
    assert claim and worker.execute(claim)
    saved = service.get_space(space["id"])
    assert saved["profile"]["primary_purpose"]["value"] == brief
    assert saved["automatic_outline_error"]["code"] == "budget_exceeded"
    assert saved["root_id"] is None and saved["nodes"] == []
    assert not any(r["operation"] == "outline" for r in saved["runs"])
    assert service.get_run(profile.run_id).status == "succeeded"
