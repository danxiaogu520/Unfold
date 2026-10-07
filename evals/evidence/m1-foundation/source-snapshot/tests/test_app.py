from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from unfold.api_contract import CreateRun
from unfold.app.api import create_app
from unfold.app.config import AppSettings
from unfold.app.db import Database
from unfold.app.generation import DeepSeekGenerator, FixtureGenerator
from unfold.app.migrate import migrate
from unfold.app.models import (
    Base,
    BudgetLedger,
    GenerationRun,
    IntakeSession,
    OutlineItem,
    RunEventRecord,
)
from unfold.app.security import sanitized
from unfold.app.service import RunService, ServiceError
from unfold.app.worker import Worker
from unfold.provider import GenerationError, ProviderResult, Settings

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = json.loads((ROOT / "evals/contracts/success.v1.json").read_text())
QUESTIONNAIRE = next(row for row in SAMPLES if row["operation"] == "questionnaire")


def settings(database: Database, **changes: Any) -> AppSettings:
    return AppSettings(
        database_url=database.engine.url.render_as_string(hide_password=False),
        **changes,
    )


def request(service: RunService) -> CreateRun:
    raw = QUESTIONNAIRE["input"]
    space = service.create_space(raw["domain"], raw["user_brief"])
    return CreateRun(
        schema_version="api.v1",
        space_id=space["id"],
        operation="questionnaire",
        profile_revision=None,
        base_revision=None,
        input=raw,
    )


def test_empty_migration_matches_metadata_and_round_trip(database: Database) -> None:
    with database.engine.connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
        assert connection.scalar(text("SELECT version()")).startswith("PostgreSQL 18.")
    migrate(database, "base", downgrade=True)
    with database.engine.connect() as connection:
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_schema=current_schema()"
                )
            )
            == 1
        )
    migrate(database)
    with database.engine.connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []


def test_http_worker_publishes_and_replays_events(database: Database) -> None:
    config = settings(database)
    app = create_app(config, database)
    service: RunService = app.state.service
    payload = request(service)
    with TestClient(app) as client:
        queued = client.post(
            "/api/v1/runs", json=payload.model_dump(), headers={"Idempotency-Key": "success"}
        )
        assert queued.status_code == 202
        run_id = queued.json()["run_id"]
        assert queued.json()["status"] == "queued"
        assert Worker(service, FixtureGenerator(database, ROOT)).tick()
        committed = client.get(f"/api/v1/runs/{run_id}").json()
        assert committed["status"] == "succeeded" and committed["result_ref"]
        assert not Worker(service, FixtureGenerator(database, ROOT)).tick()
        events = service.events(run_id)
        assert [row["kind"] for row in events] == ["queued", "started", "completed"]
        replay = client.get(f"/api/v1/runs/{run_id}/events", headers={"Last-Event-ID": "2"})
        assert "id: 3\n" in replay.text and "id: 1\n" not in replay.text
        trace = client.get(f"/api/v1/runs/{run_id}/trace").json()
        assert trace["attempts"] == [
            {"attempt": 1, "status": "succeeded", "outcome_unknown": False}
        ]
        assert trace["calls"][0]["provider"] == "deterministic_fake"
        assert trace["calls"][0]["input_tokens"] is None
        assert "lease_token" not in json.dumps(trace)
        saved = client.get(f"/api/v1/spaces/{payload.space_id}").json()
        assert saved["intakes"][0]["id"] == committed["result_ref"]
        assert client.get("/api/health").json()["model_mode"] == "fake"


def test_failed_candidate_never_becomes_official_knowledge(database: Database) -> None:
    service = RunService(database, settings(database))
    payload = request(service)
    run = service.create_run(payload, "invalid-output")
    assert Worker(service, FixtureGenerator(database, ROOT, failure=True)).tick()
    failed = service.get_run(run.run_id)
    assert failed.status == "failed" and failed.result_ref is None
    assert failed.error and failed.error.code == "schema_validation"
    with database.transaction() as session:
        assert session.scalar(select(func.count()).select_from(IntakeSession)) == 0
    assert [row["kind"] for row in service.events(run.run_id)] == ["queued", "started", "failed"]


def test_concurrent_keys_and_aliases_survive_completion(database: Database) -> None:
    service = RunService(database, settings(database))
    payload = request(service)
    with ThreadPoolExecutor(max_workers=8) as pool:
        runs = list(pool.map(lambda i: service.create_run(payload, "key-" + str(i)), range(16)))
    assert len({run.run_id for run in runs}) == 1
    assert Worker(service, FixtureGenerator(database, ROOT)).tick()
    for i in range(16):
        assert service.create_run(payload, "key-" + str(i)).run_id == runs[0].run_id
    altered = payload.model_copy(update={"input": {**payload.input, "known_targets": []}})
    with pytest.raises(ServiceError, match="different input"):
        service.create_run(altered, "key-15")
    with database.transaction() as session:
        assert session.scalar(select(func.count()).select_from(GenerationRun)) == 1


def test_claim_and_global_capacity_across_spaces(database: Database) -> None:
    service = RunService(database, settings(database, queue_capacity=4, worker_concurrency=2))
    payloads = [request(service) for _ in range(8)]

    def enqueue(i: int) -> bool:
        try:
            service.create_run(payloads[i], "same-client-key")
            return True
        except ServiceError as exc:
            assert exc.http_status == 429
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(enqueue, range(8))) == 4
        claims = [claim for claim in pool.map(lambda _: service.claim(), range(8)) if claim]
    assert len(claims) == 2
    assert len({claim.run_id for claim in claims}) == 2
    first = claims[0]
    assert not service.heartbeat(replace(first, token="obsolete"))
    service.cancel(first.run_id)
    assert not service.finish_success(first, QUESTIONNAIRE["output"])
    assert service.claim() is not None


def test_cancellation_has_no_publication_and_is_idempotent(database: Database) -> None:
    service = RunService(database, settings(database))
    payload = request(service)
    run = service.create_run(payload, "cancel")
    claim = service.claim()
    assert claim
    cancelled = service.cancel(run.run_id)
    assert cancelled.status == "cancelled"
    assert service.cancel(run.run_id).last_event_seq == cancelled.last_event_seq
    assert not service.heartbeat(claim)
    assert not service.finish_success(claim, QUESTIONNAIRE["output"])
    assert service.get_space(payload.space_id)["intakes"] == []


@pytest.mark.parametrize("violation", ["cross-space-parent", "negative-position", "bad-kind"])
def test_database_protects_item_references_and_bounds(database: Database, violation: str) -> None:
    service = RunService(database, settings(database))
    a, b = request(service).space_id, request(service).space_id
    with database.transaction() as session:
        session.add(
            OutlineItem(
                id="parent",
                space_id=a,
                parent_id=None,
                concept_id=None,
                position=0,
                kind="group",
                title="root",
                scope="scope",
                overview="overview",
                revision=1,
            )
        )
    with pytest.raises(IntegrityError), database.transaction() as session:
        session.add(
            OutlineItem(
                id="invalid",
                space_id=b,
                parent_id="parent" if violation == "cross-space-parent" else None,
                position=-1 if violation == "negative-position" else 0,
                kind="invalid" if violation == "bad-kind" else "group",
                title="invalid",
                scope="scope",
                overview="overview",
                revision=1,
            )
        )


def test_http_contract_errors_and_foreign_origin_guard(database: Database) -> None:
    with TestClient(create_app(settings(database), database)) as client:
        assert client.post("/api/v1/runs", json={}).status_code == 422
        denied = client.post(
            "/api/v1/spaces",
            json={"domain": "x", "user_brief": "y"},
            headers={"Origin": "https://attacker.example"},
        )
        assert denied.status_code == 403
        assert client.get("/api/v1/runs/missing").status_code == 404


def test_request_identity_includes_redacted_values(database: Database) -> None:
    service = RunService(database, settings(database))
    original = {**QUESTIONNAIRE["input"], "user_brief": "sk-" + "a" * 32}
    space = service.create_space(original["domain"], original["user_brief"])
    first = CreateRun(
        schema_version="api.v1",
        space_id=space["id"],
        operation="questionnaire",
        profile_revision=None,
        base_revision=None,
        input=original,
    )
    changed = first.model_copy(update={"input": {**original, "user_brief": "sk-" + "b" * 32}})
    assert sanitized(first.model_dump()) == sanitized(changed.model_dump())
    run = service.create_run(first, "identity")
    with pytest.raises(ServiceError):
        service.create_run(changed, "identity")
    assert original["user_brief"] not in json.dumps(service.trace(run.run_id))


def test_redaction_is_recursive_and_does_not_mutate_input() -> None:
    source = {
        "Authorization": "Bearer private",
        "nested": [{"api_key": "private", "text": "sk-" + "a" * 32 + " exact-secret"}],
    }
    result = sanitized(source, ("exact-secret",))
    assert "private" not in json.dumps(result) and "exact-secret" not in json.dumps(result)
    assert source["Authorization"] == "Bearer private"


def test_config_is_private_and_rejects_remote_access() -> None:
    config = AppSettings(database_url="postgresql+psycopg://u:secret@127.0.0.1/db")
    assert "secret" not in repr(config)
    with pytest.raises(GenerationError):
        AppSettings(database_url=config.database_url, host="0.0.0.0")


@pytest.mark.parametrize("mode", ["known", "unknown", "timeout", "truncated"])
def test_provider_outcomes_and_persistent_budget(
    database: Database,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    service = RunService(database, settings(database))
    payload = request(service)
    run = service.create_run(payload, "provider")
    calls = 0

    class ControlledProvider:
        def __init__(self, config: Settings) -> None:
            assert config.api_key == "test-secret"

        def generate(self, body: dict[str, Any]) -> ProviderResult:
            nonlocal calls
            calls += 1
            if mode == "timeout":
                return ProviderResult(
                    None, None, None, None, None, "timeout", "timeout", outcome_unknown=True
                )
            if mode == "truncated":
                return ProviderResult(
                    "response", "deepseek-flash", None, 100, 100, "truncated", "max output tokens"
                )
            return ProviderResult(
                "response",
                "deepseek-flash",
                json.dumps(QUESTIONNAIRE["output"]),
                100 if mode == "known" else None,
                100 if mode == "known" else None,
                None,
                None,
            )

        def close(self) -> None:
            pass

    monkeypatch.setattr("unfold.app.generation.ResponsesProvider", ControlledProvider)
    config = Settings(
        api_key="test-secret",
        model="deepseek-flash",
        base_url="https://api.deepseek.com",
        max_cost=Decimal("0.12"),
        input_rate=Decimal(2),
        output_rate=Decimal(8),
    )
    generator = DeepSeekGenerator(database, service, config, ROOT)
    assert Worker(service, generator).tick()
    result = service.get_run(run.run_id)
    assert result.status == ("failed" if mode in ("timeout", "truncated") else "succeeded")
    trace = service.trace(run.run_id)
    assert "test-secret" not in json.dumps(trace)
    assert calls == 1  # External failures are not automatically retried by the adapter.
    with database.transaction() as session:
        ledger = session.get(BudgetLedger, "model")
        assert ledger
        if mode in ("known", "truncated"):
            assert ledger.accounted_cost == Decimal("0.001")
        else:
            assert ledger.accounted_cost == Decimal(trace["calls"][0]["reserved_cost"])
        assert ledger.accounted_cost > 0
    if mode == "timeout":
        assert result.error and result.error.outcome_unknown
    if mode == "unknown":
        assert trace["calls"][0]["input_tokens"] is None
        assert trace["calls"][0]["accounted_cost"] is None
    # Reopening with a smaller configured budget cannot reset recorded spending.
    generator.settings = replace(config, max_cost=Decimal("0.00001"))
    another = service.create_run(request(service), "cannot-reset-budget")
    assert Worker(service, generator).tick()
    failed = service.get_run(another.run_id)
    assert failed.error and failed.error.code == "budget_exceeded"
    assert calls == 1


def test_interruption_rolls_back_content_state_and_event_together(
    database: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = RunService(database, settings(database))
    run = service.create_run(request(service), "interrupt")
    claim = service.claim()
    assert claim
    emit = service.emit

    def interrupt(session: Any, current: GenerationRun, kind: str) -> None:
        emit(session, current, kind)
        if kind == "completed":
            session.flush()
            raise RuntimeError("injected interruption after writes before commit")

    monkeypatch.setattr(service, "emit", interrupt)
    with pytest.raises(RuntimeError, match="injected interruption"):
        service.finish_success(claim, QUESTIONNAIRE["output"])
    assert service.get_run(run.run_id).status == "running"
    with database.transaction() as session:
        assert session.scalar(select(func.count()).select_from(IntakeSession)) == 0
        assert session.scalar(select(func.count()).select_from(RunEventRecord)) == 2
    monkeypatch.setattr(service, "emit", emit)
    assert service.finish_success(claim, QUESTIONNAIRE["output"])


def test_database_error_response_does_not_echo_exception(
    database: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy.exc import OperationalError

    app = create_app(settings(database), database)

    def disconnected() -> Any:
        raise OperationalError("private SQL", {"password": "secret"}, RuntimeError("private DSN"))

    monkeypatch.setattr(database, "transaction", disconnected)
    with TestClient(app) as client:
        response = client.get("/api/health")
        assert response.status_code == 503
        assert "secret" not in response.text and "private" not in response.text
