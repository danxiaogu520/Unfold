"""Verify frozen M3 results without creating tasks or performing network calls."""

import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path

from unfold.api_contract import RuntimeRunSnapshot

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / "evals/evidence/m3-reliability"


def main() -> None:
    manifest = json.loads((DIRECTORY / "manifest.json").read_text())
    for name, digest in manifest["files"].items():
        path = (DIRECTORY / name).resolve()
        assert path.is_relative_to(DIRECTORY) and path.is_file()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest, name
    for checkpoint in ("claimed", "response_saved", "before_commit", "committed"):
        result = json.loads((DIRECTORY / f"m3-process-{checkpoint}.json").read_text())
        assert result["child_killed_with_sigkill"] and result["assertions_passed"]
        snapshot = RuntimeRunSnapshot.model_validate(result["trace"]["snapshot"])
        assert snapshot.status == "succeeded" and result["saved_intakes"] == 1
        assert snapshot.attempt == (1 if checkpoint == "committed" else 2)
        if checkpoint in ("response_saved", "before_commit"):
            assert result["trace"]["calls"][-1]["provider"] == "stored_response_replay"
    load = json.loads((DIRECTORY / "m3-load.json").read_text())
    assert load["real_model_calls"] == 0 and load["assertions_passed"]
    assert load["accepted"] == load["capacity_rejections"] == 32
    assert load["final_statuses"] == {"cancelled": 8, "succeeded": 24}
    assert load["generations"] == 24 and load["peak_fake_generations"] <= 2
    race = json.loads((DIRECTORY / "m3-cancel-race.json").read_text())
    assert race["iterations"] == sum(race["outcomes"].values()) == 20
    assert race["one_terminal_state_per_run"]
    checks = json.loads((DIRECTORY / "checks.json").read_text())
    assert all(item["exit_code"] == 0 for item in checks["checks"])
    suites = ET.parse(DIRECTORY / "python.xml").getroot().findall("testsuite")
    assert sum(int(s.attrib["tests"]) for s in suites) == 155
    assert all(
        int(s.attrib[attribute]) == 0
        for s in suites
        for attribute in ("failures", "errors", "skipped")
    )
    print(
        json.dumps(
            {
                "files": len(manifest["files"]),
                "python_tests": 155,
                "process_death_windows": 4,
                "cancel_races": 20,
                "real_model_calls": 0,
            }
        )
    )


if __name__ == "__main__":
    main()
