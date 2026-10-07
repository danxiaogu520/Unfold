"""Validate frozen M2 bytes and structural results without paying or replaying effects."""

import hashlib
import json
from decimal import Decimal
from pathlib import Path

from unfold.api_contract import RunSnapshot, RuntimeRunSnapshot
from unfold.contracts import validate_pair

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / "evals/evidence/m2-learning"


def main() -> None:
    manifest = json.loads((DIRECTORY / "manifest.json").read_text())
    for name, expected in manifest["files"].items():
        path = (DIRECTORY / name).resolve()
        assert path.is_relative_to(DIRECTORY) and path.is_file()
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError("M2 evidence digest mismatch: " + name)
    summary = json.loads((DIRECTORY / "summary.json").read_text())
    real_calls, cost, unknown = 0, Decimal(0), 0
    for name in summary["batches"]:
        batch = DIRECTORY / name
        report = json.loads((batch / "report.json").read_text())
        for source, digest in report.get("source_sha256", {}).items():
            assert (
                hashlib.sha256((batch / "source-snapshot" / source).read_bytes()).hexdigest()
                == digest
            )
        for path in batch.glob("run_*.json"):
            trace = json.loads(path.read_text())
            snapshot = trace["snapshot"]
            cls = RuntimeRunSnapshot if snapshot["schema_version"] == "run.v2" else RunSnapshot
            cls.model_validate(snapshot)
            if snapshot["status"] == "succeeded":
                validate_pair(snapshot["operation"], trace["input"], trace["proposal"])
            for call in trace["calls"]:
                assert call["provider"] == "deepseek"
                real_calls += 1
                unknown += call["input_tokens"] is None or call["output_tokens"] is None
                cost += Decimal(
                    call["accounted_cost"]
                    if call["accounted_cost"] is not None
                    else call["reserved_cost"]
                )
        # Newer application checks can reject historical content or accept a path
        # title previously over-restricted. Original verdicts remain unchanged.
    accepted = json.loads(
        (DIRECTORY / summary["accepted_learning_batch"] / "report.json").read_text()
    )
    assert len(accepted["cases"]) == 2 and all(case["success"] for case in accepted["cases"])
    assert real_calls == summary["real_calls"] == 23
    assert cost == Decimal(summary["conservative_cost_cny"]) and unknown == 0
    replay = json.loads((DIRECTORY / summary["purpose_change_preview"] / "report.json").read_text())
    assert replay["new_live_model_calls"] == 0 and replay["current_structure_preserved"]
    trace = json.loads((DIRECTORY / summary["purpose_change_preview"] / "replay.json").read_text())
    assert trace["snapshot"]["result_kind"] == "structure_preview"
    assert all(call["provider"] == "recorded_replay" for call in trace["calls"])
    print(
        json.dumps(
            {
                "files": len(manifest["files"]),
                "real_calls": real_calls,
                "recorded_replay": 1,
                "conservative_cost_cny": str(cost),
            }
        )
    )


if __name__ == "__main__":
    main()
