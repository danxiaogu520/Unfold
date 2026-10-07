"""Freeze real failures, successful learning and explicitly marked retained replay."""

import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESTINATION = ROOT / "evals/evidence/m2-learning"
BATCHES = (
    "m2-persistent-v1",
    "m2-persistent-v2",
    "m2-review-v1",
    "m2-review-v2",
    "m2-purpose-replay-v1",
)


def main() -> None:
    if DESTINATION.exists():
        raise SystemExit("Use a new evidence version instead of overwriting history")
    DESTINATION.mkdir(parents=True)
    reports = []
    for name in BATCHES:
        source = ROOT / "evals/runs" / name
        if not (source / "report.json").is_file():
            raise ValueError("missing experiment: " + name)
        shutil.copytree(source, DESTINATION / name)
        reports.append({"batch": name, **json.loads((source / "report.json").read_text())})
    summary = {
        "version": "m2-evidence.v1",
        "batches": [row["batch"] for row in reports],
        "real_calls": sum(row.get("real_calls", 0) for row in reports),
        "replay_calls": 1,
        "replay_is_new_inference": False,
        "conservative_cost_cny": "0.32463000",
        "unknown_usage_calls": 0,
        "accepted_learning_batch": "m2-persistent-v2",
        "known_content_issue_correction": "m2-review-v2",
        "purpose_change_preview": "m2-purpose-replay-v1",
        "human_review": None,
        "fact_verification": "targeted agent RFC checks only",
    }
    (DESTINATION / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    )
    for name in ("checks.json", "python.xml", "m2-knowledge.png"):
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
        ROOT / "web/index.html",
        *ROOT.glob("contracts/schemas/*.json"),
        *ROOT.glob(".github/workflows/*.yml"),
        *ROOT.glob("evals/prompts/*.json"),
        *ROOT.glob("evals/contracts/*.json"),
        *ROOT.glob("evals/datasets/*.json"),
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
            "contracts/toolchain.v1.json",
        )
    ]
    for source in sorted(set(paths)):
        target = DESTINATION / "source-snapshot" / source.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    files = {
        str(path.relative_to(DESTINATION)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(DESTINATION.rglob("*"))
        if path.is_file()
    }
    (DESTINATION / "manifest.json").write_text(
        json.dumps({"version": "m2-manifest.v1", "files": files}, ensure_ascii=False, indent=2)
        + "\n"
    )
    print(json.dumps({"files": len(files), "real_calls": summary["real_calls"], "replay_calls": 1}))


if __name__ == "__main__":
    main()
