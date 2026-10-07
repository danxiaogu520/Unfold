from __future__ import annotations

import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest

from unfold.evaluation import Budget, Experiment
from unfold.provider import (
    GenerationError,
    ProviderResult,
    ResponsesProvider,
    Settings,
    load_env_file,
    parse_response,
)

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = json.loads((ROOT / "evals/contracts/success.v1.json").read_text())


def response(content: str, *, usage: bool = True) -> dict[str, Any]:
    return {
        "id": "response-1",
        "model": "deepseek-flash",
        "status": "completed",
        "output": [{"type": "message", "content": [{"type": "output_text", "text": content}]}],
        "usage": {"input_tokens": 100, "output_tokens": 100} if usage else None,
    }


@pytest.mark.parametrize(
    "reason,code", [("max_output_tokens", "truncated"), ("content_filter", "content_filter")]
)
def test_incomplete_json_is_never_accepted(reason: str, code: str) -> None:
    raw = response('{"partial":')
    raw.update({"status": "incomplete", "incomplete_details": {"reason": reason}})
    result = parse_response(raw)
    assert result.error == code and result.output_text is None


def test_refusal_and_missing_usage_are_explicit() -> None:
    raw = response("{}", usage=False)
    raw["output"][0]["content"] = [{"type": "refusal", "refusal": "cannot respond"}]
    result = parse_response(raw)
    assert result.error == "refusal"
    assert result.input_tokens is None and result.output_tokens is None


@pytest.mark.parametrize(
    "status,code",
    [(400, "invalid_input"), (401, "authentication"), (429, "rate_limit"), (500, "provider_error")],
)
def test_http_failures_do_not_log_credentials(settings: Settings, status: int, code: str) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": {"message": "test-secret"}})

    provider = ResponsesProvider(settings, httpx.Client(transport=httpx.MockTransport(handle)))
    result = provider.generate({})
    assert result.error == code
    assert "test-secret" not in repr(result)
    assert "test-secret" not in repr(settings)


def test_timeouts_preserve_unknown_outcome(settings: Settings) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("contains-sensitive-details", request=request)

    provider = ResponsesProvider(settings, httpx.Client(transport=httpx.MockTransport(handle)))
    result = provider.generate({})
    assert result.error == "timeout" and result.outcome_unknown
    assert "sensitive" not in repr(result)


def test_unsupported_schema_is_not_silently_downgraded(settings: Settings) -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                400, json={"error": {"code": "invalid_json_schema", "param": "text.format.schema"}}
            )
        )
    )
    assert ResponsesProvider(settings, client).generate({}).error == "schema_unsupported"


def test_cost_reservation_and_call_limits(settings: Settings) -> None:
    budget = Budget(replace(settings, max_calls=1))
    reserved = budget.reserve({"input": "a"})
    assert budget.accounted_cost == reserved
    unknown = ProviderResult(None, None, None, None, None, "timeout", "timeout", True)
    assert budget.settle(reserved, unknown) is None
    assert budget.accounted_cost == reserved and budget.unknown_usage_calls == 1
    with pytest.raises(GenerationError, match="call budget"):
        budget.reserve({})
    tiny = Budget(replace(settings, max_cost=Decimal("0.000001")))
    with pytest.raises(GenerationError, match="fee ceiling"):
        tiny.reserve({})


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-1", "0"])
def test_invalid_budget_rejected(settings: Settings, value: str) -> None:
    with pytest.raises(GenerationError):
        replace(settings, max_cost=Decimal(value))


def test_env_file_is_literal_and_does_not_override_existing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "existing")
    file = tmp_path / "env"
    file.write_text('DEEPSEEK_API_KEY="replacement"\nUNFOLD_MODEL=$(literal-not-executed)\n')
    load_env_file(file)
    import os

    assert os.environ["DEEPSEEK_API_KEY"] == "existing"
    assert os.environ["UNFOLD_MODEL"] == "$(literal-not-executed)"
    file.write_text("UNSUPPORTED_KEY=anything\n")
    with pytest.raises(GenerationError):
        load_env_file(file)


def test_bounded_repair_keeps_failed_call_and_usage(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = next(row for row in SAMPLES if row["operation"] == "outline")
    results = iter(
        [
            parse_response(response("not json")),
            parse_response(response(json.dumps(fixture["output"]))),
        ]
    )
    monkeypatch.setattr(ResponsesProvider, "generate", lambda *a, **k: next(results))
    experiment = Experiment(ROOT, settings, tmp_path / "run")
    try:
        experiment.invoke("outline", fixture["input"], "test-task")
        assert len(experiment.calls) == 2
        assert not experiment.calls[0]["schema_valid"] and experiment.calls[1]["domain_valid"]
        assert experiment.budget.accounted_cost == Decimal("0.002")
        log = (experiment.directory / "calls.jsonl").read_text()
        assert "not json" in log and "test-secret" not in log
    finally:
        experiment.close()


def test_provider_failure_is_not_repaired(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = SAMPLES[0]
    monkeypatch.setattr(
        ResponsesProvider,
        "generate",
        lambda *a, **k: ProviderResult(None, None, None, 100, 100, "refusal", "refused"),
    )
    experiment = Experiment(ROOT, settings, tmp_path / "run")
    try:
        with pytest.raises(GenerationError, match="refused"):
            experiment.invoke("questionnaire", fixture["input"], "task")
        assert len(experiment.calls) == 1
    finally:
        experiment.close()
