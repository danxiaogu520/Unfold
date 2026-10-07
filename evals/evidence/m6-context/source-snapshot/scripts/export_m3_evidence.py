"""Freeze M3 finite fault-matrix results and the matching implementation."""

import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESTINATION = ROOT / "evals/evidence/m3-reliability"


def main() -> None:
    if DESTINATION.exists():
        raise SystemExit("Use a new evidence version instead of overwriting history")
    DESTINATION.mkdir(parents=True)
    for source in [
        *sorted((ROOT / ".data/qa").glob("m3-*.json")),
        *[
            ROOT / ".data/qa" / name
            for name in ("checks.json", "python.xml", "m3-reconnect.png", "m3-delivery-check.log")
        ],
    ]:
        shutil.copyfile(source, DESTINATION / source.name)
    paths = [
        *ROOT.glob("src/**/*.py"),
        *ROOT.glob("src/**/*.sql"),
        *ROOT.glob("scripts/*.py"),
        *ROOT.glob("tests/*.py"),
        *ROOT.glob("web/src/*"),
        *ROOT.glob("web/tests/*.ts"),
        *ROOT.glob("web/*.json"),
        *ROOT.glob("web/*.ts"),
        *ROOT.glob("contracts/schemas/*.json"),
        *ROOT.glob("evals/prompts/*.json"),
        *ROOT.glob("evals/contracts/*.json"),
        *ROOT.glob("evals/datasets/*.json"),
        *ROOT.glob(".github/workflows/*.yml"),
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
            "docs/decisions/0002-budget-authorization.md",
            "docs/reports/m3-reliability-2026-10-07.md",
            "web/index.html",
            "contracts/toolchain.v1.json",
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
        json.dumps({"version": "m3-manifest.v1", "files": files}, ensure_ascii=False, indent=2)
        + "\n"
    )
    print(json.dumps({"files": len(files), "real_model_calls": 0}))


if __name__ == "__main__":
    main()
