import json
import os
import selectors
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import func, select, text
from test_app import request, settings

from unfold.app.db import Database
from unfold.app.generation import FixtureGenerator
from unfold.app.models import GenerationRun, IntakeSession
from unfold.app.service import RunService
from unfold.app.worker import Worker

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("checkpoint", ["claimed", "response_saved", "before_commit", "committed"])
def test_real_process_death_and_restart(database: Database, checkpoint: str) -> None:
    service = RunService(database, settings(database, retry_base_seconds=0))
    payload = request(service)
    run = service.create_run(payload, "process-death")
    environment = {
        **os.environ,
        "UNFOLD_FAULT_DATABASE_URL": database.engine.url.render_as_string(hide_password=False),
    }
    process = subprocess.Popen(
        [
            sys.executable,
            str(ROOT / "scripts/fault_worker.py"),
            "--run-id",
            run.run_id,
            "--checkpoint",
            checkpoint,
        ],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=ROOT,
    )
    try:
        assert process.stdout
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            assert selector.select(timeout=10), "checkpoint not reached"
            assert process.stdout.readline().strip() == "CHECKPOINT_READY"
        process.kill()
        process.wait(timeout=5)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
    if checkpoint == "committed":
        assert service.get_run(run.run_id).status == "succeeded"
        assert service.recover_expired() == 0
        assert not Worker(service, FixtureGenerator(database, ROOT)).tick()
    else:
        assert service.get_run(run.run_id).status == "running"
        with database.transaction() as session:
            session.execute(
                text(
                    "UPDATE generation_runs SET lease_until=clock_timestamp()-interval '1 second' "
                    "WHERE id=:id"
                ),
                {"id": run.run_id},
            )
        assert service.recover_expired() == 1
        assert Worker(service, FixtureGenerator(database, ROOT)).tick()
        assert service.get_run(run.run_id).status == "succeeded"
        assert service.get_run(run.run_id).attempt == 2
        if checkpoint in ("response_saved", "before_commit"):
            assert service.trace(run.run_id)["calls"][-1]["provider"] == "stored_response_replay"
    assert service.create_run(payload, "process-death").run_id == run.run_id
    with database.transaction() as session:
        assert session.scalar(select(func.count()).select_from(IntakeSession)) == 1
        assert session.scalar(select(func.count()).select_from(GenerationRun)) == 1
    target = ROOT / ".data/qa" / ("m3-process-" + checkpoint + ".json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {
                "checkpoint": checkpoint,
                "child_killed_with_sigkill": True,
                "trace": service.trace(run.run_id),
                "saved_intakes": 1,
                "request_replay_reused": True,
                "assertions_passed": True,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
