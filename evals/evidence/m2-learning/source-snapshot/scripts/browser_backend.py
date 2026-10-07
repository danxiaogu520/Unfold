"""Disposable PostgreSQL schema + fake worker for real HTTP browser acceptance."""

from __future__ import annotations

import os
import threading
from pathlib import Path
from uuid import uuid4

import uvicorn
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.schema import CreateSchema, DropSchema

from unfold.app.api import create_app
from unfold.app.config import AppSettings
from unfold.app.db import Database
from unfold.app.generation import FixtureGenerator
from unfold.app.migrate import migrate
from unfold.app.service import RunService
from unfold.app.worker import Worker

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    url = os.environ.get("UNFOLD_TEST_DATABASE_URL")
    if not url:
        raise SystemExit(
            "UNFOLD_TEST_DATABASE_URL required; browser tests never use live model mode"
        )
    schema = "browser_" + uuid4().hex
    control = create_engine(url)
    with control.begin() as connection:
        connection.execute(CreateSchema(schema))
    isolated = make_url(url).update_query_dict({"options": f"-csearch_path={schema}"})
    config = AppSettings(database_url=isolated.render_as_string(hide_password=False))
    database = Database(config)
    stop = threading.Event()
    thread: threading.Thread | None = None
    try:
        migrate(database)
        worker = Worker(RunService(database, config), FixtureGenerator(database, ROOT))
        thread = threading.Thread(target=worker.run, args=(stop,), daemon=True)
        thread.start()
        uvicorn.run(create_app(config, database), host="127.0.0.1", port=8000, log_level="warning")
    finally:
        stop.set()
        if thread:
            thread.join(timeout=5)
        database.close()
        with control.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        control.dispose()


if __name__ == "__main__":
    main()
