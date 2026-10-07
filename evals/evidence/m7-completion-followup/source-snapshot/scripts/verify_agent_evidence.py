"""Offline checks of actual Agent decision and restoration evidence; no side effects."""

import hashlib
import json
import xml.etree.ElementTree as ET
from decimal import Decimal
from pathlib import Path

from unfold.app.resource_agent import AgentDecision

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / "evals/evidence/m5-agent"


def main() -> None:
    manifest = json.loads((DIRECTORY / "manifest.json").read_text())
    for name, expected in manifest["files"].items():
        path = (DIRECTORY / name).resolve()
        assert path.is_relative_to(DIRECTORY) and path.is_file()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected, name
    calls, cost = 0, Decimal(0)
    for batch in ("m5-public-comparison-v1", "m5-public-comparison-v2"):
        report = json.loads((DIRECTORY / batch / "report.json").read_text())
        assert report["data_classification"] == "public_synthetic_evaluation.v1"
        assert not report["reads_existing_learning_spaces"] and len(report["trials"]) == 9
        for path in (DIRECTORY / batch).glob("run_*.json"):
            trace = json.loads(path.read_text())
            for row in trace["agent_steps"]:
                AgentDecision.model_validate(row["decision"])
            for call in trace["calls"]:
                assert call["provider"] == "deepseek"
                calls += 1
                assert call["input_tokens"] is not None and call["output_tokens"] is not None
                cost += Decimal(call["accounted_cost"])
        for mode in ("baseline_single", "baseline_fixed", "agent"):
            trials = [row for row in report["trials"] if row["mode"] == mode]
            assert sum(row["acceptable_capability_exit"] for row in trials) == 1
            required_success = 1 if batch.endswith("v2") and mode == "baseline_single" else 2
            assert sum(row["technical_success"] for row in trials) == required_success
    for batch in ("m5-public-recovery-v1", "m5-public-recovery-v2"):
        report = json.loads((DIRECTORY / batch / "report.json").read_text())
        assert report["child_sigkill"] and report["restoration_integrity_passed"]
        assert report["final_status"] == ("failed" if batch.endswith("v1") else "succeeded")
        before = json.loads((DIRECTORY / batch / "before-recovery.json").read_text())
        after = json.loads((DIRECTORY / batch / "after-recovery.json").read_text())
        completed = report["completed_steps_not_replayed"]
        for number in completed:
            assert next(row for row in before["agent_steps"] if row["number"] == number) == next(
                row for row in after["agent_steps"] if row["number"] == number
            )
        assert all(row["attempt"] == 1 for row in after["tools"] if row["step"] in completed)
        for call in after["calls"]:
            calls += 1
            assert call["input_tokens"] is not None and call["output_tokens"] is not None
            cost += Decimal(call["accounted_cost"])
    assert calls == 42 and cost == Decimal("0.41247200")
    suites = ET.parse(DIRECTORY / "python.xml").getroot().findall("testsuite")
    assert sum(int(s.attrib["tests"]) for s in suites) == 197
    assert all(int(s.attrib[k]) == 0 for s in suites for k in ("failures", "errors", "skipped"))
    isolation = json.loads((DIRECTORY / "m5-isolation.json").read_text())
    assert (
        isolation["metadata"]["browser_sandbox"]
        and not isolation["metadata"]["model_key_in_driver"]
    )
    assert (
        isolation["probe"]["network"] == "blocked"
        and isolation["probe"]["storage"] == "unavailable"
    )
    print(
        json.dumps(
            {
                "files": len(manifest["files"]),
                "real_calls": calls,
                "cost_cny": str(cost),
                "real_recovery_trials": 2,
                "python_tests": 197,
            }
        )
    )


if __name__ == "__main__":
    main()
