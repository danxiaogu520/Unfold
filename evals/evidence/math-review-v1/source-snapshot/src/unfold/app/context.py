"""Traceable context selection with mandatory goals and dependency-qualified extracts."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Literal

from unfold.provider import GenerationError, json_bytes

Strategy = Literal["all_history", "current_path", "structural_relevance"]
STRATEGIES = ("all_history", "current_path", "structural_relevance")


def terms(text: str) -> set[str]:
    lowered = text.casefold()
    latin = set(re.findall(r"[a-z0-9_]{2,}", lowered))
    for segment in re.findall(r"[\u4e00-\u9fff]+", lowered):
        latin.update(segment[index : index + 2] for index in range(len(segment) - 1))
    return latin


def basis(
    page: dict[str, Any],
    profile_revision: int,
    outline_revision: int,
    path_versions: dict[str, int],
) -> str:
    return hashlib.sha256(
        json_bytes(
            {
                "content_id": page["id"],
                "text": page["text"],
                "title": page["title"],
                "scope": page["scope"],
                "node_revision": page["node_revision"],
                "profile_revision": profile_revision,
                "outline_revision": outline_revision,
                "parent_id": page["parent_id"],
                "review_status": page.get("review_status", "unverified"),
                "provenance": page.get("provenance"),
                "path_versions": path_versions,
            }
        )
    ).hexdigest()


def extract(page: dict[str, Any], fingerprint: str, limit: int = 600) -> dict[str, Any]:
    text = page["text"]
    return {
        "content_id": page["id"],
        "node_id": page["node_id"],
        "title": page["title"],
        "scope": page["scope"],
        "text": text[:limit],
        "representation": "programmatic_extract.v1",
        "source_profile_revision": page["profile_revision"],
        "basis_sha256": fingerprint,
        "original_characters": len(text),
        "included_characters": min(limit, len(text)),
        "omitted_characters": max(0, len(text) - limit),
        "fact_status": page.get("review_status", "unverified"),
    }


@dataclass(frozen=True)
class ContextSelection:
    material: dict[str, Any]
    log: dict[str, Any]
    node_dependencies: dict[str, int]


def select_context(
    request: dict[str, Any],
    base_material: dict[str, Any],
    pages: list[dict[str, Any]],
    relations: list[dict[str, Any]],
    profile_history: list[dict[str, Any]],
    strategy: Strategy,
    *,
    budget_bytes: int = 24000,
    summaries: dict[str, dict[str, Any]] | None = None,
) -> ContextSelection:
    if strategy not in STRATEGIES:
        raise GenerationError("configuration", "unknown context strategy")
    mandatory_size = len(json_bytes({"input": request, "context": base_material}))
    if mandatory_size > budget_bytes:
        raise GenerationError(
            "budget_exceeded", "mandatory goals and node context exceed budget; nothing truncated"
        )
    path = {item["key"]: item["content_revision"] for item in request.get("path", [])}
    target = request.get("target", {}).get("key")
    child_ids = {item["key"] for item in request.get("existing_children", [])}
    required_nodes = set(path) | child_ids | ({target} if target else set())
    current = [
        page
        for page in pages
        if page["active"] and page["content_revision"] == page["node_revision"]
    ]
    required_pages = [page for page in current if page["node_id"] in required_nodes]
    material = {**base_material, "context_pages": [], "context_relations": []}
    selected: list[dict[str, Any]] = []
    omitted: list[dict[str, Any]] = []
    dependencies: dict[str, int] = {}

    def size() -> int:
        return len(json_bytes({"input": request, "context": material}))

    def full(page: dict[str, Any]) -> dict[str, Any]:
        return {
            "content_id": page["id"],
            "node_id": page["node_id"],
            "title": page["title"],
            "scope": page["scope"],
            "text": page["text"],
            "representation": "full",
            "source_profile_revision": page["profile_revision"],
            "fact_status": page.get("review_status", "unverified"),
        }

    def include(page: dict[str, Any], payload: dict[str, Any], reason: str, score: int = 0) -> bool:
        material["context_pages"].append(payload)
        if size() > budget_bytes:
            material["context_pages"].pop()
            return False
        selected.append(
            {
                "content_id": page["id"],
                "node_id": page["node_id"],
                "reason": reason,
                "score": score,
                "representation": payload["representation"],
            }
        )
        dependencies[page["node_id"]] = page["node_revision"]
        return True

    if strategy == "all_history":
        material["profile_history"] = profile_history
        material["context_relations"] = relations
        for page in pages:
            material["context_pages"].append(full(page))
            selected.append(
                {
                    "content_id": page["id"],
                    "node_id": page["node_id"],
                    "reason": "all_supplied_history",
                    "representation": "full",
                }
            )
            if page["active"]:
                dependencies[page["node_id"]] = page["node_revision"]
        if size() > budget_bytes:
            raise GenerationError(
                "budget_exceeded",
                "all supplied history exceeds budget; strategy not silently truncated",
            )
    else:
        for page in required_pages:
            if not include(page, full(page), "mandatory_current_path_or_children"):
                raise GenerationError(
                    "budget_exceeded", "current path bodies exceed budget; no partial path"
                )
        related = {row["target_id"] for row in relations if row["source_id"] in required_nodes}
        related |= {row["source_id"] for row in relations if row["target_id"] in required_nodes}
        query = terms(
            str(request.get("instruction", ""))
            + " "
            + str(request.get("feedback", ""))
            + " "
            + str(request.get("target", {}).get("title", ""))
            + " "
            + str(request.get("target", {}).get("scope", ""))
        )
        ranked = []
        for page in current:
            if page["node_id"] in required_nodes:
                continue
            score = len(query & terms(page["title"] + " " + page["scope"] + " " + page["text"]))
            if page["node_id"] in related:
                score += 30
            if page["parent_id"] == target or page["parent_id"] in child_ids:
                score += 15
            ranked.append((score, page))
        ranked.sort(key=lambda row: (-row[0], row[1]["id"]))
        for score, page in ranked:
            if strategy == "current_path" or score == 0:
                omitted.append(
                    {
                        "content_id": page["id"],
                        "reason": "outside_path"
                        if strategy == "current_path"
                        else "no_structural_or_keyword_match",
                        "score": score,
                    }
                )
                continue
            payload = (summaries or {}).get(page["id"], full(page))
            if not include(page, payload, "structure_and_keyword_rank", score):
                omitted.append(
                    {"content_id": page["id"], "reason": "optional_budget", "score": score}
                )
        selected_nodes = set(dependencies)
        material["context_relations"] = [
            row
            for row in relations
            if row["source_id"] in selected_nodes and row["target_id"] in selected_nodes
        ]
        if size() > budget_bytes:
            raise GenerationError(
                "budget_exceeded", "selected relation metadata exceeds context budget"
            )
    accounted = {row["content_id"] for row in selected} | {row["content_id"] for row in omitted}
    omitted.extend(
        {"content_id": page["id"], "reason": "historical_or_inactive_version", "score": 0}
        for page in pages
        if page["id"] not in accounted
    )
    log = {
        "version": "context-selection.v1",
        "strategy": strategy,
        "input_bytes": size(),
        "budget_bytes": budget_bytes,
        "mandatory_bytes": mandatory_size,
        "output_tokens_reserved": 4000,
        "available_pages": len(pages),
        "selected": selected,
        "excluded": omitted,
        "scope": "declared snapshot only",
        "silent_truncation": False,
    }
    return ContextSelection(material, log, dependencies)
