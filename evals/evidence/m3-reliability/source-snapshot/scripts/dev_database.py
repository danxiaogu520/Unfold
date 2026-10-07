"""Manage only the project-local PostgreSQL cluster, never a global service."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / ".data/postgres"
SOCKETS = ROOT / ".data/sockets"
PORT = "54318"


def binary(name: str) -> str:
    candidates = [
        Path(os.environ.get("UNFOLD_PG_BIN", ROOT / ".tools/postgres/bin")) / name,
        Path("/opt/homebrew/opt/postgresql@18/bin") / name,
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    found = shutil.which(name)
    if found:
        return found
    raise SystemExit("PostgreSQL 18.6 missing; set UNFOLD_PG_BIN to its bin directory")


def run(name: str, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run([binary(name), *args], check=check, text=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("start", "stop", "status"))
    args = parser.parse_args()
    if args.command == "status":
        raise SystemExit(run("pg_ctl", "-D", str(DATA), "status", check=False).returncode)
    if args.command == "stop":
        run("pg_ctl", "-D", str(DATA), "stop", "-m", "fast")
        return
    version = subprocess.check_output([binary("postgres"), "--version"], text=True)
    if "18.6" not in version.split():
        raise SystemExit("This toolchain requires PostgreSQL 18.6")
    SOCKETS.mkdir(parents=True, exist_ok=True)
    if not (DATA / "PG_VERSION").exists():
        run(
            "initdb",
            "-D",
            str(DATA),
            "-U",
            "unfold",
            "--auth=trust",
            "--encoding=UTF8",
            "--locale=C",
        )
    status = subprocess.run([binary("pg_ctl"), "-D", str(DATA), "status"], capture_output=True)
    if status.returncode:
        run(
            "pg_ctl",
            "-D",
            str(DATA),
            "-l",
            str(ROOT / ".data/postgres.log"),
            "-o",
            f"-p {PORT} -h 127.0.0.1 -k {SOCKETS}",
            "start",
            "-w",
        )
    databases = subprocess.check_output(
        [
            binary("psql"),
            "-h",
            "127.0.0.1",
            "-p",
            PORT,
            "-U",
            "unfold",
            "-d",
            "postgres",
            "-Atc",
            "SELECT datname FROM pg_database",
        ],
        text=True,
    ).splitlines()
    if "unfold" not in databases:
        run("createdb", "-h", "127.0.0.1", "-p", PORT, "-U", "unfold", "unfold")
    print("Ready: postgresql+psycopg://unfold@127.0.0.1:54318/unfold")


if __name__ == "__main__":
    main()
