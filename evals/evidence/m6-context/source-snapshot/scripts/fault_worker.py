"""Dedicated fake process checkpoints for PostgreSQL crash-window acceptance."""

import argparse
import os
import threading
from pathlib import Path

from unfold.app.config import AppSettings
from unfold.app.db import Database
from unfold.app.generation import FixtureGenerator
from unfold.app.service import RunService


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument(
        "--checkpoint",
        choices=("claimed", "response_saved", "before_commit", "committed"),
        required=True,
    )
    args = parser.parse_args()
    settings = AppSettings(
        database_url=os.environ["UNFOLD_FAULT_DATABASE_URL"], retry_base_seconds=0
    )
    database = Database(settings)
    service = RunService(database, settings)
    try:
        claim = service.claim(args.run_id)
        if not claim:
            raise SystemExit("no execution slot")
        if args.checkpoint != "claimed":
            proposal = FixtureGenerator(database, Path(__file__).resolve().parents[1]).generate(
                claim
            )
            if args.checkpoint == "before_commit":
                emit = service.emit

                def stop_before_commit(session: object, run: object, kind: str) -> None:
                    emit(session, run, kind)
                    if kind == "completed":
                        session.flush()
                        print("CHECKPOINT_READY", flush=True)
                        threading.Event().wait(30)

                service.emit = stop_before_commit
                assert service.finish_success(claim, proposal)
            elif args.checkpoint == "committed":
                assert service.finish_success(claim, proposal)
        print("CHECKPOINT_READY", flush=True)
        # The test kills this process, retaining the actual committed database state.
        threading.Event().wait(30)
    finally:
        database.close()


if __name__ == "__main__":
    main()
