from dataclasses import replace
from pathlib import Path

import pytest
from sqlalchemy import select, text
from test_learning import seed

from unfold.app.db import Database
from unfold.app.generation import FixtureGenerator
from unfold.app.learning import LearningAction
from unfold.app.models import (
    AgentStep,
    ContentRevision,
    GenerationRun,
    ResourceCandidate,
    ResourceRevision,
)
from unfold.app.resource_agent import AgentDecision, ResourceAgent
from unfold.app.resources import Resources
from unfold.app.service import Claim, RunService
from unfold.provider import GenerationError

ROOT = Path(__file__).resolve().parents[1]


def web_setup(database: Database, directory: Path) -> tuple[RunService, Claim, str]:
    original, space_id = seed(database)
    service = RunService(
        database, replace(original.settings, data_directory=directory, retry_base_seconds=0)
    )
    node_id = service.get_space(space_id)["root_id"]
    run = service.create_action(
        space_id,
        LearningAction(
            operation="explanation",
            target_id=node_id,
            expected_profile_revision=1,
            expected_base_revision=1,
        ),
        "web-lecture",
    )
    claim = service.claim(run.run_id)
    assert claim
    proposal = FixtureGenerator(database, ROOT).generate(claim)
    proposal["resource_requests"] = [
        {
            "key": "demo",
            "kind": "interactive",
            "purpose": "看区间缩小",
            "specification": "复用二分查找组件，含命中、未命中、空数组和键盘操作",
            "runtime": "web",
            "controls": ["step", "reset"],
            "fallback_text": "按正文理解步骤",
        }
    ]
    proposal["blocks"].append(
        {
            "type": "interactive",
            "resource_key": "demo",
            "caption": "二分查找",
            "fallback_text": "资源失败时正文可读",
        }
    )
    assert service.finish_success(claim, proposal)
    with database.transaction() as session:
        content = session.get(ContentRevision, service.get_run(run.run_id).result_ref)
        assert content
        content.source = {**content.source, "evaluation_case": "search_component-1"}
    assert Resources.dispatch(service) == 1
    resource = service.claim()
    assert resource and resource.operation == "resource"
    return service, resource, node_id


def decision(tool: str | None, arguments: dict, reason: str = "test decision") -> AgentDecision:
    return AgentDecision.model_validate(
        {
            "schema_version": "resource-agent-decision.v1",
            "action": "tool" if tool else "stop",
            "tool": tool,
            "arguments": arguments,
            "reason": reason,
        }
    )


class ReuseModel:
    provenance = "scripted_test_policy"

    def __init__(self) -> None:
        self.calls = 0

    def decide(
        self, claim: Claim, number: int, observations: list[dict], mode: str
    ) -> AgentDecision:
        self.calls += 1
        if number == 1:
            return decision("query_resources", {"query": "二分查找"})
        if number == 2:
            return decision(
                "make_component",
                {
                    "component_ref": "binary-search.v1",
                    "parameters": {"values": [1, 3, 5], "target": 3},
                },
            )
        candidate = observations[-1]["result"]["candidate_id"]
        return decision(
            "check_web" if number == 3 else "submit_candidate", {"candidate_id": candidate}
        )


def test_actual_component_loop_checks_before_qualified_publication(
    database: Database, tmp_path: Path
) -> None:
    service, claim, node_id = web_setup(database, tmp_path)
    model = ReuseModel()
    proposal = ResourceAgent(service, model).generate(claim)
    assert model.calls == 4
    with database.transaction() as session:
        assert (
            session.scalar(select(ResourceRevision)) is None
        )  # Model offered, app has not published.
        steps = list(session.scalars(select(AgentStep).order_by(AgentStep.number)))
        assert [s.status for s in steps] == ["tool_completed"] * 4
    assert service.finish_success(claim, proposal)
    resource = service.get_node(
        claim.input["profile"].get("space_id", service.trace(claim.run_id)["request"]["space_id"]),
        node_id,
    )["contents"][0]["resources"][0]
    assert resource["status"] == "ready" and resource["versions"][0]["mime"] == "text/html"


def test_completed_steps_resume_without_model_or_tool_repetition(
    database: Database, tmp_path: Path
) -> None:
    service, first, _ = web_setup(database, tmp_path)
    model = ReuseModel()
    agent = ResourceAgent(service, model)
    saved = agent.save_step(first, 1, model.decide(first, 1, [], "agent"), "model_decision")
    observation = agent.execute_step(first, saved)
    assert observation["actual_registry"]
    with database.transaction() as session:
        session.execute(
            text(
                "UPDATE generation_runs SET lease_until=clock_timestamp()-interval '1 second' "
                "WHERE id=:id"
            ),
            {"id": first.run_id},
        )
    assert service.recover_expired() == 1
    second = service.claim(first.run_id)
    assert second and second.attempt == 2
    proposal = agent.generate(second)
    assert model.calls == 4
    assert service.finish_success(second, proposal) and not service.finish_success(first, proposal)
    assert len(service.trace(first.run_id)["tools"]) == 4


def test_write_side_effect_recovered_after_unknown_tool_window(
    database: Database, tmp_path: Path
) -> None:
    service, first, _ = web_setup(database, tmp_path)
    agent = ResourceAgent(service, ReuseModel())
    command = decision(
        "make_component",
        {"component_ref": "binary-search.v1", "parameters": {"values": [], "target": 1}},
    )
    step = agent.save_step(first, 1, command, "model_decision")
    output = agent.execute_tool(first, 1, command)  # Persisted file/candidate, no step output yet.
    with database.transaction() as session:
        row = session.get(AgentStep, (first.run_id, 1))
        assert row
        row.status = "tool_started"
        session.execute(
            text(
                "UPDATE generation_runs SET lease_until=clock_timestamp()-interval '1 second' "
                "WHERE id=:id"
            ),
            {"id": first.run_id},
        )
    assert service.recover_expired() == 1
    second = service.claim(first.run_id)
    assert second
    recovered = agent.execute_step(second, step)
    assert recovered["candidate_id"] == output["candidate_id"] and recovered["recovered"]
    with database.transaction() as session:
        assert len(list(session.scalars(select(ResourceCandidate)))) == 1


def test_tool_boundary_and_unchecked_candidate_cannot_publish(
    database: Database, tmp_path: Path
) -> None:
    service, claim, _ = web_setup(database, tmp_path)
    agent = ResourceAgent(service, ReuseModel())
    output = agent.execute_tool(claim, 1, decision("write_web", {"html": "<p>unchecked</p>"}))
    with pytest.raises(GenerationError, match="independent checks"):
        agent.execute_tool(
            claim, 2, decision("submit_candidate", {"candidate_id": output["candidate_id"]})
        )
    with pytest.raises(GenerationError, match="outside this task"):
        agent.execute_tool(claim, 2, decision("read_file", {"candidate_id": "other_run_candidate"}))
    with database.transaction() as session:
        assert (
            session.get(GenerationRun, claim.run_id).context_dependencies.get(
                "prepared_candidate_id"
            )
            is None
        )
    with pytest.raises(GenerationError):
        service.finish_success(claim, agent.proposal(claim))


def test_decision_and_time_budgets_end_and_stop_is_not_leaf(
    database: Database, tmp_path: Path
) -> None:
    service, claim, node_id = web_setup(database, tmp_path)

    class Endless:
        def decide(
            self, claim: Claim, number: int, observations: list[dict], mode: str
        ) -> AgentDecision:
            return decision("read_content", {})

    with pytest.raises(GenerationError) as error:
        ResourceAgent(service, Endless(), max_steps=2).generate(claim)
    assert error.value.code == "budget_exceeded"

    class Stop:
        def decide(
            self, claim: Claim, number: int, observations: list[dict], mode: str
        ) -> AgentDecision:
            return decision(None, {}, "Python execution unsupported")

    # Use a new run only after explicitly ending this attempt.
    service.finish_failure(claim, error.value)
    space_id = service.trace(claim.run_id)["request"]["space_id"]
    task_id = service.get_node(space_id, node_id)["contents"][0]["resources"][0]["id"]
    snapshot = Resources.retry(service, space_id, task_id)
    next_claim = service.claim(snapshot.run_id)
    assert next_claim
    with pytest.raises(GenerationError) as stopped:
        ResourceAgent(service, Stop()).generate(next_claim)
    assert stopped.value.code == "capability_limit"
