"""Freeze actual resource comparisons, failed/successful recovery and current sources."""

import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESTINATION = ROOT / "evals/evidence/m5-agent"


def main() -> None:
    if DESTINATION.exists():
        raise SystemExit("Use a new version; every trial is historical evidence")
    DESTINATION.mkdir(parents=True)
    for batch in (
        "m5-public-comparison-v1",
        "m5-public-comparison-v2",
        "m5-public-recovery-v1",
        "m5-public-recovery-v2",
    ):
        shutil.copytree(ROOT / "evals/runs" / batch, DESTINATION / batch)
    for name in (
        "checks.json",
        "python.xml",
        "m5-delivery-check.log",
        "m5-isolation.json",
        "m5-component-check.json",
        "m5-runtime-ui.png",
    ):
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
        *ROOT.glob("evals/assets/m4/*"),
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
            "contracts/README.md",
            "contracts/toolchain.v1.json",
            "web/index.html",
            "docs/development.md",
            "docs/decisions/0003-managed-resource-browser.md",
            "docs/reports/m5-resource-agent-2026-10-07.md",
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
        json.dumps({"version": "m5-manifest.v1", "files": files}, ensure_ascii=False, indent=2)
        + "\n"
    )
    print(json.dumps({"files": len(files), "real_calls": 42}))


if __name__ == "__main__":
    main()
