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
