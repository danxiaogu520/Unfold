"""Read-only restore comparison: all public rows, formal files and actual HTTP."""

import argparse
import hashlib
import json
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import inspect, select, text
from sqlalchemy.engine import make_url

from unfold.app.api import create_app
from unfold.app.config import AppSettings, load_app_env
from unfold.app.db import Database
from unfold.app.files import FileStore, StoredFile
from unfold.app.models import BudgetLedger, ResourceCandidate, ResourceRevision, Space

ROOT = Path(__file__).resolve().parents[1]


def rows(database: Database) -> dict:
    result = {}
    with database.engine.connect() as connection:
        for name in sorted(inspect(connection).get_table_names(schema="public")):
            quoted = connection.dialect.identifier_preparer.quote(name)
            digest, count = hashlib.sha256(), 0
            for row in connection.execute(
                text(f"SELECT to_jsonb(t)::text FROM public.{quoted} t ORDER BY to_jsonb(t)::text")
            ):
                digest.update(row[0].encode() + b"\0")
                count += 1
            result[name] = {"rows": count, "sha256": digest.hexdigest()}
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--data-directory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.database.startswith("unfold_restore_") or args.output.exists():
        raise SystemExit("Use a new restore target and new report")
    load_app_env(ROOT / ".env.app.local")
    config = AppSettings.from_env()
    restored_config = replace(
        config,
        database_url=make_url(config.database_url)
        .set(database=args.database)
        .render_as_string(hide_password=False),
        data_directory=args.data_directory,
        model_mode="fake",
    )
    source, restored = Database(config), Database(restored_config)
    try:
        before, after = rows(source), rows(restored)
        assert before == after, "Public database rows differ"
        files = 0
        with restored.transaction() as session:
            ledger = session.get(BudgetLedger, "model")
            assert ledger
            cost, limit = str(ledger.accounted_cost), str(ledger.limit_cost)
            for saved in session.scalars(select(ResourceRevision)):
                candidate = session.get(ResourceCandidate, saved.candidate_id)
                assert candidate
                stored = StoredFile(**candidate.file)
                assert FileStore(restored_config.data_directory).verify(stored), saved.id
                files += 1
            space = session.scalar(select(Space).where(Space.root_id.is_not(None)))
            assert space
            space_id = space.id
        with TestClient(create_app(restored_config, restored)) as client:
            health = client.get("/api/health")
            assert health.status_code == 200 and health.json()["model_mode"] == "fake"
            saved_space = client.get("/api/v1/spaces/" + space_id)
            assert saved_space.status_code == 200 and saved_space.json()["root_id"]
        report = {
            "version": "restore-verification.v1",
            "target_database": args.database,
            "all_public_rows_equal": True,
            "tables": before,
            "formal_files_verified": files,
            "restored_http_read": True,
            "new_model_calls": 0,
            "ledger_accounted_cny": cost,
            "ledger_limit_cny": limit,
            "credentials_in_report": False,
        }
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({k: v for k, v in report.items() if k != "tables"}))
    finally:
        source.close()
        restored.close()


if __name__ == "__main__":
    main()
