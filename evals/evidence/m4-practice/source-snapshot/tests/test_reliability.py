from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path

from sqlalchemy import func, select, text
from test_app import QUESTIONNAIRE, request, settings

from unfold.app.db import Database
from unfold.app.generation import FixtureGenerator
from unfold.app.models import BudgetLedger, ModelCall, RunAttempt
from unfold.app.service import Claim, RunService
from unfold.app.worker import Worker
from unfold.provider import GenerationError

ROOT = Path(__file__).resolve().parents[1]


def write_evidence(name: str, payload: object) -> None:
    import json

    target = ROOT / ".data/qa" / ("m3-" + name + ".json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def test_queued_dependency_change_conflicts_before_any_model_call(database: Database) -> None:
    from test_learning import seed

    from unfold.app.learning import LearningAction, ProfileEdit

    service, space_id = seed(database)
    node = service.get_space(space_id)["nodes"][0]
    run = service.create_action(
        space_id,
        LearningAction(
            operation="explanation",
            expected_profile_revision=1,
            expected_base_revision=1,
            target_id=node["id"],
        ),
        "queued-old-goal",
    )
    service.edit_profile(
        space_id, ProfileEdit(expected_revision=1, fields={"primary_purpose": "新目标"})
    )
    assert service.claim(run.run_id) is None
    trace = service.trace(run.run_id)
    assert trace["snapshot"]["status"] == "conflicted"
    assert trace["snapshot"]["attempt"] == 0 and trace["calls"] == []
    write_evidence("queued-conflict", trace)


def test_cancel_while_waiting_for_budget_does_not_reserve_or_dispatch(database: Database) -> None:
    import threading

    from unfold.app.generation import DeepSeekGenerator
    from unfold.provider import Settings

    service = RunService(database, settings(database))
    run = service.create_run(request(service), "cancel-at-budget-lock")
    claim = service.claim()
    assert claim
    heartbeat_seen = threading.Event()
    heartbeat = service.heartbeat

    def observe(token: Claim) -> bool:
        result = heartbeat(token)
        heartbeat_seen.set()
        return result

    service.heartbeat = observe
    config = Settings(
        api_key="test-secret",
        model="deepseek-flash",
        base_url="https://api.deepseek.com",
        max_cost=Decimal(1),
        input_rate=Decimal(2),
        output_rate=Decimal(8),
    )
    generator = DeepSeekGenerator(database, service, config, ROOT)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with database.transaction() as session:
            session.execute(text("SELECT pg_advisory_xact_lock(180002)"))
            future = pool.submit(generator.reserve_call, claim, 1, generator.build_body(claim))
            assert heartbeat_seen.wait(timeout=5)
            service.cancel(run.run_id)
        import pytest

        with pytest.raises(GenerationError) as error:
            future.result(timeout=5)
        assert error.value.code == "cancelled"
    with database.transaction() as session:
        assert session.scalar(select(func.count()).select_from(ModelCall)) == 0
        assert session.get(BudgetLedger, "model") is None


def test_resource_outbox_is_atomic_with_explanation_and_completion(database: Database) -> None:
    import pytest
    from test_learning import seed

    from unfold.app.learning import LearningAction
    from unfold.app.models import ContentRevision, ResourceTask

    service, space_id = seed(database)
    node = service.get_space(space_id)["nodes"][0]
    run = service.create_action(
        space_id,
        LearningAction(
            operation="explanation",
            target_id=node["id"],
            expected_profile_revision=1,
            expected_base_revision=1,
        ),
        "explanation-with-resource",
    )
    claim = service.claim(run.run_id)
    assert claim
    proposal = FixtureGenerator(database, ROOT).generate(claim)
    proposal["resource_requests"] = [
        {
            "key": "illustration",
            "kind": "image",
            "purpose": "配合讲解",
            "specification": "具有来源记录的示意图",
            "runtime": None,
            "controls": [],
            "fallback_text": "图片制作失败时阅读正文。",
        }
    ]
    proposal["blocks"].append(
        {
            "type": "image",
            "resource_key": "illustration",
            "caption": "示意图",
            "fallback_text": "图片尚未就绪，请阅读正文。",
        }
    )
    emit = service.emit

    def interrupted(session: object, current: object, kind: str) -> None:
        emit(session, current, kind)
        if kind == "completed":
            session.flush()
            raise RuntimeError("interrupted atomic resource commit")

    service.emit = interrupted
    with pytest.raises(RuntimeError, match="atomic resource commit"):
        service.finish_success(claim, proposal)
    with database.transaction() as session:
        assert session.scalar(select(func.count()).select_from(ResourceTask)) == 0
        assert session.scalar(select(func.count()).select_from(ContentRevision)) == 3
    assert service.get_run(run.run_id).status == "running"
    service.emit = emit
    assert service.finish_success(claim, proposal)
    assert not service.finish_success(claim, proposal)
    with database.transaction() as session:
        tasks = list(session.scalars(select(ResourceTask)))
        assert len(tasks) == 1 and tasks[0].status == "pending"
        assert tasks[0].source_run_id == run.run_id
        assert tasks[0].content_id == service.get_run(run.run_id).result_ref
    assert service.events(run.run_id)[-1]["kind"] == "completed"
    write_evidence(
        "resource-atomicity",
        {
            "trace": service.trace(run.run_id),
            "resource_task_count": 1,
            "content_count_before_success": 3,
            "rollback_observed": True,
            "pending_resources_are_not_ready_files": True,
        },
    )


def expire(database: Database, run_id: str, *, deadline: bool = False) -> None:
    with database.transaction() as session:
        column = "attempt_deadline" if deadline else "lease_until"
        session.execute(
            text(
                f"UPDATE generation_runs SET {column}=clock_timestamp()-interval '1 second' "
                "WHERE id=:run_id"
            ),
            {"run_id": run_id},
        )


def test_saved_response_recovery_reuses_without_another_generation(database: Database) -> None:
    service = RunService(database, settings(database, retry_base_seconds=0))
    run = service.create_run(request(service), "lost-after-model")
    first = service.claim()
    assert first
    output = FixtureGenerator(database, ROOT).generate(first)
    expire(database, run.run_id)
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(lambda _: service.recover_expired(), range(2))) == 1
    assert not service.heartbeat(first)
    assert not service.finish_success(first, output)

    class MustNotGenerate:
        def generate(self, claim: Claim) -> dict:
            raise AssertionError("a completed response must be reused")

    assert Worker(service, MustNotGenerate()).tick()
    result = service.get_run(run.run_id)
    assert result.status == "succeeded" and result.attempt == 2
    trace = service.trace(run.run_id)
    assert [row["provider"] for row in trace["calls"]] == [
        "deterministic_fake",
        "stored_response_replay",
    ]
    assert [row["kind"] for row in service.events(run.run_id)] == [
        "queued",
        "started",
        "progress",
        "started",
        "completed",
    ]


def test_attempt_deadline_cannot_be_extended_by_heartbeat(database: Database) -> None:
    service = RunService(database, settings(database, retry_base_seconds=0))
    run = service.create_run(request(service), "deadline")
    claim = service.claim()
    assert claim
    expire(database, run.run_id, deadline=True)
    assert not service.heartbeat(claim)
    assert not service.finish_success(claim, QUESTIONNAIRE["output"])
    assert service.recover_expired() == 1
    assert service.get_run(run.run_id).status == "queued"


def test_retry_is_bounded_and_permanent_errors_are_terminal(database: Database) -> None:
    service = RunService(database, settings(database, retry_base_seconds=0, max_attempts=3))
    run = service.create_run(request(service), "timeouts")
    for attempt in range(1, 4):
        claim = service.claim(run.run_id)
        assert claim and claim.attempt == attempt
        assert service.finish_failure(
            claim, GenerationError("timeout", "controlled timeout", outcome_unknown=True)
        )
        assert service.get_run(run.run_id).status == ("queued" if attempt < 3 else "failed")
    assert service.claim(run.run_id) is None
    final = service.get_run(run.run_id)
    assert final.error and final.error.outcome_unknown
    other = service.create_run(request(service), "refusal")
    claim = service.claim(other.run_id)
    assert claim
    assert service.finish_failure(claim, GenerationError("refusal", "controlled refusal"))
    assert service.get_run(other.run_id).status == "failed"


def test_backoff_and_cancellation_do_not_revive(database: Database) -> None:
    service = RunService(database, settings(database, retry_base_seconds=30))
    run = service.create_run(request(service), "backoff")
    claim = service.claim()
    assert claim
    assert service.finish_failure(claim, GenerationError("rate_limit", "controlled rate limit"))
    assert service.get_run(run.run_id).status == "queued"
    assert service.claim() is None
    service.cancel(run.run_id)
    with database.transaction() as session:
        session.execute(
            text("UPDATE generation_runs SET not_before=clock_timestamp() WHERE id=:id"),
            {"id": run.run_id},
        )
    assert service.recover_expired() == 0 and service.claim() is None
    assert service.get_run(run.run_id).status == "cancelled"


def test_unknown_external_execution_preserves_reserved_cost(database: Database) -> None:
    service = RunService(database, settings(database, retry_base_seconds=0))
    run = service.create_run(request(service), "unknown-model-result")
    claim = service.claim()
    assert claim
    with database.transaction() as session:
        session.add(
            BudgetLedger(
                id="model", currency="CNY", limit_cost=Decimal(1), accounted_cost=Decimal("0.1")
            )
        )
        session.add(
            ModelCall(
                id="unknown",
                run_id=run.run_id,
                attempt=1,
                step=1,
                provider="controlled_unknown",
                model="fixture",
                prompt_version="fixture",
                schema_version="questionnaire.v1",
                status="started",
                request=claim.input,
                response=None,
                input_tokens=None,
                output_tokens=None,
                reserved_cost=Decimal("0.1"),
                accounted_cost=None,
                currency="CNY",
                outcome_unknown=True,
            )
        )
    expire(database, run.run_id)
    assert service.recover_expired() == 1
    with database.transaction() as session:
        attempt = session.get(RunAttempt, (run.run_id, 1))
        ledger = session.get(BudgetLedger, "model")
        assert attempt and attempt.outcome_unknown
        assert ledger and ledger.accounted_cost == Decimal("0.1")
        assert session.scalar(select(func.count()).select_from(ModelCall)) == 1
    assert Worker(service, FixtureGenerator(database, ROOT)).tick()
    assert service.get_run(run.run_id).status == "succeeded"
    assert not service.finish_success(claim, QUESTIONNAIRE["output"])


def test_expired_events_return_snapshot_fallback_without_new_generation(database: Database) -> None:
    import pytest
    from fastapi.testclient import TestClient

    from unfold.app.api import create_app
    from unfold.app.service import ServiceError

    config = settings(database, event_retention_seconds=10)
    service = RunService(database, config)
    payload = request(service)
    run = service.create_run(payload, "event-retention")
    assert Worker(service, FixtureGenerator(database, ROOT)).tick()
    with database.transaction() as session:
        session.execute(
            text(
                "UPDATE run_events SET created_at=clock_timestamp()-interval '1 day' "
                "WHERE run_id=:id"
            ),
            {"id": run.run_id},
        )
    assert service.prune_events() == 3
    with pytest.raises(ServiceError) as rejected:
        service.events(run.run_id, after=1)
    assert rejected.value.http_status == 410
    with TestClient(create_app(config, database)) as client:
        response = client.get(f"/api/v1/runs/{run.run_id}/events", headers={"Last-Event-ID": "1"})
        assert response.status_code == 410
        snapshot = client.get(f"/api/v1/runs/{run.run_id}").json()
        assert snapshot["status"] == "succeeded" and snapshot["last_event_seq"] == 3
    assert service.create_run(payload, "event-retention").run_id == run.run_id
    assert not Worker(service, FixtureGenerator(database, ROOT)).tick()


def test_retention_only_removes_contiguous_prefix(database: Database) -> None:
    service = RunService(database, settings(database, event_retention_seconds=10))
    run = service.create_run(request(service), "prefix-only")
    assert Worker(service, FixtureGenerator(database, ROOT)).tick()
    with database.transaction() as session:
        session.execute(
            text(
                "UPDATE run_events SET created_at=clock_timestamp()-interval '1 day' "
                "WHERE run_id=:id AND sequence IN (1,3)"
            ),
            {"id": run.run_id},
        )
    assert service.prune_events() == 1
    assert [row["sequence"] for row in service.events(run.run_id, 1)] == [2, 3]


def test_cancel_and_completion_race_has_one_effective_terminal_state(database: Database) -> None:
    import threading

    service = RunService(database, settings(database))
    outcomes: dict[str, int] = {}
    for index in range(20):
        payload = request(service)
        run = service.create_run(payload, f"cancel-race-{index}")
        claim = service.claim(run.run_id)
        assert claim
        barrier = threading.Barrier(2)

        def cancel(gate: threading.Barrier = barrier, run_id: str = run.run_id) -> object:
            gate.wait()
            return service.cancel(run_id)

        def complete(gate: threading.Barrier = barrier, token: Claim = claim) -> object:
            gate.wait()
            return service.finish_success(token, QUESTIONNAIRE["output"])

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(cancel), pool.submit(complete)]
            for future in futures:
                future.result(timeout=10)
        state = service.get_run(run.run_id)
        assert state.status in ("cancelled", "succeeded")
        saved = service.get_space(payload.space_id)["intakes"]
        assert len(saved) == (1 if state.status == "succeeded" else 0)
        assert service.events(run.run_id)[-1]["snapshot"]["status"] == state.status
        outcomes[state.status] = outcomes.get(state.status, 0) + 1
    write_evidence(
        "cancel-race", {"iterations": 20, "outcomes": outcomes, "one_terminal_state_per_run": True}
    )


def test_old_attempt_late_after_second_success_cannot_overwrite(database: Database) -> None:
    service = RunService(database, settings(database, retry_base_seconds=0))
    payload = request(service)
    run = service.create_run(payload, "late-first")
    first = service.claim()
    assert first
    expire(database, run.run_id)
    assert service.recover_expired() == 1
    assert Worker(service, FixtureGenerator(database, ROOT)).tick()
    accepted = service.get_run(run.run_id)
    before = service.get_space(payload.space_id)
    assert accepted.status == "succeeded" and accepted.attempt == 2
    assert not service.finish_success(first, QUESTIONNAIRE["output"])
    assert not service.finish_failure(first, GenerationError("provider_error", "late error"))
    assert service.get_run(run.run_id) == accepted
    assert service.get_space(payload.space_id) == before
