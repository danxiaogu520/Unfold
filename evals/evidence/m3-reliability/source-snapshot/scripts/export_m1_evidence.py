"""Export synthetic HTTP evidence on a disposable real PostgreSQL schema."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.schema import CreateSchema, DropSchema

from unfold.app.api import create_app
from unfold.app.config import AppSettings
from unfold.app.db import Database
from unfold.app.generation import FixtureGenerator
from unfold.app.migrate import migrate
from unfold.app.worker import Worker

ROOT = Path(__file__).resolve().parents[1]
DESTINATION = ROOT / "evals/evidence/m1-foundation"


def write(name: str, value: object) -> None:
    (DESTINATION / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def main() -> None:
    url = os.environ.get("UNFOLD_TEST_DATABASE_URL")
    if not url:
        raise SystemExit("UNFOLD_TEST_DATABASE_URL required")
    if DESTINATION.exists():
        raise SystemExit("Evidence exists; choose a new version instead of overwriting history")
    schema = "evidence_" + uuid4().hex
    control = create_engine(url)
    with control.begin() as connection:
        connection.execute(CreateSchema(schema))
    isolated = make_url(url).update_query_dict({"options": f"-csearch_path={schema}"})
    config = AppSettings(database_url=isolated.render_as_string(hide_password=False))
    database = Database(config)
    DESTINATION.mkdir(parents=True)
    try:
        migrate(database)
        app = create_app(config, database)
        service = app.state.service
        sample = next(
            row
            for row in json.loads((ROOT / "evals/contracts/success.v1.json").read_text())
            if row["operation"] == "questionnaire"
        )
        with TestClient(app) as client:
            for name, failure in (("success", False), ("failure", True)):
                space = client.post(
                    "/api/v1/spaces",
                    json={
                        "domain": sample["input"]["domain"],
                        "user_brief": sample["input"]["user_brief"],
                    },
                ).json()
                body = {
                    "schema_version": "api.v1",
                    "space_id": space["id"],
                    "operation": "questionnaire",
                    "profile_revision": None,
                    "base_revision": None,
                    "input": sample["input"],
                }
                queued = client.post("/api/v1/runs", json=body, headers={"Idempotency-Key": name})
                assert queued.status_code == 202
                run_id = queued.json()["run_id"]
                assert Worker(service, FixtureGenerator(database, ROOT, failure=failure)).tick()
                state = client.get(f"/api/v1/runs/{run_id}").json()
                official = client.get(f"/api/v1/spaces/{space['id']}").json()["intakes"]
                assert state["status"] == ("failed" if failure else "succeeded")
                assert len(official) == (0 if failure else 1)
                write(
                    name + ".json",
                    {
                        "evidence_kind": "deterministic_fake_real_postgres_http",
                        "request": body,
                        "queued": queued.json(),
                        "final": state,
                        "trace": client.get(f"/api/v1/runs/{run_id}/trace").json(),
                        "events": service.events(run_id),
                        "official_intakes": official,
                    },
                )
        shutil.copyfile(ROOT / ".data/qa/checks.json", DESTINATION / "checks.json")
        shutil.copyfile(ROOT / ".data/qa/python.xml", DESTINATION / "python.xml")
        for image in (ROOT / ".data/qa").glob("m1-*.png"):
            shutil.copyfile(image, DESTINATION / image.name)
        paths = [
            *ROOT.glob("src/**/*.py"),
            *ROOT.glob("src/**/*.sql"),
            *ROOT.glob("tests/*.py"),
            *ROOT.glob("scripts/*.py"),
            *ROOT.glob("web/src/*"),
            *ROOT.glob("web/tests/*.ts"),
            *ROOT.glob("web/*.json"),
            *ROOT.glob("web/*.ts"),
            *ROOT.glob(".github/workflows/*.yml"),
        ]
        paths += [
            ROOT / name for name in ("pyproject.toml", "uv.lock", "contracts/toolchain.v1.json")
        ]
        for path in sorted(set(paths)):
            target = DESTINATION / "source-snapshot" / path.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
        manifest = {
            str(path.relative_to(DESTINATION)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(DESTINATION.rglob("*"))
            if path.is_file()
        }
        write(
            "manifest.json", {"version": "m1-evidence.v1", "real_model_calls": 0, "files": manifest}
        )
        print(
            json.dumps(
                {"directory": str(DESTINATION), "files": len(manifest), "real_model_calls": 0}
            )
        )
    finally:
        database.close()
        with control.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        control.dispose()


if __name__ == "__main__":
    main()
