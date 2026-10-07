import json
import os
import selectors
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import func, select, text
from test_resources import cached_executor, resource_setup

from unfold.app.db import Database
from unfold.app.files import FileStore
from unfold.app.generation import FixtureGenerator
from unfold.app.models import ResourceCandidate, ResourceRevision
from unfold.app.resources import ApplicationGenerator, Resources
from unfold.app.worker import Worker

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "checkpoint", ["file_saved", "candidate_saved", "before_commit", "committed"]
)
def test_real_process_death_across_file_database_windows(
    database: Database, tmp_path: Path, checkpoint: str
) -> None:
    service, space_id, node_id, _ = resource_setup(database, tmp_path)
    assert Resources.dispatch(service) == 1
    resource = service.get_node(space_id, node_id)["contents"][0]["resources"][0]
    run_id = resource["run_id"]
    environment = {
        **os.environ,
        "UNFOLD_FAULT_DATABASE_URL": database.engine.url.render_as_string(hide_password=False),
        "UNFOLD_FAULT_FILES": str(tmp_path),
    }
    process = subprocess.Popen(
        [
            sys.executable,
            str(ROOT / "scripts/resource_fault_worker.py"),
            "--run-id",
            run_id,
            "--checkpoint",
            checkpoint,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
        cwd=ROOT,
    )
    try:
        assert process.stdout
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            assert selector.select(timeout=10), "resource checkpoint not reached"
            assert process.stdout.readline().strip() == "CHECKPOINT_READY"
        process.kill()
        process.wait(timeout=5)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
    with database.transaction() as session:
        candidates = session.scalar(select(func.count()).select_from(ResourceCandidate))
        published = session.scalar(select(func.count()).select_from(ResourceRevision))
    assert candidates == (0 if checkpoint == "file_saved" else 1)
    assert published == (1 if checkpoint == "committed" else 0)
    orphaned = FileStore(tmp_path).unreferenced(set()) if checkpoint == "file_saved" else []
    assert bool(orphaned) == (checkpoint == "file_saved")
    worker = Worker(
        service, ApplicationGenerator(FixtureGenerator(database, ROOT), cached_executor(service))
    )
    if checkpoint == "committed":
        assert service.recover_expired() == 0 and not worker.tick()
    else:
        with database.transaction() as session:
            session.execute(
                text(
                    "UPDATE generation_runs SET lease_until=clock_timestamp()-interval '1 second' "
                    "WHERE id=:id"
                ),
                {"id": run_id},
            )
        assert service.recover_expired() == 1
        assert worker.tick()
    result = service.get_node(space_id, node_id)["contents"][0]["resources"][0]
    assert result["status"] == "ready" and len(result["versions"]) == 1
    assert service.get_node(space_id, node_id)["revision"] == 2
    trace = service.trace(run_id)
    assert trace["snapshot"]["attempt"] == (1 if checkpoint == "committed" else 2)
    if checkpoint in ("candidate_saved", "before_commit"):
        assert trace["tools"][-1]["output"]["network_call"] is False
    if checkpoint == "file_saved":
        assert trace["attempts"][0]["outcome_unknown"]
        assert trace["tools"][0]["outcome_unknown"]
    target = ROOT / ".data/qa" / f"m4-process-{checkpoint}.json"
    target.write_text(
        json.dumps(
            {
                "checkpoint": checkpoint,
                "sigkill": True,
                "before_recovery_candidates": candidates,
                "before_recovery_published": published,
                "identified_orphans": orphaned,
                "resource": result,
                "trace": trace,
                "real_model_calls": 0,
                "live_image_retrieval": False,
                "assertions_passed": True,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
