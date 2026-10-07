"""Verify the audit, actual learning video and explicitly separate Agent recovery."""

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / "evals/evidence/m7-completion-followup"


def main() -> None:
    manifest = json.loads((DIRECTORY / "manifest.json").read_text())
    assert manifest["new_model_calls"] == 0 and manifest["independent_human_review"] is None
    for name, expected in manifest["files"].items():
        path = (DIRECTORY / name).resolve()
        assert path.is_relative_to(DIRECTORY) and path.is_file()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected, name
    suites = ET.parse(DIRECTORY / "python.xml").getroot().findall("testsuite")
    assert sum(int(s.attrib["tests"]) for s in suites) == 213
    assert all(int(s.attrib[k]) == 0 for s in suites for k in ("errors", "failures", "skipped"))
    checks = json.loads((DIRECTORY / "checks.json").read_text())
    assert len(checks["checks"]) == 15 and all(s["exit_code"] == 0 for s in checks["checks"])
    install = json.loads((DIRECTORY / "m7-completion-followup-install.json").read_text())
    assert all(c["exit_code"] == 0 for c in install["checks"])
    assert not install["model_credentials_copied"] and install["smoke"]["automatic_outline"]
    learning = json.loads((DIRECTORY / "m7-learning-flow.json").read_text())
    video = DIRECTORY / "m7-learning-flow.webm"
    assert learning["execution"] == "actual_http_ui_fake_model"
    assert video.stat().st_size == learning["bytes"]
    assert hashlib.sha256(video.read_bytes()).hexdigest() == learning["sha256"]
    recording = json.loads((DIRECTORY / "recordings/recording.json").read_text())
    viewer = (DIRECTORY / "m7-completion-demo-viewer.html").read_bytes()
    assert hashlib.sha256(viewer).hexdigest() == recording["viewer_sha256"]
    assert next(r["steps"] for r in recording["records"] if r["name"] == "agent") == 7
    assert b"m5-public-recovery-v2" in viewer
    for row in recording["records"]:
        assert row["new_model_calls"] == 0
        assert (DIRECTORY / "recordings" / row["video"]).stat().st_size == row["bytes"]
    snapshot = DIRECTORY / "source-snapshot"
    expected = set(re.findall(r"\| (M[0-7]-\d{2}) \|", (snapshot / "PLAN.md").read_text()))
    actual = set(
        re.findall(
            r"\| (M[0-7]-\d{2}) \|",
            (snapshot / "docs/reports/m7-completion-audit-2026-10-07.md").read_text(),
        )
    )
    assert expected == actual and len(expected) == 50
    print(
        json.dumps(
            {
                "files": len(manifest["files"]),
                "numbered_tasks": 50,
                "python_tests": 213,
                "browser_tests": 10,
                "actual_learning_recordings": 1,
                "historical_replay_recordings": 3,
                "new_model_calls": 0,
                "independent_human_review": None,
            }
        )
    )


if __name__ == "__main__":
    main()
