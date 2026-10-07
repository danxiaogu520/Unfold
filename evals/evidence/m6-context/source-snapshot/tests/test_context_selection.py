from copy import deepcopy
from pathlib import Path

import pytest
from test_learning import seed

from unfold.app.context import basis, extract, select_context
from unfold.app.context_store import context_from_store
from unfold.app.db import Database
from unfold.app.generation import FixtureGenerator
from unfold.app.learning import Knowledge, LearningAction, ProfileEdit
from unfold.app.models import Space
from unfold.app.worker import Worker
from unfold.provider import GenerationError


def materials() -> tuple[dict, list[dict], list[dict]]:
    request = {
        "profile": {"revision": 1, "profile": {"purpose": "保留这个明确目标"}},
        "target": {"key": "target", "title": "HTTP", "scope": "消息边界"},
        "path": [{"key": "root", "content_revision": 1}, {"key": "target", "content_revision": 1}],
        "existing_children": [],
        "instruction": "解释 HTTP 消息边界",
    }
    pages = [
        {
            "id": "content_" + node,
            "node_id": node,
            "parent_id": parent,
            "node_revision": 1,
            "content_revision": 1,
            "profile_revision": 1,
            "title": title,
            "scope": scope,
            "text": body,
            "active": True,
            "review_status": "unverified",
        }
        for node, parent, title, scope, body in [
            ("root", None, "协议", "协议总览", "总览"),
            ("target", "root", "HTTP", "消息边界", "当前路径正文"),
            ("related", "other", "HTTP 长度", "请求边界", "相关长度定义 " * 80),
            ("other", "root", "音乐", "节奏", "无关正文" * 40),
        ]
    ]
    relations = [
        {
            "id": "r",
            "source_id": "target",
            "target_id": "related",
            "kind": "related",
            "explanation": "与消息边界有关",
        }
    ]
    return request, pages, relations


def test_three_strategies_record_selection_and_preserve_required_goal() -> None:
    request, pages, relations = materials()
    current = select_context(request, {}, pages, relations, [], "current_path")
    structural = select_context(request, {}, pages, relations, [], "structural_relevance")
    all_history = select_context(request, {}, pages, relations, [], "all_history")
    assert len(current.material["context_pages"]) == 2
    assert len(structural.material["context_pages"]) == 3
    assert len(all_history.material["context_pages"]) == 4
    assert structural.material["context_relations"] == relations
    assert structural.log["selected"][-1]["reason"] == "structure_and_keyword_rank"
    accounted = structural.log["selected"] + structural.log["excluded"]
    assert {r["content_id"] for r in accounted} == {r["id"] for r in pages}
    assert request["profile"]["profile"]["purpose"] == "保留这个明确目标"


def test_full_history_limit_is_a_failure_not_a_different_truncation() -> None:
    request, pages, relations = materials()
    pages[-1]["text"] = "huge" * 10000
    with pytest.raises(GenerationError, match="not silently truncated"):
        select_context(request, {}, pages, relations, [], "all_history")
    selected = select_context(request, {}, pages, relations, [], "current_path")
    assert len(selected.material["context_pages"]) == 2
    request["profile"]["profile"]["purpose"] = "goal" * 10000
    with pytest.raises(GenerationError, match="nothing truncated"):
        select_context(request, {}, pages, relations, [], "current_path")


def test_extract_records_omission_and_content_goal_path_dependencies() -> None:
    _, pages, _ = materials()
    page = pages[2]
    fingerprint = basis(page, 1, 1, {"root": 1, "target": 1})
    summary = extract(page, fingerprint, limit=100)
    assert summary["omitted_characters"] == len(page["text"]) - 100
    assert summary["representation"] == "programmatic_extract.v1"
    changed = deepcopy(page)
    changed["text"] += " revision"
    assert basis(changed, 1, 1, {"root": 1, "target": 1}) != fingerprint
    assert basis(page, 2, 1, {"root": 1, "target": 1}) != fingerprint
    assert basis(page, 1, 2, {"root": 1, "target": 1}) != fingerprint
    assert basis(page, 1, 1, {"root": 2, "target": 1}) != fingerprint
    changed = deepcopy(page)
    changed["parent_id"] = "new_parent"
    assert basis(changed, 1, 1, {"root": 1, "target": 1}) != fingerprint
    changed = deepcopy(page)
    changed["review_status"] = "reported"
    assert basis(changed, 1, 1, {"root": 1, "target": 1}) != fingerprint


def test_persistent_summary_cache_rebuilds_after_profile_change(database: Database) -> None:
    service, space_id = seed(database)
    node_id = service.get_space(space_id)["root_id"]

    def build(revision: int, node_revision: int = 1) -> dict:
        with database.transaction() as session:
            space = session.get(Space, space_id)
            assert space
            request = Knowledge.build_request(
                session,
                space,
                LearningAction(
                    operation="explanation",
                    target_id=node_id,
                    expected_profile_revision=revision,
                    expected_base_revision=node_revision,
                ),
            )
            base = Knowledge.verify_input(session, space, request)
            return context_from_store(
                session, space, request.input, base["selected_context"], "structural_relevance"
            )

    first = build(1)
    second = build(1)
    assert not any(row["cache_hit"] for row in first["summary_cache"])
    assert all(row["cache_hit"] for row in second["summary_cache"])
    service.edit_profile(
        space_id, ProfileEdit(expected_revision=1, fields={"primary_purpose": "新的用途"})
    )
    third = build(2)
    assert not any(row["cache_hit"] for row in third["summary_cache"])
    assert {row["basis_sha256"] for row in third["summary_cache"]}.isdisjoint(
        {row["basis_sha256"] for row in second["summary_cache"]}
    )
    run = service.create_action(
        space_id,
        LearningAction(
            operation="explanation",
            target_id=node_id,
            expected_profile_revision=2,
            expected_base_revision=1,
        ),
        "new-content-version",
    )
    claim = service.claim(run.run_id)
    assert claim
    assert Worker(service, FixtureGenerator(database, Path(__file__).resolve().parents[1])).execute(
        claim
    )
    fourth = build(2, 2)
    assert not any(row["cache_hit"] for row in fourth["summary_cache"])
    assert {row["basis_sha256"] for row in fourth["summary_cache"]}.isdisjoint(
        {row["basis_sha256"] for row in third["summary_cache"]}
    )
