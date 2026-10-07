"""Predeclared synthetic HTTP/PostgreSQL workload; never calls an external model."""

from __future__ import annotations

import json
import os
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import uvicorn
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.schema import CreateSchema, DropSchema

from unfold.app.api import create_app
from unfold.app.config import AppSettings
from unfold.app.db import Database
from unfold.app.generation import FixtureGenerator
from unfold.app.migrate import migrate
from unfold.app.models import GenerationRun, IntakeSession
from unfold.app.service import Claim, RunService
from unfold.app.worker import Worker

ROOT = Path(__file__).resolve().parents[1]
WORKLOAD = {
    "requests": 64,
    "clients": 8,
    "queue_capacity": 32,
    "global_execution_limit": 2,
    "worker_processes": 0,
    "worker_instances": 2,
    "cancel_before_execution": 8,
    "fake_delay_seconds": 0.05,
}


def percentiles(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    return {
        f"p{p}_ms": round(1000 * ordered[min(len(ordered) - 1, int(len(ordered) * p / 100))], 3)
        for p in (50, 95)
    }


def main() -> None:
    url = os.environ.get("UNFOLD_TEST_DATABASE_URL")
    if not url:
        raise SystemExit("UNFOLD_TEST_DATABASE_URL required; an isolated schema is created")
    schema = "load_" + uuid4().hex
    control = create_engine(url)
    with control.begin() as connection:
        connection.execute(CreateSchema(schema))
    isolated = make_url(url).update_query_dict({"options": f"-csearch_path={schema}"})
    config = AppSettings(database_url=isolated.render_as_string(hide_password=False))
    database = Database(config)
    stop = threading.Event()
    threads: list[threading.Thread] = []
    server: uvicorn.Server | None = None
    started = time.monotonic()
    try:
        migrate(database)
        service = RunService(database, config)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        server = uvicorn.Server(
            uvicorn.Config(
                create_app(config, database),
                host="127.0.0.1",
                port=port,
                log_level="error",
                access_log=False,
            )
        )
        server_thread = threading.Thread(target=server.run, daemon=True)
        server_thread.start()
        threads.append(server_thread)
        deadline = time.monotonic() + 10
        while not server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("local HTTP server did not start")
            time.sleep(0.01)
        fixture = json.loads((ROOT / "evals/contracts/success.v1.json").read_text())[0]["input"]

        def submit(index: int) -> dict[str, Any]:
            with httpx.Client(base_url=base, timeout=20, trust_env=False) as client:
                space = client.post(
                    "/api/v1/spaces",
                    json={"domain": fixture["domain"], "user_brief": fixture["user_brief"]},
                )
                space.raise_for_status()
                payload = {
                    "schema_version": "api.v1",
                    "space_id": space.json()["id"],
                    "operation": "questionnaire",
                    "profile_revision": None,
                    "base_revision": None,
                    "input": fixture,
                }
                begin = time.monotonic()
                result = client.post(
                    "/api/v1/runs", json=payload, headers={"Idempotency-Key": f"load-{index}"}
                )
                duration = time.monotonic() - begin
                if result.status_code == 202:
                    duplicate = client.post(
                        "/api/v1/runs", json=payload, headers={"Idempotency-Key": f"alias-{index}"}
                    )
                    assert duplicate.status_code == 202
                    assert duplicate.json()["run_id"] == result.json()["run_id"]
                return {
                    "status": result.status_code,
                    "seconds": duration,
                    "run_id": result.json().get("run_id"),
                    "payload": payload,
                    "key": f"load-{index}",
                }

        with ThreadPoolExecutor(max_workers=8) as pool:
            submissions = list(pool.map(submit, range(64)))
        accepted = [row for row in submissions if row["status"] == 202]
        assert len(accepted) == 32
        assert sum(row["status"] == 429 for row in submissions) == 32
        with httpx.Client(base_url=base, timeout=20, trust_env=False) as client:
            for row in accepted[:8]:
                response = client.post(f"/api/v1/runs/{row['run_id']}/cancel")
                assert response.status_code == 200 and response.json()["status"] == "cancelled"
        gate = threading.Lock()
        active = 0
        peak = 0
        generations = 0

        class DelayedFake(FixtureGenerator):
            def generate(self, claim: Claim) -> dict[str, Any]:
                nonlocal active, peak, generations
                with gate:
                    active += 1
                    peak = max(peak, active)
                    generations += 1
                try:
                    time.sleep(0.05)
                    return super().generate(claim)
                finally:
                    with gate:
                        active -= 1

        processing = time.monotonic()
        for _ in range(2):
            worker = Worker(service, DelayedFake(database, ROOT))
            thread = threading.Thread(target=worker.run, args=(stop,), daemon=True)
            thread.start()
            threads.append(thread)
        reads: list[float] = []
        statuses: dict[str, int] = {}
        with httpx.Client(base_url=base, timeout=20, trust_env=False) as client:
            deadline = time.monotonic() + 30
            while True:
                statuses = {}
                for row in accepted:
                    begin = time.monotonic()
                    response = client.get(f"/api/v1/runs/{row['run_id']}")
                    reads.append(time.monotonic() - begin)
                    response.raise_for_status()
                    status = response.json()["status"]
                    statuses[status] = statuses.get(status, 0) + 1
                if statuses == {"cancelled": 8, "succeeded": 24}:
                    break
                if time.monotonic() > deadline:
                    raise RuntimeError("synthetic workload did not finish")
                time.sleep(0.01)
            for row in accepted:
                duplicate = client.post(
                    "/api/v1/runs", json=row["payload"], headers={"Idempotency-Key": row["key"]}
                )
                assert duplicate.status_code == 202
                assert duplicate.json()["run_id"] == row["run_id"]
        with database.transaction() as session:
            assert session.scalar(select(func.count()).select_from(GenerationRun)) == 32
            assert session.scalar(select(func.count()).select_from(IntakeSession)) == 24
        assert peak <= 2 and generations == 24
        report = {
            "version": "m3-load.v1",
            "timestamp": datetime.now(UTC).isoformat(),
            "workload": WORKLOAD,
            "real_model_calls": 0,
            "final_statuses": statuses,
            "accepted": 32,
            "capacity_rejections": 32,
            "duplicate_aliases_reused": 32,
            "terminal_request_replays_reused": 32,
            "generations": generations,
            "peak_fake_generations": peak,
            "post_run_latency": percentiles([r["seconds"] for r in submissions]),
            "get_snapshot_latency": percentiles(reads),
            "snapshot_reads": len(reads),
            "processing_seconds": round(time.monotonic() - processing, 3),
            "total_seconds": round(time.monotonic() - started, 3),
            "assertions_passed": True,
            "database_version": "18.6",
            "limits": (
                "64 synthetic spaces; fake delay is not real model latency; no throughput claim"
            ),
        }
        target = ROOT / ".data/qa/m3-load.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps(report, ensure_ascii=False))
    finally:
        stop.set()
        if server:
            server.should_exit = True
        for thread in threads:
            thread.join(timeout=5)
        database.close()
        with control.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        control.dispose()


if __name__ == "__main__":
    main()
