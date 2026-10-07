"""Verify immutable M1 evidence, without executing archived source or live models."""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / "evals/evidence/m1-foundation"


def main() -> None:
    manifest = json.loads((DIRECTORY / "manifest.json").read_text())
    for name, digest in manifest["files"].items():
        path = (DIRECTORY / name).resolve()
        if not path.is_relative_to(DIRECTORY) or not path.is_file():
            raise ValueError("invalid evidence file")
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError("evidence digest mismatch: " + name)
    for name, status, count in (("success", "succeeded", 1), ("failure", "failed", 0)):
        evidence = json.loads((DIRECTORY / (name + ".json")).read_text())
        assert evidence["evidence_kind"] == "deterministic_fake_real_postgres_http"
        assert evidence["final"]["status"] == status
        assert len(evidence["official_intakes"]) == count
        assert [e["sequence"] for e in evidence["events"]] == [1, 2, 3]
        assert all(call["provider"] == "deterministic_fake" for call in evidence["trace"]["calls"])
    assert manifest["real_model_calls"] == 0
    print(json.dumps({"files": len(manifest["files"]), "runs": 2, "real_model_calls": 0}))


if __name__ == "__main__":
    main()
