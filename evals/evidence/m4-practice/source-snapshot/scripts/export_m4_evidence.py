"""Freeze real M4 calls, local corrections and matching deterministic evidence."""

import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESTINATION = ROOT / "evals/evidence/m4-practice"


def main() -> None:
    if DESTINATION.exists():
        raise SystemExit("Never overwrite historical evidence")
    DESTINATION.mkdir(parents=True)
    for batch in ("m4-live-v1", "m4-reference-correction-v1"):
        shutil.copytree(ROOT / "evals/runs" / batch, DESTINATION / batch)
    for source in sorted((ROOT / ".data/qa").glob("m4-process-*.json")):
        shutil.copyfile(source, DESTINATION / source.name)
    for name in (
        "checks.json",
        "python.xml",
        "m4-delivery-check.log",
        "m4-schema-validation.log",
        "m4-rendering.png",
        "m4-practice.png",
        "m4-diagram.png",
        "m4-diagram.svg",
        "m4-npm-audit.json",
        "m4-question-checks.json",
        "m4-image-source.json",
    ):
        shutil.copyfile(ROOT / ".data/qa" / name, DESTINATION / name)
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
        *ROOT.glob("evals/assets/m4/*"),
        *ROOT.glob(".github/workflows/*.yml"),
        *ROOT.glob("docs/reports/m4-*.md"),
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
            "contracts/README.md",
            "contracts/toolchain.v1.json",
            "web/index.html",
            "docs/development.md",
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
        json.dumps({"version": "m4-manifest.v1", "files": files}, ensure_ascii=False, indent=2)
        + "\n"
    )
    print(json.dumps({"files": len(files), "real_calls": 4, "local_corrections": 2}))


if __name__ == "__main__":
    main()
