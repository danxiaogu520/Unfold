"""Freeze the completion-audit corrections separately from historical M7 evidence."""

import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESTINATION = ROOT / "evals/evidence/m7-completion-followup"


def main() -> None:
    if DESTINATION.exists():
        raise SystemExit("Never replace frozen completion evidence")
    DESTINATION.mkdir(parents=True)
    for name in (
        "checks.json",
        "python.xml",
        "m7-completion-followup-check.log",
        "m7-completion-followup-install.json",
        "m7-completion-followup-install-step1.log",
        "m7-completion-followup-install-step2.log",
        "m7-completion-followup-install-step3.log",
        "m7-completion-followup-install-step4.log",
        "m7-completion-followup-install-step5.log",
        "m7-feedback-followup-tests.log",
        "m7-feedback-followup-browser.log",
        "m7-learning-flow.webm",
        "m7-learning-flow.json",
        "m7-learning-flow-recording.log",
        "m7-completion-demo-viewer.html",
        "m2-knowledge.png",
    ):
        shutil.copyfile(ROOT / ".data/qa" / name, DESTINATION / name)
    shutil.copytree(ROOT / ".data/qa/m7-completion-demos", DESTINATION / "recordings")
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
        *ROOT.glob("docs/**/*.md"),
    ]
    paths += [
        ROOT / name
        for name in (
            "README.md",
            "PLAN.md",
            "PRODUCT.md",
            "DESIGN.md",
            "pyproject.toml",
            "uv.lock",
            ".python-version",
            ".gitignore",
            ".env.example",
            ".env.app.example",
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
        json.dumps(
            {
                "version": "m7-completion-followup.v1",
                "base_evidence": "m7-delivery",
                "new_model_calls": 0,
                "independent_human_review": None,
                "files": files,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    print(json.dumps({"files": len(files), "new_model_calls": 0}))


if __name__ == "__main__":
    main()
