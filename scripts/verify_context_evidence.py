"""Offline verification of frozen context selection and provider evidence."""

import hashlib
import json
import xml.etree.ElementTree as ET
from decimal import Decimal
from pathlib import Path

from summarize_context import metrics

from unfold.app.context import basis, extract, select_context
from unfold.contracts import validate_pair
from unfold.provider import GenerationError

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / "evals/evidence/m6-context"


def main() -> None:
    manifest = json.loads((DIRECTORY / "manifest.json").read_text())
    actual = {
        str(p.relative_to(DIRECTORY))
        for p in DIRECTORY.rglob("*")
        if p.is_file() and p.name != "manifest.json"
    }
    assert actual == set(manifest["files"])
    for name, expected in manifest["files"].items():
        path = (DIRECTORY / name).resolve()
        assert path.is_relative_to(DIRECTORY) and path.is_file()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected, name
    batch = DIRECTORY / "heldout-v1"
    report = json.loads((batch / "report.json").read_text())
    assert metrics(report) == json.loads((batch / "derived-metrics.json").read_text())
    assert len(report["trials"]) == 72
    assert report["candidate_only"] and report["formal_results_published"] == 0
    assert not report["reads_existing_learning_spaces"]
    assert report["replications_per_cell"] == 1
    assert report["quality_protocol"]["fact_accuracy"] is None
    calls, cost, overflow = 0, Decimal(0), 0
    for row in report["trials"]:
        frozen = json.loads(
            (batch / (row["snapshot"].replace(":", "-") + ".snapshot.json")).read_text()
        )
        request, data = frozen["request"], frozen["snapshot"]
        assert frozen["case"]["split"] == "holdout"
        assert data["data_classification"] == "public_synthetic_evaluation.v1"
        versions = {p["key"]: p["content_revision"] for p in request["path"]}
        summaries = {p["id"]: extract(p, basis(p, 1, 1, versions)) for p in data["pages"]}
        try:
            selected = select_context(
                request,
                frozen["base"],
                data["pages"],
                data["relations"],
                data["profile_history"],
                row["strategy"],
                summaries=summaries,
            )
        except GenerationError as exc:
            assert row["status"] == "not_started" and row["error"]["code"] == exc.code
            assert row["real_calls"] == 0
            overflow += 1
            continue
        assert selected.log == row["selection"]
        assert not selected.log["silent_truncation"]
        assert selected.log["input_bytes"] <= 24000
        trace = json.loads((batch / (row["run_id"] + ".json")).read_text())
        assert trace["dependencies"]["selected_context"] == selected.material
        if row["technical_success"]:
            candidate = json.loads((batch / (row["run_id"] + ".candidate.json")).read_text())
            validate_pair("explanation", request, candidate)
            assert trace["snapshot"]["status"] == "cancelled"
            assert trace["snapshot"]["result_ref"] is None
        for call in trace["calls"]:
            assert call["provider"] == "deepseek"
            assert call["input_tokens"] is not None and call["output_tokens"] is not None
            payload = json.loads(call["request"]["input"])
            assert payload["input"] == request and payload["context"] == selected.material
            calls += 1
            cost += Decimal(call["accounted_cost"])
    assert calls == report["real_calls"] and cost == Decimal(report["conservative_cost_cny"])
    suites = ET.parse(DIRECTORY / "python.xml").getroot().findall("testsuite")
    assert all(int(s.attrib[k]) == 0 for s in suites for k in ("failures", "errors", "skipped"))
    print(
        json.dumps(
            {
                "files": len(manifest["files"]),
                "real_calls": calls,
                "cost_cny": str(cost),
                "overflow_before_inference": overflow,
                "python_tests": sum(int(s.attrib["tests"]) for s in suites),
            }
        )
    )


if __name__ == "__main__":
    main()
