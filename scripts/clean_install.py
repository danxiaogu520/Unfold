"""New source directory, fresh dependencies/build and fake HTTP application smoke."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    if args.output.exists() or not os.environ.get("UNFOLD_TEST_DATABASE_URL"):
        raise SystemExit("Use a new report and specify local test PostgreSQL")
    environment = {
        k: os.environ[k]
        for k in ("PATH", "HOME", "TMPDIR", "LANG", "TZ", "UNFOLD_TEST_DATABASE_URL")
        if k in os.environ
    }
    report = {
        "version": "clean-install.v1",
        "model_credentials_copied": False,
        "node_modules_copied": False,
        "virtual_environment_copied": False,
        "existing_system_runtimes": True,
        "real_model_calls": 0,
        "checks": [],
    }
    with tempfile.TemporaryDirectory(prefix="unfold-clean-") as temporary:
        target = Path(temporary)
        for directory in ("src", "scripts", "contracts"):
            shutil.copytree(
                ROOT / directory, target / directory, ignore=shutil.ignore_patterns("__pycache__")
            )
        shutil.copytree(
            ROOT / "web",
            target / "web",
            ignore=shutil.ignore_patterns(
                "node_modules", "dist", "test-results", "playwright-report"
            ),
        )
        for directory in ("contracts", "prompts", "datasets", "assets"):
            shutil.copytree(ROOT / "evals" / directory, target / "evals" / directory)
        for name in (
            "pyproject.toml",
            "uv.lock",
            ".python-version",
            "README.md",
            "LICENSE",
            "THIRD_PARTY_NOTICES.md",
        ):
            shutil.copyfile(ROOT / name, target / name)
        report["source_sha256"] = {
            str(p.relative_to(target)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in target.rglob("*")
            if p.is_file()
        }
        commands = [
            [
                str(ROOT / ".venv/bin/uv"),
                "sync",
                "--locked",
                "--extra",
                "app",
                "--cache-dir",
                str(ROOT / ".tools/uv-cache"),
                "--python",
                str(ROOT / ".venv/bin/python"),
            ],
            [
                "npm",
                "ci",
                "--prefix",
                "web",
                "--ignore-scripts",
                "--no-audit",
                "--no-fund",
                "--cache",
                str(ROOT / ".tools/npm-cache"),
            ],
            ["npm", "run", "build", "--prefix", "web"],
            [str(target / ".venv/bin/unfold"), "check"],
            [str(target / ".venv/bin/python"), "scripts/clean_smoke.py"],
        ]
        if args.offline:
            commands[0].insert(3, "--offline")
            commands[1].append("--offline")
        for index, command in enumerate(commands):
            began = time.monotonic()
            result = subprocess.run(
                command, cwd=target, env=environment, capture_output=True, text=True
            )
            report["checks"].append(
                {
                    "step": index + 1,
                    "command": [Path(command[0]).name, *command[1:]],
                    "exit_code": result.returncode,
                    "seconds": round(time.monotonic() - began, 3),
                }
            )
            log = args.output.with_name(args.output.stem + f"-step{index + 1}.log")
            log.write_text(result.stdout + result.stderr)
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
            if result.returncode:
                raise SystemExit("Fresh installation failed; diagnostic logs retained")
        report["smoke"] = json.loads((target / "smoke-result.json").read_text())
        report["temporary_source_removed"] = True
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "source_sha256"}))


if __name__ == "__main__":
    main()
