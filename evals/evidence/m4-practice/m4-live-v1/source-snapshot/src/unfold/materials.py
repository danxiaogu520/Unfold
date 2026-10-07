"""Export canonical schemas and check fixed examples/split leakage offline."""

from __future__ import annotations

import hashlib
import json
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from unfold.api_contract import API_MODELS, openapi_document
from unfold.contracts import (
    INPUTS,
    OUTPUTS,
    GuidedExerciseInput,
    ProfileHistoryInput,
    validate_pair,
)
from unfold.evaluation import fingerprint, read_json, write_json
from unfold.provider import provider_schema


def exported_schemas() -> dict[str, Any]:
    result = {}
    for direction, registry in [("input", INPUTS), ("output", OUTPUTS), ("api", API_MODELS)]:
        for name, cls in registry.items():
            result[f"{name}.{direction}.schema.json"] = {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                **cls.model_json_schema(),
            }
    for name in OUTPUTS:
        result[f"{name}.provider.schema.json"] = provider_schema(name)
    result["profile-history.input.schema.json"] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        **ProfileHistoryInput.model_json_schema(),
    }
    result["exercises-guided.input.schema.json"] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        **GuidedExerciseInput.model_json_schema(),
    }
    result["openapi.v1.json"] = openapi_document()
    return result


def export(root: Path, *, check: bool = False) -> int:
    count = 0
    for name, schema in exported_schemas().items():
        path = root / "contracts/schemas" / name
        if check:
            if not path.exists() or read_json(path) != schema:
                raise ValueError(f"outdated generated schema: {name}")
        else:
            write_json(path, schema)
        count += 1
    return count


def split_audit(rows: list[dict[str, Any]], text_key: str) -> list[str]:
    ids = [row["id"] for row in rows]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate scenario IDs")
    groups: dict[str, str] = {}
    for row in rows:
        if row["split"] not in ("dev", "holdout"):
            raise ValueError("invalid dataset split")
        if groups.setdefault(row["family"], row["split"]) != row["split"]:
            raise ValueError("task family leaked across splits")
    warnings = []
    for left in rows:
        if left["split"] != "dev":
            continue
        for right in rows:
            if right["split"] != "holdout":
                continue
            a = re.sub(r"\s+", "", left[text_key])
            b = re.sub(r"\s+", "", right[text_key])
            score = SequenceMatcher(None, a, b, autojunk=False).ratio()
            if a == b:
                raise ValueError("identical input leaked across splits")
            if score >= 0.65:
                warnings.append(f"near input {left['id']} / {right['id']}: {score:.3f}")
    return warnings


def check_materials(root: Path) -> dict[str, Any]:
    success = read_json(root / "evals/contracts/success.v1.json")
    failure = read_json(root / "evals/contracts/failure.v1.json")
    for sample in success:
        validate_pair(sample["operation"], sample["input"], sample["output"])
    for sample in failure:
        try:
            validate_pair(sample["operation"], sample["input"], sample["output"])
        except (ValidationError, ValueError):
            continue
        raise ValueError("failure example accepted: " + sample["failure"])
    learning = read_json(root / "evals/datasets/learning.v1.json")["scenarios"]
    resources = read_json(root / "evals/datasets/resources.v1.json")["tasks"]
    warnings = split_audit(learning, "user_brief") + split_audit(resources, "task")
    return {
        "success_examples": len(success),
        "rejected_failure_examples": len(failure),
        "learning_scenarios": len(learning),
        "resource_tasks": len(resources),
        "learning_dev": sum(row["split"] == "dev" for row in learning),
        "learning_holdout": sum(row["split"] == "holdout" for row in learning),
        "resource_dev": sum(row["split"] == "dev" for row in resources),
        "resource_holdout": sum(row["split"] == "holdout" for row in resources),
        "near_duplicate_warnings": warnings,
        "schemas": export(root, check=True),
    }


def verify_evidence(root: Path) -> dict[str, int]:
    directory = root / "evals/evidence"
    index = read_json(directory / "sha256.json")
    for name, expected in index.items():
        if hashlib.sha256((directory / name).read_bytes()).hexdigest() != expected:
            raise ValueError("historical evidence digest mismatch: " + name)
    calls = valid = 0
    for batch in read_json(directory / "m0-summary.json")["batches"]:
        run = directory / batch["batch"]
        manifest = read_json(run / "manifest.json")
        if fingerprint(run / "source-snapshot") != manifest["source_sha256"]:
            raise ValueError("experiment source snapshot mismatch")
        for line in (run / "calls.jsonl").read_text().splitlines():
            row = json.loads(line)
            calls += 1
            if row.get("intentional_probe"):
                continue
            accepted = False
            try:
                if row["result"]["error"] is None:
                    validate_pair(
                        row["operation"],
                        row["request"],
                        json.loads(row["result"]["output_text"] or ""),
                    )
                    accepted = True
            except (ValueError, ValidationError):
                pass
            if accepted != row["domain_valid"]:
                raise ValueError("replayed verdict changed: " + str(row["call"]))
            valid += accepted
    return {"evidence_files": len(index), "recorded_calls": calls, "valid_normal_proposals": valid}
