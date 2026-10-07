"""Actual process-death windows spanning file writes and resource publication."""

import argparse
import os
import threading
from pathlib import Path

from unfold.app.config import AppSettings
from unfold.app.db import Database
from unfold.app.files import FileStore
from unfold.app.fixtures import CachedCommons
from unfold.app.resources import ImageExecutor
from unfold.app.service import RunService

ROOT = Path(__file__).resolve().parents[1]


def checkpoint() -> None:
    print("CHECKPOINT_READY", flush=True)
    threading.Event().wait(30)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument(
        "--checkpoint",
        required=True,
        choices=("file_saved", "candidate_saved", "before_commit", "committed"),
    )
    args = parser.parse_args()
    config = AppSettings(
        database_url=os.environ["UNFOLD_FAULT_DATABASE_URL"],
        data_directory=Path(os.environ["UNFOLD_FAULT_FILES"]),
        retry_base_seconds=0,
    )
    database = Database(config)
    service = RunService(database, config)
    try:
        claim = service.claim(args.run_id)
        assert claim
        if args.checkpoint == "file_saved":
            write = FileStore.write_image

            def stop_after_write(store: FileStore, data: bytes) -> object:
                result = write(store, data)
                checkpoint()
                return result

            FileStore.write_image = stop_after_write
        proposal = ImageExecutor(service, lambda: CachedCommons(ROOT)).generate(claim)
        if args.checkpoint == "candidate_saved":
            checkpoint()
        if args.checkpoint == "before_commit":
            emit = service.emit

            def stop_before_commit(session: object, run: object, kind: str) -> None:
                emit(session, run, kind)
                if kind == "completed":
                    session.flush()
                    checkpoint()

            service.emit = stop_before_commit
        assert service.finish_success(claim, proposal)
        checkpoint()
    finally:
        database.close()


if __name__ == "__main__":
    main()
