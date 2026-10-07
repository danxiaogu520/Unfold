"""Disposable PostgreSQL schema + fake worker for real HTTP browser acceptance."""

from __future__ import annotations

import os
import shutil
import threading
from pathlib import Path
from typing import Any
from uuid import uuid4

import uvicorn
from sqlalchemy import create_engine, select
from sqlalchemy.engine import make_url
from sqlalchemy.schema import CreateSchema, DropSchema

from unfold.app.api import create_app
from unfold.app.config import AppSettings
from unfold.app.db import Database
from unfold.app.fixtures import CachedCommons
from unfold.app.generation import FixtureGenerator
from unfold.app.migrate import migrate
from unfold.app.models import ModelCall
from unfold.app.resources import ApplicationGenerator, ImageExecutor
from unfold.app.service import Claim, RunService
from unfold.app.worker import Worker
from unfold.contracts import validate_pair

ROOT = Path(__file__).resolve().parents[1]


class BrowserGenerator(FixtureGenerator):
    def generate(self, claim: Claim) -> dict[str, Any]:
        raw = super().generate(claim)
        if claim.operation == "explanation" and "M4 渲染验收" in claim.input["instruction"]:
            raw["blocks"] = [
                {
                    "type": "markdown",
                    "text": (
                        "## 公式与代码\n\n中点：$m=l+\\lfloor(r-l)/2\\rfloor$。\n\n"
                        "```python\nmid = left + (right - left) // 2\n```\n\n"
                        "<script>window.unfoldInjected=true</script>\n\n"
                        "[不安全链接](javascript:alert(1))"
                    ),
                },
                {
                    "type": "diagram",
                    "language": "mermaid",
                    "source": "flowchart LR\nA[检查区间]-->B[比较中点]\nB-->C[缩小区间]\nC-->A",
                    "caption": "二分查找流程",
                    "fallback_text": "检查区间、比较中点、缩小区间。",
                },
                {
                    "type": "diagram",
                    "language": "mermaid",
                    "source": "invalid diagram syntax",
                    "caption": "错误图示",
                    "fallback_text": "错误图示的文字仍然可读。",
                },
                {
                    "type": "diagram",
                    "language": "mermaid",
                    "source": '%%{init: {"securityLevel":"loose"}}}%%\nflowchart LR\nA-->B',
                    "caption": "能力边界图示",
                    "fallback_text": "越界图示保留文字替代。",
                },
                {
                    "type": "image",
                    "resource_key": "commons",
                    "caption": "二分查找示意图",
                    "fallback_text": "参考有序区间缩小过程。",
                },
            ]
            raw["resource_requests"] = [
                {
                    "key": "commons",
                    "kind": "image",
                    "purpose": "说明区间缩小",
                    "specification": "binary search",
                    "runtime": None,
                    "controls": [],
                    "fallback_text": "参考有序区间缩小过程。",
                }
            ]
            validate_pair(claim.operation, claim.input, raw)
            with self.database.transaction() as session:
                call = session.scalar(
                    select(ModelCall).where(
                        ModelCall.run_id == claim.run_id,
                        ModelCall.attempt == claim.attempt,
                        ModelCall.step == 1,
                    )
                )
                assert call
                call.response = raw
        return raw


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
    resource_directory = ROOT / ".data/browser" / schema
    config = AppSettings(
        database_url=isolated.render_as_string(hide_password=False),
        data_directory=resource_directory,
    )
    database = Database(config)
    stop = threading.Event()
    thread: threading.Thread | None = None
    try:
        migrate(database)
        service = RunService(database, config)
        generator = ApplicationGenerator(
            BrowserGenerator(database, ROOT), ImageExecutor(service, lambda: CachedCommons(ROOT))
        )
        worker = Worker(service, generator)
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
        shutil.rmtree(resource_directory, ignore_errors=True)


if __name__ == "__main__":
    main()
