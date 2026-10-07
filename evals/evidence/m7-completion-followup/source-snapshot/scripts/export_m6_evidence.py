"""Freeze context comparison, selection snapshots and deterministic checks."""

import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESTINATION = ROOT / "evals/evidence/m6-context"


def main() -> None:
    if DESTINATION.exists():
        raise SystemExit("Use a new evidence version; never overwrite a frozen comparison")
    report = json.loads((ROOT / "evals/runs/m6-heldout-v1/report.json").read_text())
    assert len(report["trials"]) == 72 and not report["unknown_usage_calls"]
    DESTINATION.mkdir(parents=True)
    shutil.copytree(ROOT / "evals/runs/m6-heldout-v1", DESTINATION / "heldout-v1")
    for name in ("checks.json", "python.xml", "m6-final-check.log", "m6-cache-check.log"):
        shutil.copyfile(ROOT / ".data/qa" / name, DESTINATION / name)
    paths = [
        *ROOT.glob("src/**/*.py"),
        *ROOT.glob("src/**/*.sql"),
        *ROOT.glob("src/**/*.html"),
        *ROOT.glob("scripts/*.py"),
        *ROOT.glob("tests/*.py"),
        *ROOT.glob("web/src/*"),
        *ROOT.glob("web/tests/*.ts"),
        *ROOT.glob("web/runtime/*.mjs"),
        *ROOT.glob("web/*.json"),
        *ROOT.glob("web/*.ts"),
        *ROOT.glob("contracts/schemas/*.json"),
        *ROOT.glob("evals/prompts/*.json"),
        *ROOT.glob("evals/contracts/*.json"),
        *ROOT.glob("evals/datasets/*.json"),
        *ROOT.glob("docs/decisions/*.md"),
    ]
    paths += [
        ROOT / name
        for name in (
            "pyproject.toml",
            "uv.lock",
            ".python-version",
            ".gitignore",
            ".env.example",
            ".env.app.example",
            "README.md",
            "PRODUCT.md",
            "DESIGN.md",
            "PLAN.md",
            "docs/development.md",
            "docs/reports/m6-context-2026-10-07.md",
        )
    ]
    for source in sorted(set(paths)):
        target = DESTINATION / "source-snapshot" / source.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    files = {
        str(p.relative_to(DESTINATION)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(DESTINATION.rglob("*"))
        if p.is_file()
    }
    (DESTINATION / "manifest.json").write_text(
        json.dumps({"version": "m6-manifest.v1", "files": files}, ensure_ascii=False, indent=2)
        + "\n"
    )
    print(json.dumps({"files": len(files), "real_calls": report["real_calls"]}))


if __name__ == "__main__":
    main()
