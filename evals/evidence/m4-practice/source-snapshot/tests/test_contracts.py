from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from unfold.api_contract import CreateRun, RunEvent, RunSnapshot, public_exercise
from unfold.contracts import Exercise, ExpansionInput, grade, validate_pair
from unfold.materials import check_materials, exported_schemas, split_audit, verify_evidence

ROOT = Path(__file__).resolve().parents[1]
SUCCESS = json.loads((ROOT / "evals/contracts/success.v1.json").read_text())
FAILURE = json.loads((ROOT / "evals/contracts/failure.v1.json").read_text())
BY_OPERATION = {row["operation"]: row for row in SUCCESS}


@pytest.mark.parametrize("sample", SUCCESS, ids=[row["operation"] for row in SUCCESS])
def test_success_contract_pairs(sample: dict[str, Any]) -> None:
    validate_pair(sample["operation"], sample["input"], sample["output"])


@pytest.mark.parametrize("sample", FAILURE, ids=[row["failure"] for row in FAILURE])
def test_invalid_candidates_never_become_success(sample: dict[str, Any]) -> None:
    with pytest.raises((ValueError, ValidationError)):
        validate_pair(sample["operation"], sample["input"], sample["output"])


@pytest.mark.parametrize("name,schema", exported_schemas().items())
def test_generated_schema_is_valid(name: str, schema: dict[str, Any]) -> None:
    if name != "openapi.v1.json":
        Draft202012Validator.check_schema(schema)
    else:
        components = schema["components"]["schemas"]
        for definition in components.values():
            Draft202012Validator.check_schema(definition)


def test_all_materials_and_generated_files_match() -> None:
    assert check_materials(ROOT)["near_duplicate_warnings"] == []


def test_preserved_real_evidence_digests_snapshots_and_verdicts() -> None:
    result = verify_evidence(ROOT)
    assert result["recorded_calls"] == 115
    assert result["valid_normal_proposals"] == 98


def test_stale_and_leaf_decisions_cannot_bypass_expansion_via_regeneration() -> None:
    request = copy.deepcopy(BY_OPERATION["expansion"]["input"])
    request["operation"] = "regenerate_expansion"
    request["target"]["decision"]["state"] = "leaf"
    request["path"] = [request["target"]]
    with pytest.raises(ValidationError, match="forbidden on leaf"):
        ExpansionInput.model_validate(request)
    request["target"]["decision"]["state"] = "expandable"
    request["profile"]["revision"] = 2
    with pytest.raises(ValidationError, match="stale decision"):
        ExpansionInput.model_validate(request)


def test_reassessment_preserves_existing_subtree_for_review() -> None:
    sample = copy.deepcopy(BY_OPERATION["reassessment"])
    child = copy.deepcopy(sample["input"]["target"])
    child["key"] = "existing-child"
    sample["input"]["existing_children"] = [child]
    with pytest.raises(ValueError, match="review note"):
        validate_pair("reassessment", sample["input"], sample["output"])
    sample["output"]["restructuring_note"] = "保留旧子树并提出预览建议，正式结构不在此操作修改。"
    validate_pair("reassessment", sample["input"], sample["output"])


def test_relation_cycles_allowed_except_strict_prerequisites() -> None:
    sample = copy.deepcopy(BY_OPERATION["outline"])
    sample["output"]["relations"] = [
        {"source_key": a, "target_key": b, "kind": "related_to", "explanation": "相互关联"}
        for a, b in [("equal", "less"), ("less", "equal")]
    ]
    validate_pair("outline", sample["input"], sample["output"])


def test_grading_uses_stable_ids_and_exact_sets() -> None:
    raw = copy.deepcopy(BY_OPERATION["exercises"]["output"]["exercises"][0])
    exercise = Exercise.model_validate(raw)
    assert grade(exercise, ["found"])
    raw["options"].reverse()
    assert grade(Exercise.model_validate(raw), ["found"])
    raw["mode"] = "multiple"
    raw["correct_keys"] = ["found", "left"]
    multiple = Exercise.model_validate(raw)
    assert grade(multiple, ["left", "found"])
    assert not grade(multiple, ["found"])
    with pytest.raises(ValueError):
        grade(multiple, ["found", "found"])
    with pytest.raises(ValueError):
        grade(multiple, ["invented"])


def test_public_exercise_does_not_expose_answers_before_submission() -> None:
    exercise = Exercise.model_validate(BY_OPERATION["exercises"]["output"]["exercises"][0])
    public = public_exercise(exercise, "exercise-revision-1").model_dump()
    assert "correct_keys" not in public and "explanation" not in public
    assert public["exercise_revision_ref"] == "exercise-revision-1"


def test_family_and_near_duplicate_leakage_detection() -> None:
    rows = [
        {"id": "a", "family": "x", "split": "dev", "task": "某个固定输入"},
        {"id": "b", "family": "x", "split": "holdout", "task": "其他输入"},
    ]
    with pytest.raises(ValueError, match="family leaked"):
        split_audit(rows, "task")
    rows[1]["family"] = "y"
    rows[1]["task"] = rows[0]["task"]
    with pytest.raises(ValueError, match="identical"):
        split_audit(rows, "task")
    rows[1]["task"] += "略作改写"
    assert split_audit(rows, "task")


def test_api_envelope_versions_cannot_disagree_with_input() -> None:
    raw = {
        "schema_version": "api.v1",
        "space_id": "space-1",
        "operation": "expansion",
        "profile_revision": 1,
        "base_revision": 1,
        "input": BY_OPERATION["expansion"]["input"],
    }
    CreateRun.model_validate(raw)
    raw["profile_revision"] = 2
    with pytest.raises(ValidationError, match="profile revision"):
        CreateRun.model_validate(raw)


def test_run_terminal_state_and_event_identity() -> None:
    raw = {
        "schema_version": "run.v1",
        "run_id": "run-1",
        "operation": "outline",
        "status": "failed",
        "attempt": 1,
        "last_event_seq": 1,
        "result_ref": None,
        "error": None,
    }
    with pytest.raises(ValidationError, match="require an error"):
        RunSnapshot.model_validate(raw)
    raw["status"] = "succeeded"
    with pytest.raises(ValidationError, match="committed result"):
        RunSnapshot.model_validate(raw)
    raw["result_ref"] = "content-1"
    RunSnapshot.model_validate(raw)
    with pytest.raises(ValidationError, match="match its run"):
        RunEvent.model_validate(
            {
                "schema_version": "event.v1",
                "run_id": "other",
                "sequence": 1,
                "kind": "completed",
                "snapshot": raw,
            }
        )
