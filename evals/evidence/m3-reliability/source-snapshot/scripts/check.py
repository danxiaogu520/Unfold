"""The same deterministic acceptance entry point is used locally and in CI."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", action="store_true")
    args = parser.parse_args()
    if not os.environ.get("UNFOLD_TEST_DATABASE_URL"):
        raise SystemExit(
            "Set UNFOLD_TEST_DATABASE_URL; required database tests must not be skipped"
        )
    executables = Path(sys.executable).parent
    commands = [
        [str(executables / "ruff"), "check", "src", "scripts", "tests"],
        [str(executables / "ruff"), "format", "--check", "src", "scripts", "tests"],
        [str(executables / "mypy"), "src"],
        [sys.executable, "-m", "unfold.cli", "check"],
        [sys.executable, "-m", "unfold.cli", "verify-evidence"],
        [
            sys.executable,
            "-m",
            "pytest",
            "--require-postgres",
            "-q",
            "--junitxml=.data/qa/python.xml",
        ],
        ["npm", "run", "build", "--prefix", "web"],
    ]
    if args.browser:
        commands.append(["npm", "test", "--prefix", "web"])
    for directory, script in (
        ("m1-foundation", "verify_foundation_evidence.py"),
        ("m2-learning", "verify_learning_evidence.py"),
        ("m3-reliability", "verify_reliability_evidence.py"),
    ):
        if (ROOT / "evals/evidence" / directory).exists():
            commands.append([sys.executable, "scripts/" + script])
    report: dict[str, object] = {
        "started_at": datetime.now(UTC).isoformat(),
        "real_model_calls": False,
        "checks": [],
    }
    checks = []
    target = ROOT / ".data/qa/checks.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    for command in commands:
        started = time.monotonic()
        result = subprocess.run(command, cwd=ROOT, check=False)
        checks.append(
            {
                "command": [Path(command[0]).name, *command[1:]],
                "exit_code": result.returncode,
                "seconds": round(time.monotonic() - started, 3),
            }
        )
        report["checks"] = checks
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        if result.returncode:
            raise SystemExit(result.returncode)
    print(f"All {len(checks)} deterministic checks passed; report: {target}")


if __name__ == "__main__":
    main()
