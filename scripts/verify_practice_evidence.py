"""Validate M4 byte provenance, source results and explicit no-inference corrections."""

import hashlib
import json
import xml.etree.ElementTree as ET
from decimal import Decimal
from pathlib import Path

from PIL import Image

from unfold.api_contract import LearningRunSnapshot
from unfold.contracts import validate_pair

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / "evals/evidence/m4-practice"


def main() -> None:
    manifest = json.loads((DIRECTORY / "manifest.json").read_text())
    for name, expected in manifest["files"].items():
        path = (DIRECTORY / name).resolve()
        assert path.is_relative_to(DIRECTORY) and path.is_file()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected, name
    calls, cost, unknown = 0, Decimal(0), 0
    for path in (DIRECTORY / "m4-live-v1").glob("run_*.json"):
        trace = json.loads(path.read_text())
        snapshot = LearningRunSnapshot.model_validate(trace["snapshot"])
        assert snapshot.status == "succeeded"
        validate_pair(snapshot.operation, trace["input"], trace["proposal"])
        for call in trace["calls"]:
            assert call["provider"] == "deepseek"
            calls += 1
            unknown += call["input_tokens"] is None or call["output_tokens"] is None
            cost += Decimal(
                call["accounted_cost"]
                if call["accounted_cost"] is not None
                else call["reserved_cost"]
            )
    assert calls == 4 and cost == Decimal("0.07373800") and unknown == 0
    correction = json.loads((DIRECTORY / "m4-reference-correction-v1/report.json").read_text())
    assert correction["new_live_model_calls"] == correction["new_image_downloads"] == 0
    for path in (DIRECTORY / "m4-reference-correction-v1").glob("run_*.json"):
        trace = json.loads(path.read_text())
        validate_pair(trace["snapshot"]["operation"], trace["input"], trace["proposal"])
        assert all(call["provider"] == "agent_reference_correction" for call in trace["calls"])
    final = correction["final_node"]
    assert final["contents"][0]["review_status"] == "unverified"
    assert final["contents"][1]["review_status"] == "reported"
    assert final["contents"][0]["resources"][0]["status"] == "ready"
    assert any(q["status"] == "disabled" for q in final["exercise_sets"][1]["questions"])
    for checkpoint in ("file_saved", "candidate_saved", "before_commit", "committed"):
        result = json.loads((DIRECTORY / f"m4-process-{checkpoint}.json").read_text())
        assert result["sigkill"] and result["assertions_passed"]
        assert result["resource"]["status"] == "ready" and len(result["resource"]["versions"]) == 1
    suites = ET.parse(DIRECTORY / "python.xml").getroot().findall("testsuite")
    assert sum(int(s.attrib["tests"]) for s in suites) == 180
    assert all(int(s.attrib[k]) == 0 for s in suites for k in ("failures", "errors", "skipped"))
    with Image.open(DIRECTORY / "m4-diagram.png") as image:
        pixels = image.convert("RGB").get_flattened_data()
        assert sum(min(pixel) < 180 for pixel in pixels) > 500
    audit = json.loads((DIRECTORY / "m4-npm-audit.json").read_text())
    assert audit["metadata"]["vulnerabilities"]["total"] == 0
    print(
        json.dumps(
            {
                "files": len(manifest["files"]),
                "python_tests": 180,
                "real_calls": calls,
                "cost_cny": str(cost),
                "local_corrections": 2,
            }
        )
    )


if __name__ == "__main__":
    main()
