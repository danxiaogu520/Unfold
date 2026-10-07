"""Local database/public-schema + immutable resource backup; no model credentials."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from dev_database import binary
from sqlalchemy import func, select
from sqlalchemy.engine import make_url

from unfold.app.config import AppSettings, load_app_env
from unfold.app.db import Database
from unfold.app.models import GenerationRun

ROOT = Path(__file__).resolve().parents[1]


def connection(config: AppSettings) -> tuple[dict[str, str], str]:
    url = make_url(config.database_url)
    if url.host not in ("127.0.0.1", "localhost"):
        raise SystemExit("This backup tool only supports local PostgreSQL")
    environment = {k: os.environ[k] for k in ("PATH", "HOME", "TMPDIR", "LANG") if k in os.environ}
    environment.update(PGHOST=url.host, PGPORT=str(url.port or 5432), PGUSER=url.username or "")
    if url.password:
        environment["PGPASSWORD"] = url.password
    return environment, str(url.database)


def verify(directory: Path) -> dict:
    manifest = json.loads((directory / "manifest.json").read_text())
    for name, expected in manifest["files"].items():
        path = (directory / name).resolve()
        if not path.is_relative_to(directory.resolve()) or not path.is_file():
            raise SystemExit("Backup path missing or invalid")
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise SystemExit("Backup hash mismatch")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-env", type=Path, default=ROOT / ".env.app.local")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="Stop API/worker before creating backup")
    create.add_argument("--output", type=Path, required=True)
    restore = commands.add_parser(
        "restore", help="Restore only to a NEW database and data directory"
    )
    restore.add_argument("backup", type=Path)
    restore.add_argument("--database", required=True)
    restore.add_argument("--data-directory", type=Path, required=True)
    args = parser.parse_args()
    load_app_env(args.app_env)
    config = AppSettings.from_env()
    environment, database = connection(config)
    if args.command == "create":
        app_database = Database(config)
        try:
            with app_database.transaction() as session:
                running = session.scalar(
                    select(func.count())
                    .select_from(GenerationRun)
                    .where(GenerationRun.status == "running")
                )
                if running:
                    raise SystemExit("Stop writers and resolve running attempts before backup")
        finally:
            app_database.close()
        output = args.output.resolve()
        if output.exists():
            raise SystemExit("Backup output must be new")
        output.mkdir(parents=True, mode=0o700)
        subprocess.run(
            [
                binary("pg_dump"),
                "--format=custom",
                "--compress=0",
                "--no-owner",
                "--no-acl",
                "--schema=public",
                "--dbname=" + database,
                "--file=" + str(output / "database.dump"),
            ],
            env=environment,
            check=True,
        )
        resource_root = config.data_directory.resolve() / "resources"
        for source in sorted((resource_root / "files").glob("*")):
            if source.is_symlink():
                raise SystemExit("Resource symlink cannot enter backup")
            if not re.fullmatch(r"[0-9a-f]{64}\.(png|jpg|webp|html)", source.name):
                continue  # unfinished atomic-write temporary files are not references
            target = output / "resources/files" / source.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            if hashlib.sha256(target.read_bytes()).hexdigest() != source.stem:
                raise SystemExit("Stored resource hash mismatch")
        files = {
            str(p.relative_to(output)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in output.rglob("*")
            if p.is_file()
        }
        (output / "manifest.json").write_text(
            json.dumps(
                {
                    "version": "local-backup.v1",
                    "created_at": datetime.now(UTC).isoformat(),
                    "source_database": database,
                    "schema": "public",
                    "requires_writers_stopped": True,
                    "model_credentials_included": False,
                    "files": files,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )
        verify(output)
        print(json.dumps({"backup": str(output), "files": len(files)}))
        return
    backup = args.backup.resolve()
    manifest = verify(backup)
    if manifest["version"] != "local-backup.v1" or manifest["schema"] != "public":
        raise SystemExit("Unsupported backup")
    if not re.fullmatch(r"unfold_restore_[a-z0-9_]{1,48}", args.database):
        raise SystemExit("Target must be a new unfold_restore_ database")
    target = args.data_directory.resolve()
    if target.exists() or args.database == database:
        raise SystemExit("Restore cannot overwrite existing data")
    # createdb fails if the target exists; neither --clean nor DROP is used.
    subprocess.run([binary("createdb"), args.database], env=environment, check=True)
    target.mkdir(parents=True, mode=0o700)
    table_of_contents = subprocess.run(
        [binary("pg_restore"), "--list", str(backup / "database.dump")],
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    # A fresh PostgreSQL template already has public. Skip only that CREATE
    # SCHEMA entry; restore all tables, constraints and rows without DROP.
    selected = [
        "; " + line if re.search(r"\bSCHEMA - public\b", line) else line
        for line in table_of_contents.splitlines()
    ]
    restore_list = target / "restore.list"
    restore_list.write_text("\n".join(selected) + "\n")
    subprocess.run(
        [
            binary("pg_restore"),
            "--exit-on-error",
            "--no-owner",
            "--no-acl",
            "--use-list=" + str(restore_list),
            "--dbname=" + args.database,
            str(backup / "database.dump"),
        ],
        env=environment,
        check=True,
    )
    if (backup / "resources").exists():
        shutil.copytree(backup / "resources", target / "resources")
    print(
        json.dumps(
            {
                "database": args.database,
                "data_directory": str(target),
                "credentials_restored": False,
                "backup_files": len(manifest["files"]),
            }
        )
    )


if __name__ == "__main__":
    main()
