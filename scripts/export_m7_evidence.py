"""Freeze final engineering evidence without private backup data or credentials."""

import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESTINATION = ROOT / "evals/evidence/m7-delivery"


def main() -> None:
    if DESTINATION.exists():
        raise SystemExit("Never overwrite frozen delivery evidence")
    DESTINATION.mkdir(parents=True)
    names = (
        "checks.json",
        "python.xml",
        "m7-release-check.log",
        "m7-restore-final-verification.json",
        "m7-restore-verification.log",
        "m7-backup.log",
        "m7-restore-v2.log",
        "m7-clean-install.json",
        "m7-clean-install-step1.log",
        "m7-clean-install-v2.json",
        "m7-clean-install-v2-step1.log",
        "m7-clean-install-v2-step2.log",
        "m7-clean-install-v2-step3.log",
        "m7-clean-install-v2-step4.log",
        "m7-working-copy-check.log",
        "m7-continuation-budget-check.log",
        "m7-native-practice.png",
        "m3-load.json",
        "m3-cancel-race.json",
        "m3-process-claimed.json",
        "m3-process-response_saved.json",
        "m3-process-before_commit.json",
        "m3-process-committed.json",
        "m7-demo-viewer.html",
        "m7-clean-install-v3.json",
        "m7-clean-install-v3-step1.log",
        "m7-clean-install-v3-step2.log",
        "m7-clean-install-v3-step3.log",
        "m7-clean-install-v3-step4.log",
        "m7-clean-install-v3-step5.log",
        "m7-backup-final.log",
        "m7-restore-final.log",
        "m7-restore-final-verification.log",
        "m7-domain-final-check.log",
    )
    for name in names:
        shutil.copyfile(ROOT / ".data/qa" / name, DESTINATION / name)
    shutil.copytree(ROOT / ".data/qa/m7-demos", DESTINATION / "recordings")
    shutil.copytree(
        ROOT / "evals/runs/m7-public-domain-entry-v1", DESTINATION / "public-domain-entry"
    )
    paths = [
        *ROOT.glob("src/**/*.py"),
        *ROOT.glob("src/**/*.sql"),
        *ROOT.glob("src/**/*.html"),
        *ROOT.glob("scripts/*.py"),
        *ROOT.glob("tests/*.py"),
        *ROOT.glob("web/src/*"),
        *ROOT.glob("web/tests/*.ts"),
        *ROOT.glob("web/runtime/*"),
        *ROOT.glob("web/*.json"),
        *ROOT.glob("web/*.ts"),
        *ROOT.glob("contracts/schemas/*.json"),
        *ROOT.glob("evals/prompts/*.json"),
        *ROOT.glob("evals/contracts/*.json"),
        *ROOT.glob("evals/datasets/*.json"),
        *ROOT.glob("evals/assets/m4/*"),
        *ROOT.glob("docs/**/*.md"),
        *ROOT.glob(".github/workflows/*.yml"),
    ]
    paths += [
        ROOT / n
        for n in (
            "README.md",
            "PLAN.md",
            "PRODUCT.md",
            "DESIGN.md",
            "docs/interview.md",
            "pyproject.toml",
            "uv.lock",
            ".python-version",
            ".gitignore",
            ".env.example",
            ".env.app.example",
            "contracts/README.md",
            "contracts/toolchain.v1.json",
            "web/index.html",
        )
    ]
    for source in sorted(set(paths)):
        target = DESTINATION / "source-snapshot" / source.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    files = {
        str(p.relative_to(DESTINATION)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in DESTINATION.rglob("*")
        if p.is_file()
    }
    (DESTINATION / "manifest.json").write_text(
        json.dumps({"version": "m7-manifest.v1", "files": files}, ensure_ascii=False, indent=2)
        + "\n"
    )
    print(json.dumps({"files": len(files), "new_real_model_calls": 4}))


if __name__ == "__main__":
    main()
