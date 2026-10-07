from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select
from test_learning import seed

from unfold.app.api import create_app
from unfold.app.db import Database
from unfold.app.generation import FixtureGenerator
from unfold.app.learning import LearningAction
from unfold.app.models import ContentRevision
from unfold.app.worker import Worker

ROOT = Path(__file__).resolve().parents[1]


def test_distinct_strategy_does_not_reuse_wrong_active_material(database: Database) -> None:
    service, space_id = seed(database)
    node_id = service.get_space(space_id)["root_id"]
    action = LearningAction(
        operation="explanation",
        target_id=node_id,
        expected_profile_revision=1,
        expected_base_revision=1,
    )
    first = service.create_action(space_id, action, "path")
    second = service.create_action(
        space_id, action.model_copy(update={"context_strategy": "all_history"}), "history"
    )
    assert first.run_id != second.run_id
    assert (
        service.trace(first.run_id)["dependencies"]["selection_log"]["strategy"] == "current_path"
    )
    assert (
        service.trace(second.run_id)["dependencies"]["selection_log"]["strategy"] == "all_history"
    )
    assert service.create_action(space_id, action, "path-alias").run_id == first.run_id
    assert Worker(service, FixtureGenerator(database, ROOT)).tick()
    assert service.get_run(first.run_id).status == "succeeded"
    assert service.claim(second.run_id) is None
    assert service.get_run(second.run_id).status == "conflicted"


def test_full_history_overflow_is_http_413_without_new_run_or_leaf(database: Database) -> None:
    service, space_id = seed(database)
    nodes = service.get_space(space_id)["nodes"]
    target = next(n for n in nodes if n["parent_id"] and n["decision"]["state"] == "expandable")
    other = next(n for n in nodes if n["parent_id"] and n["decision"]["state"] == "leaf")
    with database.transaction() as session:
        content = session.scalar(
            select(ContentRevision).where(ContentRevision.node_id == other["id"])
        )
        assert content
        content.blocks = [{"type": "markdown", "text": "公开测试超长旁支正文。" * 4000}]
    before = service.get_space(space_id)["runs"]
    action = LearningAction(
        operation="explanation",
        target_id=target["id"],
        expected_profile_revision=1,
        expected_base_revision=1,
        context_strategy="all_history",
    )
    with TestClient(create_app(service.settings)) as client:
        response = client.post(
            f"/api/v1/spaces/{space_id}/actions",
            json=action.model_dump(mode="json"),
            headers={"Idempotency-Key": "overflow"},
        )
    assert response.status_code == 413
    assert response.json()["code"] == "budget_exceeded"
    assert service.get_space(space_id)["runs"] == before
    assert service.get_node(space_id, target["id"])["decision"]["state"] == "expandable"
    current = service.create_action(
        space_id,
        action.model_copy(update={"context_strategy": "current_path"}),
        "path-after-overflow",
    )
    assert current.status == "queued"
