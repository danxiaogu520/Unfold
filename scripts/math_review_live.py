"""New public synthetic math scopes + real lessons/questions for human review."""

import argparse
import hashlib
import json
import shutil
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

from unfold.api_contract import CreateRun
from unfold.app.config import AppSettings, load_app_env
from unfold.app.db import Database
from unfold.app.generation import DeepSeekGenerator, FixtureGenerator
from unfold.app.learning import LearningAction
from unfold.app.migrate import migrate
from unfold.app.models import BudgetLedger, ModelCall
from unfold.app.service import Claim, RunService
from unfold.app.worker import Worker
from unfold.contracts import validate_pair
from unfold.provider import GenerationError, Settings, load_env_file

ROOT = Path(__file__).resolve().parents[1]
SCOPES = [
    (
        "quadratic",
        "二次函数",
        "二次函数 f(x)=x^2-4x+3 的定义域、配方法、对称轴、顶点、零点和最小值。",
    ),
    (
        "probability",
        "简单概率",
        "等可能有限样本空间；公平六面骰子一次投掷、两枚公平硬币独立投掷的基本概率。",
    ),
]


class Scaffold(FixtureGenerator):
    def generate(self, claim: Claim) -> dict:
        proposal = super().generate(claim)
        if claim.operation == "outline":
            proposal["children"] = [
                {
                    "key": key,
                    "title": title,
                    "scope": scope,
                    "kind": "concept",
                    "concept_ref": None,
                    "overview": "公开合成审阅范围；具体讲解和题目随后真实生成。",
                    "decision": {"state": "leaf", "reason": "固定审阅范围，仅用于构建评测材料。"},
                }
                for key, title, scope in SCOPES
            ]
            proposal["division_basis"] = "公开合成评测脚手架，不是真实模型大纲。"
            validate_pair(claim.operation, claim.input, proposal)
            with self.database.transaction() as session:
                call = session.scalar(select(ModelCall).where(ModelCall.run_id == claim.run_id))
                assert call
                call.response = proposal
        return proposal


class Limited(DeepSeekGenerator):
    def __init__(self, *args, run_ids: list[str], space_id: str):
        super().__init__(*args)
        self.run_ids, self.space_id = run_ids, space_id

    def reserve_call(self, claim: Claim, step: int, body: dict, **kwargs) -> str:
        assert self.service.trace(claim.run_id)["request"]["space_id"] == self.space_id
        with self.database.transaction() as session:
            calls = list(
                session.scalars(
                    select(ModelCall.id).where(
                        ModelCall.run_id.in_(self.run_ids), ModelCall.provider == "deepseek"
                    )
                )
            )
        if len(calls) >= 8:
            raise GenerationError("budget_exceeded", "public math review call cap")
        return super().reserve_call(claim, step, body, **kwargs)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model-env", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("Do not overwrite a real trial")
    load_app_env(ROOT / ".env.app.local")
    load_env_file(args.model_env)
    config = replace(AppSettings.from_env(), model_mode="deepseek", max_attempts=1)
    database = Database(config)
    migrate(database)
    service = RunService(database, config)
    args.output.mkdir(parents=True)
    sources = {}
    for source in sorted(
        {
            *ROOT.glob("src/**/*.py"),
            *ROOT.glob("src/**/*.sql"),
            *ROOT.glob("evals/prompts/*.json"),
            Path(__file__).resolve(),
        }
    ):
        name = str(source.relative_to(ROOT))
        sources[name] = hashlib.sha256(source.read_bytes()).hexdigest()
        target = args.output / "source-snapshot" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    space = service.create_space(
        "公开合成审阅：高中数学",
        "准备基础讲解与选择题供人工审阅；范围仅二次函数和等可能概率。"
        "这是假定的公开评测输入，不代表审阅者个人基础，不宣称官方高考题或课程对齐。",
    )
    fake = Worker(service, Scaffold(database, ROOT))

    def execute_fake(run_id: str) -> None:
        claim = service.claim(run_id)
        assert claim and fake.execute(claim)
        assert service.get_run(run_id).status == "succeeded"

    questionnaire = service.create_run(
        CreateRun(
            schema_version="api.v1",
            space_id=space["id"],
            operation="questionnaire",
            profile_revision=None,
            base_revision=None,
            input={
                "schema_version": "questionnaire-input.v1",
                "domain": space["domain"],
                "user_brief": space["user_brief"],
                "known_targets": ["purpose", "scope"],
                "previous_question_keys": [],
                "questions_asked": 0,
                "round": 1,
            },
        ),
        "math-review-intake",
    )
    execute_fake(questionnaire.run_id)
    intake = service.get_space(space["id"])["intakes"][0]["questionnaire"]
    profile = service.create_run(
        CreateRun(
            schema_version="api.v1",
            space_id=space["id"],
            operation="profile",
            profile_revision=None,
            base_revision=None,
            input={
                "schema_version": "profile-input.v1",
                "domain": space["domain"],
                "user_brief": space["user_brief"],
                "questionnaire": intake,
                "answers": [
                    {
                        "question_key": q["key"],
                        "selected_keys": [],
                        "free_text": None,
                        "unknown": True,
                    }
                    for q in intake["questions"]
                ],
            },
        ),
        "math-review-profile",
    )
    execute_fake(profile.run_id)
    outline = service.create_action(
        space["id"],
        LearningAction(operation="outline", expected_profile_revision=1),
        "math-review-scaffold",
    )
    execute_fake(outline.run_id)
    run_ids: list[str] = []
    generator = Limited(
        database, service, Settings.from_env(), ROOT, run_ids=run_ids, space_id=space["id"]
    )
    worker = Worker(service, generator)
    with database.transaction() as session:
        ledger = session.get(BudgetLedger, "model")
        assert ledger
        before = ledger.accounted_cost
    report = {
        "version": "math-review.v1",
        "data_classification": "public_synthetic_evaluation.v1",
        "reads_existing_learning_spaces": False,
        "scaffold": "deterministic_fixture",
        "lessons_and_questions": "real_deepseek",
        "independent_human_review": None,
        "space_id": space["id"],
        "max_real_calls": 8,
        "source_sha256": sources,
        "scope_frozen_before_calls": SCOPES,
        "ledger_before_cny": str(before),
        "tasks": [],
    }

    def save() -> None:
        with database.transaction() as session:
            calls = list(
                session.scalars(
                    select(ModelCall).where(
                        ModelCall.run_id.in_(run_ids), ModelCall.provider == "deepseek"
                    )
                )
            )
            ledger = session.get(BudgetLedger, "model")
            assert ledger
            report["ledger_after_cny"] = str(ledger.accounted_cost)
        report["real_calls"] = len(calls)
        report["unknown_usage_calls"] = sum(
            c.input_tokens is None or c.output_tokens is None for c in calls
        )
        report["conservative_cost_cny"] = str(
            sum(
                (
                    c.accounted_cost if c.accounted_cost is not None else c.reserved_cost
                    for c in calls
                ),
                Decimal(0),
            )
        )
        (args.output / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        )

    try:
        save()
        for key, title, scope in SCOPES:
            node = next(n for n in service.get_space(space["id"])["nodes"] if n["title"] == title)
            for operation in ("explanation", "exercises"):
                current = service.get_node(space["id"], node["id"])
                instruction = (
                    "写给熟悉高中数学的审阅者。严格限定："
                    + scope
                    + " 给出定义、必要条件、计算步骤和一个完整例子，"
                    "只使用 Markdown/公式，不请求媒体。"
                    if operation == "explanation"
                    else "仅依据已提供讲解出三道基础题，至少一道单选和一道多选；"
                    "条件明确，避免陷阱、大学数学和官方试题标记，解析展示计算。"
                )
                queued = service.create_action(
                    space["id"],
                    LearningAction(
                        operation=operation,
                        target_id=node["id"],
                        expected_profile_revision=1,
                        expected_base_revision=current["revision"],
                        instruction=instruction,
                        count=3,
                    ),
                    "math-review:" + key + ":" + operation,
                )
                run_ids.append(queued.run_id)
                claim = service.claim(queued.run_id)
                assert claim
                worker.execute(claim)
                trace = service.trace(queued.run_id)
                (args.output / (queued.run_id + ".json")).write_text(
                    json.dumps(trace, ensure_ascii=False, indent=2) + "\n"
                )
                report["tasks"].append(
                    {
                        "case": key,
                        "operation": operation,
                        "run_id": queued.run_id,
                        "status": trace["snapshot"]["status"],
                        "error": trace["snapshot"]["error"],
                    }
                )
                save()
                if trace["snapshot"]["status"] != "succeeded":
                    raise SystemExit(
                        "Real task failed; retain trace before producing review material"
                    )
    finally:
        save()
        database.close()
    print(json.dumps({k: v for k, v in report.items() if k not in ("source_sha256", "tasks")}))


if __name__ == "__main__":
    main()
