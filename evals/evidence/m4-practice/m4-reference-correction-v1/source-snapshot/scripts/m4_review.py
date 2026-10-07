"""Local reference correction only: no provider request and no model key loading."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import shutil
from decimal import Decimal
from pathlib import Path
from typing import Any

from unfold.app.config import AppSettings, load_app_env
from unfold.app.db import Database
from unfold.app.files import FileStore, StoredFile
from unfold.app.learning import FeedbackInput, LearningAction
from unfold.app.models import (
    BudgetLedger,
    GenerationRun,
    ModelCall,
    ResourceCandidate,
    ResourceRevision,
    ResourceTask,
    ToolInvocation,
)
from unfold.app.practice import Practice, QuestionIssue
from unfold.app.resources import Resources
from unfold.app.service import RunService, entity_id
from unfold.contracts import validate_pair

ROOT = Path(__file__).resolve().parents[1]
REFERENCES = [
    "https://docs.python.org/3/library/stdtypes.html#numeric-types-int-float-complex",
    "https://docs.oracle.com/en/java/javase/26/docs/specs/jls/jls-15.html#jls-15.18.2",
    "https://open-std.org/jtc1/sc22/wg14/www/docs/n2396.htm",
]


def correct_explanation(proposal: dict[str, Any]) -> dict[str, Any]:
    corrected = copy.deepcopy(proposal)
    for block in corrected["blocks"]:
        if block["type"] != "markdown" or "### 溢出条件" not in block["text"]:
            continue
        block["text"] = (
            "## 中点公式与适用前提\n\n"
            "这里使用非负数组下标：$0\\le l\\le r\\le M$，其中 $M$ 是整数类型的最大值。"
            "因此 $0\\le r-l\\le r$，并且 $l\\le l+\\lfloor(r-l)/2\\rfloor\\le r$，"
            "差值和最终中点都不会超出可表示范围。只写 $l\\le r$ 并不足以保证这个结论。\n\n"
            "直接计算 `(l + r) / 2` 可能先让数学和超出整数类型上限；"
            "改写为 `l + (r - l) / 2` 在上述非负下标前提下避免这个中间求和问题。\n\n"
            "### 不同语言分别处理\n\n"
            "- Java 的 `int` 是 32 位有符号整数，整数加法溢出保留数学和的低 32 位，可能回绕为负数。"
            "例如两个 `2000000000` 相加，结果按 Java 的整数规则回绕；"
            "这只是运算示例，不创建大数组。\n"
            "- 标准 C 的有符号整数溢出属于未定义行为，不能承诺它固定回绕或一定得到负数。"
            "C 的 `int` 位宽由实现决定，也不能普遍假定为 32 位。\n"
            "- Python 内建 `int` 是任意精度整数。在这里的整数算术中，"
            "`(l + r) // 2` 与 `l + (r - l) // 2` 结果相同；"
            "不需要后一写法来避免 Python 整数溢出。\n\n"
            "```python\na = [1, 3, 5]\ntarget = 3\nl, r = 0, 2\n"
            "m = l + (r - l) // 2\nvalue = a[m]\n"
            "print(m, value, value == target)  # 1 3 True\n```\n\n"
            "此代码只说明一次中点比较，没有展开循环和边界更新。"
        )
    corrected["blocks"].append(
        {
            "type": "markdown",
            "text": "定向复核参考："
            "[Python 内建数值类型](" + REFERENCES[0] + ")、[Java 整数加法](" + REFERENCES[1] + ")、"
            "[C 委员会对表达式越界规则的说明](" + REFERENCES[2] + "). "
            "本轮为代码代理按已读取规范做本地修订，未进行新的供应商推理或独立人审。",
        }
    )
    return corrected


def correct_questions(proposal: dict[str, Any], content_id: str) -> dict[str, Any]:
    corrected = copy.deepcopy(proposal)
    for question in corrected["exercises"]:
        question["content_refs"] = [content_id]
        if question["mode"] != "multiple":
            continue
        question["stem"] = (
            "在非负数组下标 0≤l≤r≤INT_MAX 的条件下，关于中点公式和语言整数规则，哪些说法正确？"
        )
        replacements = {
            "opt_overflow_cond": (
                "以 Java 32 位有符号 int 计算时，若数学和 l+r 超过上限，先求和会回绕，使中点不可靠"
            ),
            "opt_diff_safe": "在题干条件下，r-l 与 l+floor((r-l)/2) 均在整数类型可表示范围内",
            "opt_python_same": "Python 内建 int 任意精度，两种整数中点表达式的结果相同",
        }
        for option in question["options"]:
            if option["key"] in replacements:
                option["text"] = replacements[option["key"]]
        question["explanation"] = (
            "题干明确 0≤l≤r≤INT_MAX，因此 0≤r-l≤r，最终中点也在 [l,r] 内。"
            "Java int 加法溢出按低 32 位回绕；标准 C 的有符号溢出属于未定义行为，不能混写。"
            "Python 内建 int 是任意精度整数，两种写法结果相同。"
            "选项依赖题干前提，不泛化到任意负下标。"
        )
    return corrected


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    destination = args.output.resolve()
    if destination.exists():
        raise SystemExit("Use a new correction record, never overwrite evidence")
    load_app_env(ROOT / ".env.app.local")
    config = AppSettings.from_env()
    database = Database(config)
    service = RunService(database, config)
    original = json.loads((ROOT / "evals/runs/m4-live-v1/report.json").read_text())
    space_id, node_id = original["selection_frozen_before_calls"]["narrow"]
    explanation_trace = json.loads(
        (ROOT / "evals/runs/m4-live-v1/run_57741bf7332f4102ab5f7092a97798f3.json").read_text()
    )
    question_trace = json.loads(
        (ROOT / "evals/runs/m4-live-v1/run_be8c5bd55c60462b92e42b9957e77758.json").read_text()
    )
    destination.mkdir(parents=True)
    sources = {}
    for path in sorted(
        {*ROOT.glob("src/**/*.py"), *ROOT.glob("src/**/*.sql"), Path(__file__).resolve()}
    ):
        relative = str(path.relative_to(ROOT))
        sources[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        target = destination / "source-snapshot" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    report: dict[str, Any] = {
        "version": "m4-reference-correction.v1",
        "new_live_model_calls": 0,
        "new_image_downloads": 0,
        "source_sha256": sources,
        "references": REFERENCES,
        "independent_human_review": None,
        "tasks": [],
        "resource_reuse": [],
        "approval_review": "external model revision rejected; local correction used instead",
    }
    node = service.get_node(space_id, node_id)
    space = service.get_space(space_id)
    assert node == original["final_narrow_node"], "source changed since reviewed version"
    assert space["profile"] == explanation_trace["input"]["profile"]["profile"]
    before_content = node["contents"][0]
    old_question = next(q for q in node["exercise_sets"][0]["questions"] if q["mode"] == "multiple")
    issue = "需明确非负数组下标前提，并区分标准 C 的有符号溢出未定义行为与 Java int 的回绕。"
    service.feedback(
        space_id,
        node_id,
        FeedbackInput(
            expected_profile_revision=space["profile_revision"],
            expected_base_revision=node["revision"],
            kind="factual_error",
            text=issue,
        ),
    )
    with database.transaction() as session:
        report["disabled_question"] = Practice.report(
            session, space_id, old_question["exercise_revision_ref"], QuestionIssue(text=issue)
        )
        ledger = session.get(BudgetLedger, "model")
        assert ledger
        before_cost = ledger.accounted_cost

    try:
        for operation in ("explanation", "exercises"):
            node = service.get_node(space_id, node_id)
            run = service.create_action(
                space_id,
                LearningAction(
                    operation=operation,
                    target_id=node_id,
                    expected_profile_revision=space["profile_revision"],
                    expected_base_revision=node["revision"],
                    instruction=issue,
                ),
                "m4-local-correction:" + destination.name + ":" + operation,
            )
            claim = service.claim(run.run_id)
            assert claim
            candidate = (
                correct_explanation(explanation_trace["proposal"])
                if operation == "explanation"
                else correct_questions(question_trace["proposal"], node["contents"][0]["id"])
            )
            validate_pair(operation, claim.input, candidate)
            with database.transaction() as session:
                row = session.get(GenerationRun, run.run_id)
                assert row
                row.context_dependencies = {
                    **row.context_dependencies,
                    "publication_provenance": "agent_reference_correction",
                    "review_references": REFERENCES,
                    "correction_source_run": explanation_trace["snapshot"]["run_id"]
                    if operation == "explanation"
                    else question_trace["snapshot"]["run_id"],
                }
                session.add(
                    ModelCall(
                        id=entity_id("call"),
                        run_id=run.run_id,
                        attempt=claim.attempt,
                        step=1,
                        provider="agent_reference_correction",
                        model="no_new_provider_inference",
                        prompt_version="reference-correction.v1",
                        schema_version=candidate["schema_version"],
                        status="completed",
                        request=claim.input,
                        response={"candidate": candidate, "network_call": False},
                        input_tokens=None,
                        output_tokens=None,
                        reserved_cost=Decimal(0),
                        accounted_cost=Decimal(0),
                        currency="CNY",
                        latency_seconds=Decimal(0),
                        outcome_unknown=False,
                    )
                )
            assert service.finish_success(claim, candidate)
            trace = service.trace(run.run_id)
            report["tasks"].append(
                {"run_id": run.run_id, "operation": operation, "status": "succeeded"}
            )
            (destination / f"{run.run_id}.json").write_text(
                json.dumps(trace, ensure_ascii=False, indent=2) + "\n"
            )
        final = service.get_node(space_id, node_id)
        for item in final["contents"][0]["resources"]:
            old = next(
                r for r in before_content["resources"] if r["request_key"] == item["request_key"]
            )
            run = Resources.retry(service, space_id, item["id"])
            claim = service.claim(run.run_id)
            assert claim
            with database.transaction() as session:
                old_revision = session.get(ResourceRevision, old["versions"][0]["id"])
                assert old_revision
                source = session.get(ResourceCandidate, old_revision.candidate_id)
                assert source and FileStore(config.data_directory).verify(StoredFile(**source.file))
                task = session.get(ResourceTask, item["id"])
                assert (
                    task
                    and task.request["specification"]
                    == before_content["resources"][0]["request"]["specification"]
                )
                candidate = ResourceCandidate(
                    id=entity_id("candidate"),
                    run_id=claim.run_id,
                    attempt=claim.attempt,
                    file=source.file,
                    source={
                        **source.source,
                        "retrieval_mode": "stored_file_reuse",
                        "reused_candidate_id": source.id,
                    },
                    proposal=source.proposal,
                )
                session.add(candidate)
                session.add(
                    ToolInvocation(
                        id=entity_id("tool"),
                        run_id=claim.run_id,
                        attempt=claim.attempt,
                        step=1,
                        tool="stored_commons_file_copy.v1",
                        status="completed",
                        input={"source_candidate_id": source.id},
                        output={"candidate_id": candidate.id, "network_call": False},
                        outcome_unknown=False,
                    )
                )
                proposal = source.proposal
            assert service.finish_success(claim, proposal)
            report["resource_reuse"].append(service.trace(run.run_id))
        report["final_node"] = service.get_node(space_id, node_id)
        assert report["final_node"]["contents"][1]["review_status"] == "reported"
        with database.transaction() as session:
            ledger = session.get(BudgetLedger, "model")
            assert ledger and ledger.accounted_cost == before_cost
            report["ledger_after_cny"] = str(ledger.accounted_cost)
    finally:
        (destination / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        )
        database.close()
    print(
        json.dumps(
            {
                "local_corrections": 2,
                "new_live_model_calls": 0,
                "new_downloads": 0,
                "ledger_after_cny": report["ledger_after_cny"],
            }
        )
    )


if __name__ == "__main__":
    main()
