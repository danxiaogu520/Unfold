"""Read-only hashes, installation, restore, recordings and final checks."""

import hashlib
import json
import xml.etree.ElementTree as ET
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / "evals/evidence/m7-delivery"


def main() -> None:
    manifest = json.loads((DIRECTORY / "manifest.json").read_text())
    for name, expected in manifest["files"].items():
        path = (DIRECTORY / name).resolve()
        assert path.is_relative_to(DIRECTORY) and path.is_file()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected, name
    suites = ET.parse(DIRECTORY / "python.xml").getroot().findall("testsuite")
    tests = sum(int(s.attrib["tests"]) for s in suites)
    assert tests == 212
    assert all(int(s.attrib[k]) == 0 for s in suites for k in ("failures", "errors", "skipped"))
    checks = json.loads((DIRECTORY / "checks.json").read_text())
    assert len(checks["checks"]) == 14 and all(row["exit_code"] == 0 for row in checks["checks"])
    install = json.loads((DIRECTORY / "m7-clean-install-v3.json").read_text())
    assert not install["model_credentials_copied"] and not install["node_modules_copied"]
    assert not install["virtual_environment_copied"] and install["real_model_calls"] == 0
    assert all(step["exit_code"] == 0 for step in install["checks"])
    assert install["smoke"]["automatic_outline"] and install["smoke"]["answer_grading"]
    failed = json.loads((DIRECTORY / "m7-clean-install.json").read_text())
    assert failed["checks"][0]["exit_code"] != 0
    restored = json.loads((DIRECTORY / "m7-restore-final-verification.json").read_text())
    assert restored["all_public_rows_equal"] and len(restored["tables"]) == 27
    assert restored["formal_files_verified"] == 14 and restored["restored_http_read"]
    assert restored["new_model_calls"] == 0 and restored["ledger_accounted_cny"] == "1.98536200"
    entry = DIRECTORY / "public-domain-entry"
    real = json.loads((entry / "report.json").read_text())
    assert real["technical_success"] and real["all_answers_unknown"]
    assert not real["reads_existing_learning_spaces"] and real["real_calls"] == 4
    assert real["primary_purpose"]["source"] == "unknown"
    calls, cost = 0, Decimal(0)
    for stage in real["stages"]:
        trace = json.loads((entry / (stage["run_id"] + ".json")).read_text())
        assert trace["snapshot"]["status"] == "succeeded"
        for call in trace["calls"]:
            assert call["provider"] == "deepseek" and call["prompt_version"] == "app-prompt.v4"
            calls += 1
            cost += Decimal(call["accounted_cost"])
    assert calls == 4 and cost == Decimal("0.03999200")
    recording = json.loads((DIRECTORY / "recordings/recording.json").read_text())
    assert len(recording["records"]) == 3
    assert (
        hashlib.sha256((DIRECTORY / "m7-demo-viewer.html").read_bytes()).hexdigest()
        == recording["viewer_sha256"]
    )
    for row in recording["records"]:
        assert row["execution"] == "recorded_evidence_replay" and row["new_model_calls"] == 0
        assert (DIRECTORY / "recordings" / row["video"]).stat().st_size == row["bytes"]
    load = json.loads((DIRECTORY / "m3-load.json").read_text())
    assert load["accepted"] == load["capacity_rejections"] == 32
    assert load["real_model_calls"] == 0 and load["assertions_passed"]
    assert load["peak_fake_generations"] == 2
    print(
        json.dumps(
            {
                "files": len(manifest["files"]),
                "python_tests": tests,
                "browser_tests": 10,
                "recordings": 3,
                "new_real_model_calls": 4,
                "independent_human_review": None,
            }
        )
    )


if __name__ == "__main__":
    main()
