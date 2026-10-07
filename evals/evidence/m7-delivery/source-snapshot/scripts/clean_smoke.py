"""HTTP + independent worker smoke in a fresh install and disposable schema."""

import json
import os
import socket
import threading
import time
from pathlib import Path
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
from unfold.app.models import ModelCall
from unfold.app.service import RunService
from unfold.app.worker import Worker

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    source_url = os.environ["UNFOLD_TEST_DATABASE_URL"]
    schema = "clean_" + uuid4().hex
    control = create_engine(source_url)
    with control.begin() as connection:
        connection.execute(CreateSchema(schema))
    url = make_url(source_url).update_query_dict({"options": "-csearch_path=" + schema})
    config = AppSettings(
        database_url=url.render_as_string(hide_password=False), data_directory=ROOT / ".data"
    )
    database = Database(config)
    stop = threading.Event()
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = None
    threads = []
    try:
        migrate(database)
        service = RunService(database, config)
        worker = Worker(service, FixtureGenerator(database, ROOT))
        thread = threading.Thread(target=worker.run, args=(stop,), daemon=True)
        thread.start()
        threads.append(thread)
        server = uvicorn.Server(uvicorn.Config(create_app(config, database), log_level="warning"))
        api_thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        api_thread.start()
        threads.append(api_thread)
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=5) as client:
            for _ in range(100):
                if server.started:
                    break
                time.sleep(0.05)
            assert server.started
            created = client.post(
                "/api/v1/spaces",
                json={
                    "domain": "公开清洁安装验收",
                    "user_brief": "验证本地流程，不进行真实模型推理。",
                },
            )
            assert created.status_code == 201
            space = created.json()
            response = client.post(
                "/api/v1/runs",
                headers={"Idempotency-Key": "intake"},
                json={
                    "schema_version": "api.v1",
                    "space_id": space["id"],
                    "operation": "questionnaire",
                    "profile_revision": None,
                    "base_revision": None,
                    "input": {
                        "schema_version": "questionnaire-input.v1",
                        "domain": space["domain"],
                        "user_brief": space["user_brief"],
                        "known_targets": ["purpose"],
                        "previous_question_keys": [],
                        "questions_asked": 0,
                        "round": 1,
                    },
                },
            )
            assert response.status_code == 202

            def saved_until(predicate):
                for _ in range(200):
                    saved = client.get("/api/v1/spaces/" + space["id"]).json()
                    if predicate(saved):
                        return saved
                    time.sleep(0.05)
                raise AssertionError("fresh install flow did not complete")

            saved = saved_until(lambda value: value["intakes"])
            intake = saved["intakes"][0]
            response = client.post(
                "/api/v1/spaces/" + space["id"] + "/intake",
                headers={"Idempotency-Key": "profile"},
                json={
                    "operation": "profile",
                    "expected_intake_id": intake["id"],
                    "answers": [
                        {
                            "question_key": q["key"],
                            "selected_keys": [],
                            "free_text": None,
                            "unknown": True,
                        }
                        for q in intake["questionnaire"]["questions"]
                    ],
                },
            )
            assert response.status_code == 202
            saved = saved_until(lambda value: value["root_id"])
            root = next(n for n in saved["nodes"] if n["id"] == saved["root_id"])
            assert len(root["children"]) == 2
            response = client.post(
                "/api/v1/spaces/" + space["id"] + "/actions",
                headers={"Idempotency-Key": "practice"},
                json={
                    "operation": "exercises",
                    "expected_profile_revision": 1,
                    "expected_base_revision": 1,
                    "target_id": root["id"],
                    "count": 3,
                },
            )
            assert response.status_code == 202
            saved = saved_until(
                lambda value: next(n for n in value["nodes"] if n["id"] == value["root_id"])[
                    "exercise_sets"
                ]
            )
            root = next(n for n in saved["nodes"] if n["id"] == saved["root_id"])
            questions = root["exercise_sets"][0]["questions"]
            assert len(questions) == 3 and "correct_keys" not in json.dumps(questions)
            result = client.post(
                f"/api/v1/spaces/{space['id']}/questions/{questions[0]['exercise_revision_ref']}/answers",
                headers={"Idempotency-Key": "answer"},
                json={"selected_keys": ["scope"]},
            )
            assert result.status_code == 201 and result.json()["correct"]
        with database.transaction() as session:
            real_calls = session.scalar(
                select(func.count()).select_from(ModelCall).where(ModelCall.provider == "deepseek")
            )
        assert real_calls == 0
        (ROOT / "smoke-result.json").write_text(
            json.dumps(
                {
                    "empty_schema_migrated": True,
                    "actual_http_and_worker": True,
                    "automatic_outline": True,
                    "saved_exercises": 3,
                    "answer_grading": True,
                    "real_model_calls": 0,
                    "temporary_schema_removed_on_exit": True,
                },
                indent=2,
            )
            + "\n"
        )
    finally:
        stop.set()
        if server:
            server.should_exit = True
        for thread in threads:
            thread.join(timeout=5)
        sock.close()
        database.close()
        with control.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        control.dispose()


if __name__ == "__main__":
    main()
