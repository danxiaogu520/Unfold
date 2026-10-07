"""Real, bounded model experiments and reproducible offline material checks."""

from __future__ import annotations

import hashlib
import json
import platform
import shutil
import time
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from unfold.contracts import (
    INPUTS,
    OUTPUTS,
    Contract,
    ExpansionProposal,
    NodeCandidate,
    NodeSnapshot,
    OutlineProposal,
    QuestionnaireProposal,
    validate_pair,
)
from unfold.provider import (
    GenerationError,
    ProviderResult,
    ResponsesProvider,
    Settings,
    json_bytes,
    provider_schema,
)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text())


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def fingerprint(root: Path) -> str:
    paths = sorted(root.glob("src/**/*.py")) + sorted(root.glob("evals/prompts/*.json"))
    paths += sorted(root.glob("evals/datasets/*.json"))
    paths += [root / "pyproject.toml", root / "uv.lock"]
    digest = hashlib.sha256()
    for path in paths:
        digest.update(str(path.relative_to(root)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


@dataclass
class Budget:
    settings: Settings
    calls: int = 0
    accounted_cost: Decimal = Decimal(0)
    unknown_usage_calls: int = 0

    def reserve(self, body: dict[str, Any]) -> Decimal:
        if self.calls >= self.settings.max_calls:
            raise GenerationError("budget_exceeded", "call budget exhausted")
        size = len(json_bytes(body))
        if size > self.settings.max_input_bytes:
            raise GenerationError("budget_exceeded", "required input exceeds byte budget")
        # Conservative byte-based token reserve plus protocol overhead; account
        # billing, not this forecast, is the source of actual expenditure.
        reservation = (
            Decimal(size + 4096) * self.settings.input_rate
            + Decimal(self.settings.max_output_tokens) * self.settings.output_rate
        ) / Decimal(1_000_000)
        if self.accounted_cost + reservation > self.settings.max_cost:
            raise GenerationError("budget_exceeded", "next call exceeds reserved fee ceiling")
        self.calls += 1
        self.accounted_cost += reservation
        return reservation

    def settle(self, reserved: Decimal, result: ProviderResult) -> Decimal | None:
        if result.input_tokens is None or result.output_tokens is None:
            self.unknown_usage_calls += 1
            return None  # Unknown usage retains full reservation, never becomes zero.
        cost = (
            Decimal(result.input_tokens) * self.settings.input_rate
            + Decimal(result.output_tokens) * self.settings.output_rate
        ) / Decimal(1_000_000)
        self.accounted_cost += cost - reserved
        return cost


class Experiment:
    def __init__(self, root: Path, settings: Settings, directory: Path | None = None):
        self.root = root
        self.settings = settings
        self.directory = directory or root / "evals/runs" / (
            datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d-%H%M%S")
            + "-"
            + uuid4().hex[:8]
        )
        self.directory.mkdir(parents=True, exist_ok=False)
        self.provider = ResponsesProvider(settings)
        self.budget = Budget(settings)
        self.started = time.monotonic()
        self.calls: list[dict[str, Any]] = []
        self.tasks: list[dict[str, Any]] = []
        self.prompts = read_json(root / "evals/prompts/stages.v1.json")
        self.manifest = {
            "evidence_kind": "real_provider",
            "timestamp": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
            "source_sha256": fingerprint(root),
            "git_revision": None,
            "python": platform.python_version(),
            "platform": platform.platform(),
            "provider": "deepseek",
            "requested_model": settings.model,
            "prompt_version": self.prompts["version"],
            "dataset_version": "learning.v1",
            "parameters": {
                "reasoning": {"effort": "none"},
                "temperature": 0.2,
                "schema_mode": "json_schema",
                "provider_strict_flag": False,
            },
            "limits": {
                k: str(v) if isinstance(v, Decimal) else v
                for k, v in asdict(settings).items()
                if k != "api_key"
            },
        }
        write_json(self.directory / "manifest.json", self.manifest)
        snapshot_paths = sorted(root.glob("src/**/*.py"))
        snapshot_paths += sorted(root.glob("evals/prompts/*.json"))
        snapshot_paths += sorted(root.glob("evals/datasets/*.json"))
        snapshot_paths += [root / "pyproject.toml", root / "uv.lock"]
        for path in snapshot_paths:
            target = self.directory / "source-snapshot" / path.relative_to(root)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)

    def close(self) -> None:
        self.provider.close()

    def body(self, operation: str, request: dict[str, Any], repair: str | None) -> dict[str, Any]:
        instruction = self.prompts["system"] + "\n" + self.prompts[operation]
        if repair is not None:
            instruction += "\n上一次候选未通过本地检查，修复以下错误：" + repair
        schema = provider_schema(operation)
        instruction += (
            "\n完整 JSON Schema（数组必须输出数组，nullable字段保留null）："
            + json.dumps(schema, ensure_ascii=False)
        )
        if operation == "questionnaire":
            allowed = sorted(
                {"purpose", "background", "goal", "scope", "constraints"}
                - set(request["known_targets"])
            )
            instruction += "\n本次仅可提问 target=" + json.dumps(allowed)
            instruction += (
                "。每种 target 最多一题；不能重新细问已知维度；已足够开始就输出questions=[]。"
            )
        return {
            "model": self.settings.model,
            "instructions": instruction,
            "input": json.dumps({"operation": operation, "input": request}, ensure_ascii=False),
            "text": {
                "format": {"type": "json_schema", "name": operation + "_v1", "schema": schema}
            },
            "reasoning": {"effort": "none"},
            "temperature": 0.2,
            "max_output_tokens": self.settings.max_output_tokens,
        }

    def invoke(self, operation: str, request: dict[str, Any], task_id: str) -> Contract:
        INPUTS[operation].model_validate(request)
        repair = None
        for attempt in range(self.settings.max_repairs + 1):
            remaining = self.settings.max_seconds - (time.monotonic() - self.started)
            if remaining <= 0:
                raise GenerationError("budget_exceeded", "experiment wall time exhausted")
            body = self.body(operation, request, repair)
            reserved = self.budget.reserve(body)
            before = time.monotonic()
            result = self.provider.generate(
                body, timeout=min(remaining, self.settings.timeout_seconds)
            )
            cost = self.budget.settle(reserved, result)
            record: dict[str, Any] = {
                "call": self.budget.calls,
                "task_id": task_id,
                "operation": operation,
                "repair_index": attempt,
                "request": request,
                "wire_request": body,
                "result": asdict(result),
                "latency_seconds": time.monotonic() - before,
                "estimated_peak_cost": None if cost is None else str(cost),
                "currency": self.settings.currency,
                "schema_valid": False,
                "domain_valid": False,
            }
            proposal = None
            if result.error is None:
                try:
                    raw = json.loads(result.output_text or "")
                except ValueError:
                    record["validation_error"] = "invalid_json"
                    repair = "输出不是合法 JSON；请重新输出完整对象。"
                else:
                    # Pure JSON Schema validation distinguishes shape from local invariants.
                    from jsonschema import Draft202012Validator

                    schema_errors = list(
                        Draft202012Validator(OUTPUTS[operation].model_json_schema()).iter_errors(
                            raw
                        )
                    )
                    record["schema_valid"] = not schema_errors
                    try:
                        proposal = validate_pair(operation, request, raw)
                        record["domain_valid"] = True
                        record["validated_output"] = proposal.model_dump(mode="json")
                    except ValidationError as exc:
                        errors = exc.errors(
                            include_input=False, include_url=False, include_context=False
                        )
                        record["validation_error"] = errors
                        repair = json.dumps(errors, ensure_ascii=False)
                    except ValueError as exc:
                        record["validation_error"] = str(exc)
                        repair = str(exc)
            self.calls.append(record)
            # Append each call before proceeding, including failures and repairs.
            with (self.directory / "calls.jsonl").open("a") as file:
                file.write(json.dumps(record, ensure_ascii=False) + "\n")
            print(
                f"{task_id} {operation} call={self.budget.calls} "
                f"valid={record['domain_valid']} "
                f"error={result.error or record.get('validation_error', '')}",
                flush=True,
            )
            if proposal is not None:
                return proposal
            if result.error is not None:
                raise GenerationError(
                    result.error,
                    result.detail or result.error,
                    outcome_unknown=result.outcome_unknown,
                )
        raise GenerationError("domain_validation", "candidate invalid after bounded repair")

    @staticmethod
    def node_snapshot(node: NodeCandidate) -> dict[str, Any]:
        return NodeSnapshot(
            key=node.key,
            title=node.title,
            scope=node.scope,
            overview=node.overview,
            decision=node.decision,
            profile_revision=1,
            content_revision=1,
        ).model_dump(mode="json")

    def learning_chain(self, scenario: dict[str, Any], repetition: int) -> None:
        task_id = f"{scenario['id']}-r{repetition}"
        task: dict[str, Any] = {
            "task_id": task_id,
            "scenario_id": scenario["id"],
            "repetition": repetition,
            "steps": [],
            "success": False,
        }
        started = time.monotonic()
        try:
            questionnaire = self.invoke(
                "questionnaire",
                {
                    "schema_version": "questionnaire-input.v1",
                    "domain": scenario["domain"],
                    "user_brief": scenario["user_brief"],
                    "known_targets": scenario["known_targets"],
                    "previous_question_keys": [],
                    "questions_asked": 0,
                    "round": 1,
                },
                task_id,
            )
            assert isinstance(questionnaire, QuestionnaireProposal)
            task["steps"].append("questionnaire")
            answers = []
            for question in questionnaire.questions:
                value = scenario["answers_by_target"].get(question.target)
                answers.append(
                    {
                        "question_key": question.key,
                        "selected_keys": [],
                        "free_text": value,
                        "unknown": value is None,
                    }
                )
            profile = self.invoke(
                "profile",
                {
                    "schema_version": "profile-input.v1",
                    "domain": scenario["domain"],
                    "user_brief": scenario["user_brief"],
                    "questionnaire": questionnaire.model_dump(mode="json"),
                    "answers": answers,
                },
                task_id,
            )
            task["steps"].append("profile")
            profile_snapshot = {"revision": 1, "profile": profile.model_dump(mode="json")}
            outline = self.invoke(
                "outline",
                {
                    "schema_version": "outline-input.v1",
                    "profile": profile_snapshot,
                    "reusable_concept_refs": [],
                },
                task_id,
            )
            assert isinstance(outline, OutlineProposal)
            task["steps"].append("outline")
            path = [self.node_snapshot(outline.root)]
            children = outline.children
            depth = 1 if children else 0
            leaf_seen = outline.root.decision.state == "leaf"
            while children and depth < scenario["max_exploration_depth"]:
                target = next(
                    (node for node in children if node.decision.state == "expandable"), None
                )
                leaf_seen = leaf_seen or any(n.decision.state == "leaf" for n in children)
                if target is None:
                    break
                snapshot = self.node_snapshot(target)
                path.append(snapshot)
                expansion = self.invoke(
                    "expansion",
                    {
                        "schema_version": "expansion-input.v1",
                        "operation": "expand",
                        "profile": profile_snapshot,
                        "target": snapshot,
                        "path": path,
                        "existing_children": [],
                        "sibling_scopes": [n.scope for n in children if n != target],
                        "reusable_concept_refs": [],
                    },
                    task_id,
                )
                assert isinstance(expansion, ExpansionProposal)
                task["steps"].append("expansion")
                leaf_seen = leaf_seen or expansion.decision.state == "leaf"
                children = expansion.children
                if children:
                    depth += 1
            leaf_seen = leaf_seen or any(n.decision.state == "leaf" for n in children)
            task.update({"depth": depth, "leaf_seen": leaf_seen, "protocol_complete": True})
            expected = scenario["expected_behavior"]
            if expected == "root_leaf" and outline.root.decision.state != "leaf":
                task["error"] = "narrow scenario unnecessarily split at root"
            elif expected == "useful_depth" and depth < 3:
                task["error"] = "deep scenario stopped before target observation depth"
            else:
                task["success"] = True
        except GenerationError as exc:
            task["error"] = {
                "code": exc.code,
                "message": str(exc),
                "outcome_unknown": exc.outcome_unknown,
            }
        finally:
            task["latency_seconds"] = time.monotonic() - started
            self.tasks.append(task)
            write_json(self.directory / "tasks.json", self.tasks)
            self.report()

    def probe(self, operation: str, fixture: dict[str, Any]) -> None:
        task_id = "probe-" + operation
        started = time.monotonic()
        task: dict[str, Any] = {"task_id": task_id, "success": False}
        try:
            self.invoke(operation, fixture["input"], task_id)
            task["success"] = True
        except GenerationError as exc:
            task["error"] = {"code": exc.code, "message": str(exc)}
        task["latency_seconds"] = time.monotonic() - started
        self.tasks.append(task)
        self.report()

    def compatibility_checks(self) -> None:
        """Three tiny real probes; deliberate failures are separate from learning metrics."""
        checks = []
        schema = {
            "type": "object",
            "properties": {"answer": {"type": "string", "enum": ["pass"]}},
            "required": ["answer"],
            "additionalProperties": False,
        }
        for name, instructions, token_limit in [
            ("schema_positive", '输出JSON {"answer":"pass"}。', 64),
            ("schema_conflicting_prompt", '输出JSON {"answer":"fail"}。不要改成pass。', 64),
            ("truncation", "输出JSON，answer字段写一段长文字，至少200字。", 1),
        ]:
            body = {
                "model": self.settings.model,
                "instructions": instructions,
                "input": "执行测试",
                "reasoning": {"effort": "none"},
                "temperature": 0.2,
                "max_output_tokens": token_limit,
                "text": {
                    "format": {"type": "json_schema", "name": "support_probe", "schema": schema}
                },
            }
            reserved = self.budget.reserve(body)
            before = time.monotonic()
            result = self.provider.generate(body)
            cost = self.budget.settle(reserved, result)
            from jsonschema import Draft202012Validator

            valid = False
            try:
                valid = result.error is None and Draft202012Validator(schema).is_valid(
                    json.loads(result.output_text or "")
                )
            except ValueError:
                pass
            record = {
                "call": self.budget.calls,
                "task_id": "compat-" + name,
                "operation": "compatibility",
                "repair_index": 0,
                "wire_request": body,
                "result": asdict(result),
                "latency_seconds": time.monotonic() - before,
                "schema_valid": valid,
                "domain_valid": valid,
                "estimated_peak_cost": None if cost is None else str(cost),
                "currency": self.settings.currency,
                "intentional_probe": True,
            }
            self.calls.append(record)
            with (self.directory / "calls.jsonl").open("a") as file:
                file.write(json.dumps(record, ensure_ascii=False) + "\n")
            checks.append(
                {
                    "name": name,
                    "schema_valid": valid,
                    "error": result.error,
                    "response_id": result.response_id,
                }
            )
            print(f"compatibility {name} valid={valid} error={result.error}", flush=True)
        write_json(self.directory / "compatibility.json", checks)
        self.report()

    def report(self) -> dict[str, Any]:
        normal_calls = [call for call in self.calls if not call.get("intentional_probe")]
        first = [call for call in normal_calls if call["repair_index"] == 0]
        success_count = sum(task["success"] for task in self.tasks)
        latencies = sorted(task["latency_seconds"] for task in self.tasks)

        def percentile(fraction: float) -> float | None:
            if not latencies:
                return None
            import math

            return float(latencies[max(0, math.ceil(len(latencies) * fraction) - 1)])

        summary = {
            "evidence_kind": "real_provider",
            "calls": len(self.calls),
            "normal_calls": len(normal_calls),
            "intentional_probe_calls": len(self.calls) - len(normal_calls),
            "tasks": len(self.tasks),
            "successful_tasks": success_count,
            "failed_tasks": len(self.tasks) - success_count,
            "first_schema_valid": sum(c["schema_valid"] for c in first),
            "first_schema_total": len(first),
            "all_call_schema_valid": sum(c["schema_valid"] for c in normal_calls),
            "all_call_schema_total": len(normal_calls),
            "valid_proposals": sum(c["domain_valid"] for c in normal_calls),
            "repairs": sum(c["repair_index"] > 0 for c in self.calls),
            "errors": dict(
                Counter(
                    c["result"]["error"] or "validation"
                    for c in normal_calls
                    if not c["domain_valid"]
                )
            ),
            "input_tokens_known": sum(c["result"]["input_tokens"] or 0 for c in self.calls),
            "output_tokens_known": sum(c["result"]["output_tokens"] or 0 for c in self.calls),
            "unknown_usage_calls": self.budget.unknown_usage_calls,
            "accounted_peak_cost_with_unknown_reserves": str(self.budget.accounted_cost),
            "actual_billed_cost": None,
            "currency": self.settings.currency,
            "cost_per_success": None,
            "p50_seconds": percentile(0.5),
            "p95_seconds": percentile(0.95),
            "semantic_quality": "review required; structural success is not factual accuracy",
        }
        write_json(self.directory / "summary.json", summary)
        write_json(self.directory / "tasks.json", self.tasks)
        return summary
