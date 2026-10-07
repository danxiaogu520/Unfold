from __future__ import annotations

import json
import time
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

from pydantic import ValidationError
from sqlalchemy import select

from unfold.app.db import Database
from unfold.app.examples import validate_http_json_examples
from unfold.app.models import BudgetLedger, GenerationRun, ModelCall
from unfold.app.security import sanitized
from unfold.app.service import Claim, RunService, database_clock, entity_id
from unfold.contracts import OUTPUTS, Contract, ExpansionProposal, OutlineProposal, validate_pair
from unfold.evaluation import read_json
from unfold.provider import (
    ErrorCode,
    GenerationError,
    ProviderResult,
    ResponsesProvider,
    Settings,
    json_bytes,
    provider_schema,
)


class Generator(Protocol):
    def generate(self, claim: Claim) -> dict[str, Any]: ...


class FixtureGenerator:
    """Deterministic development substitute; its provenance is always explicit."""

    def __init__(self, database: Database, root: Path, *, failure: bool = False):
        self.database = database
        self.fixtures = {
            row["operation"]: row for row in read_json(root / "evals/contracts/success.v1.json")
        }
        self.failure = failure

    def generate(self, claim: Claim) -> dict[str, Any]:
        if self.failure:
            raw: dict[str, Any] = {"schema_version": "invalid.v0"}
        else:
            raw = dict(self.fixtures[claim.operation]["output"])
            if claim.operation == "questionnaire":
                # M1 checks transport/persistence, not education quality. Respect
                # the caller's known dimensions while clearly labeling this fake.
                raw["questions"] = [
                    q for q in raw["questions"] if q["target"] not in claim.input["known_targets"]
                ]
                if claim.input["round"] > 1:
                    raw["questions"] = [
                        {**q, "key": q["key"] + "_round2"} for q in raw["questions"]
                    ]
                raw["questions"] = raw["questions"][: 5 - claim.input["questions_asked"]]
                raw["rationale"] = "固定开发替身，尚未进行真实模型生成。"
            elif claim.operation == "profile":

                def fact(
                    value: str, source: str = "assumption", evidence: str | None = None
                ) -> dict[str, Any]:
                    return {
                        "value": value,
                        "source": source,
                        "evidence_keys": [evidence] if evidence else [],
                    }

                rounds = claim.input.get("rounds", [claim.input])
                answers = {
                    answer["question_key"]: answer for item in rounds for answer in item["answers"]
                }
                background = fact("尚未确定当前基础", "unknown")
                for question in [q for item in rounds for q in item["questionnaire"]["questions"]]:
                    answer = answers[question["key"]]
                    if question["target"] == "background" and not answer["unknown"]:
                        values = [
                            option["text"]
                            for option in question["options"]
                            if option["key"] in answer["selected_keys"]
                        ]
                        if answer["free_text"]:
                            values.append(answer["free_text"])
                        background = fact("；".join(values), "user", "answer:" + question["key"])
                raw = {
                    "schema_version": "profile.v1",
                    "topic": fact(claim.input["domain"], "user", "brief"),
                    "primary_purpose": fact(claim.input["user_brief"], "user", "brief"),
                    "secondary_purposes": [],
                    "background": background,
                    "target_abilities": [fact("先理解核心概念，再结合用途练习")],
                    "included_scope": [fact(claim.input["domain"])],
                    "excluded_scope": [],
                    "constraints": [],
                }
            elif claim.operation == "outline":
                topic = claim.input["profile"]["profile"]["topic"]["value"]

                def node(key: str, title: str, state: str = "leaf") -> dict[str, Any]:
                    return {
                        "key": key,
                        "title": title,
                        "kind": "concept",
                        "concept_ref": None,
                        "scope": title,
                        "overview": "固定开发概述，不是实际模型生成。",
                        "decision": {"state": state, "reason": "固定开发判定，仅用于流程验证。"},
                    }

                raw = {
                    "schema_version": "outline.v1",
                    "root": node("root", topic, "expandable"),
                    "division_basis": "固定示例：概念与应用",
                    "children": [
                        node("concepts", "核心概念", "expandable"),
                        node("application", "应用与边界"),
                    ],
                    "relations": [],
                }
            elif claim.operation == "expansion":
                raw = {
                    "schema_version": "expansion.v1",
                    "target_key": claim.input["target"]["key"],
                    "decision": {"state": "leaf", "reason": "固定开发替身演示停止判定。"},
                    "division_basis": "固定示例",
                    "children": [],
                    "relations": [],
                }
                if len(claim.input["path"]) < 3:
                    raw["decision"]["state"] = "expandable"
                    raw["children"] = [
                        {
                            "key": "example",
                            "title": "基本例子",
                            "kind": "concept",
                            "concept_ref": None,
                            "scope": "固定开发例子",
                            "overview": "固定例子概述。",
                            "decision": {"state": "leaf", "reason": "固定开发终点。"},
                        }
                    ]
            elif claim.operation == "explanation":
                target = claim.input["target"]
                raw = {
                    "schema_version": "explanation.v1",
                    "target_key": target["key"],
                    "overview": target["overview"],
                    "resource_requests": [],
                    "blocks": [
                        {
                            "type": "markdown",
                            "text": "## "
                            + target["title"]
                            + "\n\n固定开发讲解，不是实际模型生成。\n\n当前范围："
                            + target["scope"],
                        }
                    ],
                }
            elif claim.operation == "reassessment":
                raw = {
                    "schema_version": "reassessment.v1",
                    "target_key": claim.input["target"]["key"],
                    "decision": {"state": "expandable", "reason": "固定开发重评结果。"},
                    "restructuring_note": None,
                }
            elif claim.operation == "exercises":
                raw = {
                    "schema_version": "exercises.v1",
                    "target_key": claim.input["target"]["key"],
                    "exercises": [
                        {
                            "key": "question_" + str(index),
                            "mode": "single" if index % 2 == 0 else "multiple",
                            "stem": "开发替身：以下哪些表述与当前范围一致？",
                            "testing_point": "固定替身，仅验证选择题流程。",
                            "content_refs": [claim.input["provided_content"][0]["revision_ref"]],
                            "options": [
                                {"key": "scope", "text": claim.input["target"]["scope"]},
                                {"key": "unknown", "text": "任何问题都不需要条件或范围。"},
                                {"key": "purpose", "text": "结合当前学习用途理解这个节点。"},
                            ],
                            "correct_keys": ["scope"] if index % 2 == 0 else ["scope", "purpose"],
                            "explanation": "固定开发答案与解析，不代表实际模型教学质量。",
                        }
                        for index in range(claim.input["count"])
                    ],
                }
        error: ErrorCode | None = None
        try:
            validate_pair(claim.operation, claim.input, raw)
        except (ValueError, ValidationError):
            error = "schema_validation"
        with self.database.transaction() as session:
            session.add(
                ModelCall(
                    id=entity_id("call"),
                    run_id=claim.run_id,
                    attempt=claim.attempt,
                    step=1,
                    provider="deterministic_fake",
                    model="fixture.v2",
                    prompt_version="fixture.v2",
                    schema_version=raw.get("schema_version", "unknown"),
                    status="failed" if error else "completed",
                    request=sanitized(claim.input),
                    response=sanitized(raw),
                    input_tokens=None,
                    output_tokens=None,
                    reserved_cost=Decimal(0),
                    accounted_cost=Decimal(0),
                    currency="CNY",
                    latency_seconds=Decimal(0),
                    error_code=error,
                    outcome_unknown=False,
                )
            )
        if error:
            raise GenerationError(error, "development fixture did not validate")
        return raw


def validate_candidate_context(proposal: Contract, context: dict[str, Any]) -> None:
    if isinstance(proposal, OutlineProposal):
        nodes = [proposal.root, *proposal.children]
    elif isinstance(proposal, ExpansionProposal):
        nodes = proposal.children
    else:
        return
    concepts = {item["id"]: item for item in context.get("concepts", [])}
    for node in nodes:
        if node.concept_ref is None:
            continue
        concept = concepts.get(node.concept_ref)
        if concept is None or node.scope != concept["scope"]:
            raise ValueError(
                "concept_ref requires the saved exact scope; display titles may differ by path; "
                "new or narrower sub-concepts must use null"
            )


class DeepSeekGenerator:
    def __init__(self, database: Database, service: RunService, settings: Settings, root: Path):
        self.database = database
        self.service = service
        self.settings = settings
        prompt_path = root / "evals/prompts/app.v3.json"
        self.prompts = read_json(
            prompt_path if prompt_path.exists() else root / "evals/prompts/stages.v1.json"
        )

    def build_body(
        self, claim: Claim, repair: str | None = None, previous_candidate: object = None
    ) -> dict[str, Any]:
        schema = provider_schema(claim.operation)
        instruction = self.prompts["system"] + "\n" + self.prompts[claim.operation]
        instruction += "\n完整JSON Schema：" + json.dumps(schema, ensure_ascii=False)
        if claim.operation == "questionnaire":
            allowed = sorted(
                {"purpose", "background", "goal", "scope", "constraints"}
                - set(claim.input["known_targets"])
            )
            instruction += "\n本次仅可提问target=" + json.dumps(allowed)
            instruction += "；每种target最多一题，已知维度不重新细问，足够开始可以零题。"
        if repair:
            instruction += "\n上一候选失败，修复："
            instruction += str(sanitized(repair, (self.settings.api_key,)))
            instruction += "\n请对previous_candidate作最小修复，保留目标和有效内容，"
            instruction += "按每条确定性检查结果修正对应字段，不随意更换示例正文。"
        payload = {
            "operation": claim.operation,
            "input": sanitized(claim.input),
            "context": sanitized(claim.context),
        }
        if repair:
            payload["previous_candidate"] = sanitized(previous_candidate, (self.settings.api_key,))
        return {
            "model": self.settings.model,
            "instructions": instruction,
            "input": json.dumps(payload, ensure_ascii=False),
            "reasoning": {"effort": "none"},
            "temperature": 0.2,
            "max_output_tokens": self.settings.max_output_tokens,
            "text": {
                "format": {"type": "json_schema", "name": claim.operation + "_v1", "schema": schema}
            },
        }

    def reserve_call(
        self,
        claim: Claim,
        step: int,
        body: dict[str, Any],
        *,
        schema_version: str | None = None,
        prompt_version: str | None = None,
    ) -> str:
        size = len(json_bytes(body))
        if size > self.settings.max_input_bytes:
            raise GenerationError(
                "budget_exceeded", "required generation context exceeds byte budget"
            )
        cost = (
            Decimal(size + 4096) * self.settings.input_rate
            + Decimal(self.settings.max_output_tokens) * self.settings.output_rate
        ) / Decimal(1_000_000)
        # Heartbeat also verifies the attempt is still eligible before paying.
        if not self.service.heartbeat(claim):
            raise GenerationError("cancelled", "attempt no longer eligible")
        with self.database.transaction() as session:
            # Fixed account-wide lock prevents simultaneous first-row creation.
            from sqlalchemy import text

            session.execute(text("SELECT pg_advisory_xact_lock(180002)"))
            run = session.scalar(
                select(GenerationRun)
                .where(GenerationRun.id == claim.run_id)
                .with_for_update(key_share=True)
            )
            # The earlier heartbeat is only a fast check. Waiting for the account
            # lock may cross cancellation, lease expiry or the attempt deadline.
            if not self.service.eligible(run, claim, database_clock(session)):
                raise GenerationError("cancelled", "attempt no longer eligible")
            ledger = session.scalar(
                select(BudgetLedger).where(BudgetLedger.id == "model").with_for_update()
            )
            if ledger is None:
                ledger = BudgetLedger(
                    id="model",
                    currency=self.settings.currency,
                    limit_cost=self.settings.max_cost,
                    accounted_cost=Decimal(0),
                )
                session.add(ledger)
            if ledger.currency != self.settings.currency:
                raise GenerationError(
                    "configuration", "configured currency differs from saved budget"
                )
            ledger.limit_cost = min(ledger.limit_cost, self.settings.max_cost)
            if ledger.accounted_cost + cost > ledger.limit_cost:
                raise GenerationError("budget_exceeded", "persistent model budget exhausted")
            ledger.accounted_cost += cost
            call_id = entity_id("call")
            session.add(
                ModelCall(
                    id=call_id,
                    run_id=claim.run_id,
                    attempt=claim.attempt,
                    step=step,
                    provider="deepseek",
                    model=self.settings.model,
                    prompt_version=prompt_version or self.prompts["version"],
                    schema_version=schema_version
                    or OUTPUTS[claim.operation].model_json_schema()["properties"]["schema_version"][
                        "const"
                    ],
                    status="started",
                    request=sanitized(body, (self.settings.api_key,)),
                    response=None,
                    input_tokens=None,
                    output_tokens=None,
                    reserved_cost=cost,
                    accounted_cost=None,
                    currency=self.settings.currency,
                    latency_seconds=None,
                    error_code=None,
                    outcome_unknown=True,
                )
            )
            return call_id

    def record_call(
        self, call_id: str, result: ProviderResult, code: ErrorCode | None, elapsed: float
    ) -> None:
        with self.database.transaction() as session:
            ledger = session.scalar(
                select(BudgetLedger).where(BudgetLedger.id == "model").with_for_update()
            )
            call = session.get(ModelCall, call_id)
            assert ledger is not None and call is not None
            if result.input_tokens is not None and result.output_tokens is not None:
                cost = (
                    Decimal(result.input_tokens) * self.settings.input_rate
                    + Decimal(result.output_tokens) * self.settings.output_rate
                ) / Decimal(1_000_000)
                ledger.accounted_cost += cost - call.reserved_cost
                call.accounted_cost = cost
            call.status = "failed" if code else "completed"
            call.response = sanitized(asdict(result), (self.settings.api_key,))
            call.input_tokens, call.output_tokens = result.input_tokens, result.output_tokens
            call.latency_seconds = Decimal(str(elapsed))
            call.error_code = code
            call.outcome_unknown = result.outcome_unknown

    def generate(self, claim: Claim) -> dict[str, Any]:
        repair = None
        previous_candidate: object = None
        last_code: ErrorCode = "domain_validation"
        provider = ResponsesProvider(self.settings)
        try:
            for step in range(1, self.settings.max_repairs + 2):
                body = self.build_body(claim, repair, previous_candidate)
                call_id = self.reserve_call(claim, step, body)
                started = time.monotonic()
                result = provider.generate(body)
                raw = None
                code = result.error
                if code is None:
                    try:
                        raw = sanitized(
                            json.loads(result.output_text or ""), (self.settings.api_key,)
                        )
                    except (ValueError, RecursionError):
                        code = "invalid_json"
                        repair = "输出必须是完整JSON对象，不含代码围栏。"
                    else:
                        try:
                            proposal = validate_pair(claim.operation, claim.input, raw)
                            validate_candidate_context(proposal, claim.context)
                            validate_http_json_examples(proposal)
                        except ValidationError as exc:
                            code = "schema_validation"
                            repair = json.dumps(
                                exc.errors(
                                    include_input=False, include_url=False, include_context=False
                                ),
                                ensure_ascii=False,
                            )
                        except ValueError as exc:
                            code = "domain_validation"
                            repair = str(exc)
                self.record_call(call_id, result, code, time.monotonic() - started)
                if code is None:
                    assert isinstance(raw, dict)
                    return raw
                last_code = code
                previous_candidate = raw if raw is not None else result.output_text
                if result.error is not None:
                    raise GenerationError(
                        code, result.detail or code, outcome_unknown=result.outcome_unknown
                    )
            raise GenerationError(last_code, "candidate invalid after bounded repair")
        finally:
            provider.close()
