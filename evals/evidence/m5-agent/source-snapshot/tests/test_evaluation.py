from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from test_provider import ROOT, SAMPLES

from unfold.evaluation import Experiment
from unfold.provider import ProviderResult, ResponsesProvider, Settings


def test_exploration_backtracks_after_a_shallow_leaf(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixtures = {row["operation"]: row for row in SAMPLES}
    outline = copy.deepcopy(fixtures["outline"]["output"])
    a, b = outline["children"]
    a["key"], b["key"] = "a", "b"
    a["decision"]["state"] = b["decision"]["state"] = "expandable"

    def generate(
        self: ResponsesProvider, body: dict, timeout: float | None = None
    ) -> ProviderResult:
        request = json.loads(body["input"])
        operation = request["operation"]
        if operation == "questionnaire":
            output = {
                "schema_version": "questionnaire.v1",
                "questions": [],
                "rationale": "足够开始",
            }
        elif operation == "outline":
            output = outline
        elif operation == "expansion":
            target = request["input"]["target"]["key"]
            children = []
            if target in ("b", "c"):
                child = copy.deepcopy(a)
                child["key"] = "c" if target == "b" else "d"
                child["decision"]["state"] = "expandable" if target == "b" else "leaf"
                children = [child]
            output = {
                "schema_version": "expansion.v1",
                "target_key": target,
                "decision": {"state": "expandable" if children else "leaf", "reason": "测试理由"},
                "division_basis": "职责",
                "children": children,
                "relations": [],
            }
        else:
            output = fixtures[operation]["output"]
        return ProviderResult(
            "fake-response", "fake-model", json.dumps(output), 100, 100, None, None
        )

    monkeypatch.setattr(ResponsesProvider, "generate", generate)
    experiment = Experiment(ROOT, settings, tmp_path / "run")
    try:
        experiment.learning_chain(
            {
                "id": "test",
                "domain": "固定替身",
                "user_brief": "固定替身输入",
                "known_targets": [],
                "answers_by_target": {},
                "expected_behavior": "useful_depth",
                "max_exploration_depth": 3,
            },
            1,
        )
        task = experiment.tasks[0]
        assert task["success"] and task["depth"] == 3
        assert task["expanded_paths"] == [["root", "a"], ["root", "b"], ["root", "b", "c"]]
        assert json.loads((experiment.directory / "tasks.json").read_text())[0]["success"]
        assert (experiment.directory / "source-snapshot/src/unfold/contracts.py").exists()
    finally:
        experiment.close()
