"""Recorded source-backed development resources; these perform no live retrieval."""

import json
from pathlib import Path
from typing import Any


class CachedCommons:
    provenance = "recorded_fixture_replay"

    def __init__(self, root: Path):
        self.directory = root / "evals/assets/m4"
        self.record = json.loads((self.directory / "commons-binary-search.source.json").read_text())

    def search(self, query: str) -> list[dict[str, Any]]:
        return [
            {
                **self.record["source"],
                "retrieval_mode": self.provenance,
                "requested_query": query,
                "teaching_suitability": "development_fixture",
            }
        ]

    def download(self, candidate: dict[str, Any]) -> bytes:
        return (self.directory / "commons-binary-search.png").read_bytes()

    def close(self) -> None:
        pass


class FixtureResourcePolicy:
    """Scripted substitute, explicitly not a live model choosing tools."""

    provenance = "deterministic_resource_fixture"

    def decide(self, claim: Any, number: int, observations: list[dict[str, Any]], mode: str) -> Any:
        from unfold.app.resource_agent import AgentDecision

        if number == 1:
            tool, arguments = (
                "make_component",
                {
                    "component_ref": "binary-search.v1",
                    "parameters": {"values": [2, 7, 11, 14, 18, 27, 33, 54, 61, 95], "target": 27},
                },
            )
        else:
            candidate = observations[-1]["result"]["candidate_id"]
            tool = "check_web" if number == 2 else "submit_candidate"
            arguments = {"candidate_id": candidate}
        return AgentDecision.model_validate(
            {
                "schema_version": "resource-agent-decision.v1",
                "action": "tool",
                "tool": tool,
                "arguments": arguments,
                "reason": "固定开发替身，用于功能验证，不是供应商模型决策。",
            }
        )
