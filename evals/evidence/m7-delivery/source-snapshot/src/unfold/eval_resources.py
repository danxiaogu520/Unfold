"""Explicit public synthetic fixtures for provider evaluations; no private-space reads."""

from pathlib import Path
from typing import Any

from sqlalchemy import select

from unfold.api_contract import CreateRun
from unfold.app.generation import FixtureGenerator
from unfold.app.learning import LearningAction
from unfold.app.models import ContentRevision, ModelCall
from unfold.app.service import RunService
from unfold.app.worker import Worker

ROOT = Path(__file__).resolve().parents[2]


def public_space(service: RunService, case: dict[str, Any], prefix: str) -> tuple[str, str]:
    """Construct a new labelled synthetic case using public fixture values only."""
    fixture = FixtureGenerator(service.database, ROOT)
    worker = Worker(service, fixture)
    domain = "公开合成资源评测：" + case["family"]
    brief = case["task"]
    space = service.create_space(domain, brief)

    def execute(run_id: str) -> None:
        claim = service.claim(run_id)
        assert claim and worker.execute(claim)
        assert service.get_run(run_id).status == "succeeded"

    intake = service.create_run(
        CreateRun(
            schema_version="api.v1",
            space_id=space["id"],
            operation="questionnaire",
            profile_revision=None,
            base_revision=None,
            input={
                "schema_version": "questionnaire-input.v1",
                "domain": domain,
                "user_brief": brief,
                "known_targets": ["purpose"],
                "previous_question_keys": [],
                "questions_asked": 0,
                "round": 1,
            },
        ),
        prefix + ":intake",
    )
    execute(intake.run_id)
    saved = service.get_space(space["id"])["intakes"][0]
    answers = [
        {"question_key": q["key"], "selected_keys": [], "free_text": None, "unknown": True}
        for q in saved["questionnaire"]["questions"]
    ]
    profile = service.create_run(
        CreateRun(
            schema_version="api.v1",
            space_id=space["id"],
            operation="profile",
            profile_revision=None,
            base_revision=None,
            input={
                "schema_version": "profile-input.v1",
                "domain": domain,
                "user_brief": brief,
                "questionnaire": saved["questionnaire"],
                "answers": answers,
            },
        ),
        prefix + ":profile",
    )
    execute(profile.run_id)
    outline = service.create_action(
        space["id"],
        LearningAction(operation="outline", expected_profile_revision=1),
        prefix + ":outline",
    )
    execute(outline.run_id)
    node_id = service.get_space(space["id"])["root_id"]
    lesson = service.create_action(
        space["id"],
        LearningAction(
            operation="explanation",
            expected_profile_revision=1,
            expected_base_revision=1,
            target_id=node_id,
        ),
        prefix + ":lesson",
    )
    claim = service.claim(lesson.run_id)
    assert claim
    proposal = fixture.generate(claim)
    kind = "playground" if case["family"] == "python_database" else "interactive"
    controls = (
        ["run", "stop", "reset"]
        if kind == "playground"
        else ["step", "reset"]
        if case["family"] == "search_component"
        else ["parameters", "reset"]
    )
    requirements = {
        "search_component": "复用 binary-search.v1，升序数组，命中、未命中、空数组与键盘。",
        "function_plot": (
            "制作 f(x)=k*x+b 的独立网页。k,b 支持 -3..3、步长0.5；x 可编辑。"
            "显示曲线与数值。重置 k=1,b=0,x=2。"
        ),
        "python_database": "需要真正执行 Python 并查询 SQLite 数据库；不能用网页静态结果冒充。",
    }[case["family"]]
    proposal["blocks"] = [
        {"type": "markdown", "text": "公开合成评测资料：" + brief},
        {
            "type": kind,
            "resource_key": "public_demo",
            "caption": "公开评测演示",
            "fallback_text": "制作失败保留文本说明。",
        },
    ]
    proposal["resource_requests"] = [
        {
            "key": "public_demo",
            "kind": kind,
            "purpose": brief,
            "specification": requirements,
            "runtime": "web",
            "controls": controls,
            "fallback_text": "本任务用于公开合成资源流程评测，不代表真实学习者数据。",
        }
    ]
    with service.database.transaction() as session:
        call = session.scalar(select(ModelCall).where(ModelCall.run_id == claim.run_id))
        assert call
        call.response = proposal
    assert service.finish_success(claim, proposal)
    with service.database.transaction() as session:
        content = session.get(ContentRevision, service.get_run(lesson.run_id).result_ref)
        assert content
        content.source = {
            **content.source,
            "evaluation_case": case["id"],
            "data_classification": "public_synthetic_evaluation.v1",
        }
    task = service.get_node(space["id"], node_id)["contents"][0]["resources"][0]
    return space["id"], task["id"]
