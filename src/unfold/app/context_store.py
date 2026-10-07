"""Authoritative snapshot and dependency-qualified extract cache."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from unfold.app.context import Strategy, basis, extract, select_context
from unfold.app.models import (
    ContentRevision,
    ContentSummary,
    OutlineItem,
    ProfileRevision,
    RelationRecord,
    Space,
)


def snapshot(session: Session, space: Space) -> dict[str, Any]:
    nodes = {
        node.id: node
        for node in session.scalars(select(OutlineItem).where(OutlineItem.space_id == space.id))
    }
    pages = []
    for content in session.scalars(
        select(ContentRevision)
        .where(ContentRevision.space_id == space.id)
        .order_by(ContentRevision.id)
    ):
        node = nodes[content.node_id]
        text = "\n\n".join(
            block.get("text", block.get("fallback_text", "")) for block in content.blocks
        )
        pages.append(
            {
                "id": content.id,
                "node_id": node.id,
                "parent_id": node.parent_id,
                "node_revision": node.revision,
                "content_revision": content.revision,
                "profile_revision": content.profile_revision,
                "title": node.title,
                "scope": node.scope,
                "text": text or content.overview,
                "active": node.active,
                "review_status": content.review_status,
                "provenance": content.source.get("publication_provenance", "model_candidate"),
            }
        )
    relations = [
        {
            "id": row.id,
            "source_id": row.source_id,
            "target_id": row.target_id,
            "kind": row.kind,
            "explanation": row.explanation,
        }
        for row in session.scalars(
            select(RelationRecord).where(RelationRecord.space_id == space.id)
        )
    ]
    history = [
        {"revision": row.revision, "profile": row.proposal}
        for row in session.scalars(
            select(ProfileRevision)
            .where(ProfileRevision.space_id == space.id)
            .order_by(ProfileRevision.revision)
        )
    ]
    return {
        "version": "context-space-snapshot.v1",
        "profile_revision": space.profile_revision,
        "outline_revision": space.outline_revision,
        "pages": pages,
        "relations": relations,
        "profile_history": history,
    }


def context_from_store(
    session: Session,
    space: Space,
    request: dict[str, Any],
    material: dict[str, Any],
    strategy: Strategy,
) -> dict[str, Any]:
    data = snapshot(session, space)
    path_versions = {item["key"]: item["content_revision"] for item in request.get("path", [])}
    summaries = {}
    cache_log = []
    if strategy == "structural_relevance":
        existing = {
            (row.content_id, row.basis_digest): row
            for row in session.scalars(
                select(ContentSummary).where(ContentSummary.space_id == space.id)
            )
        }
        for page in data["pages"]:
            if not page["active"] or page["content_revision"] != page["node_revision"]:
                continue
            fingerprint = basis(page, space.profile_revision, space.outline_revision, path_versions)
            cached = existing.get((page["id"], fingerprint))
            if cached:
                summary = cached.summary
            else:
                summary = extract(page, fingerprint)
                session.add(
                    ContentSummary(
                        space_id=space.id,
                        content_id=page["id"],
                        basis_digest=fingerprint,
                        dependencies={
                            "profile_revision": space.profile_revision,
                            "outline_revision": space.outline_revision,
                            "node_revision": page["node_revision"],
                            "parent_id": page["parent_id"],
                            "path_versions": path_versions,
                        },
                        summary=summary,
                    )
                )
            summaries[page["id"]] = summary
            cache_log.append(
                {
                    "content_id": page["id"],
                    "basis_sha256": fingerprint,
                    "cache_hit": cached is not None,
                    "source": "programmatic_extract.v1",
                }
            )
    selected = select_context(
        request,
        material,
        data["pages"],
        data["relations"],
        data["profile_history"],
        strategy,
        summaries=summaries,
    )
    return {
        "selected_context": selected.material,
        "selection_log": selected.log,
        "summary_cache": cache_log,
        "additional_node_dependencies": selected.node_dependencies,
        "context_bytes": selected.log["input_bytes"],
        "context_strategy": strategy,
    }
