"""Thin Responses API experiment adapter. No automatic network retries."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx

from unfold.contracts import OUTPUTS

ErrorCode = Literal[
    "configuration",
    "invalid_input",
    "invalid_json",
    "schema_validation",
    "domain_validation",
    "refusal",
    "truncated",
    "content_filter",
    "schema_unsupported",
    "authentication",
    "rate_limit",
    "provider_error",
    "timeout",
    "outcome_unknown",
    "capability_limit",
    "budget_exceeded",
    "cancelled",
    "version_conflict",
]


class GenerationError(Exception):
    def __init__(self, code: ErrorCode, message: str, *, outcome_unknown: bool = False):
        super().__init__(message)
        self.code = code
        self.outcome_unknown = outcome_unknown


@dataclass(frozen=True)
class Settings:
    api_key: str = field(repr=False)
    model: str
    base_url: str
    max_cost: Decimal
    input_rate: Decimal
    output_rate: Decimal
    currency: str = "CNY"
    max_calls: int = 48
    max_output_tokens: int = 4000
    max_input_bytes: int = 48000
    timeout_seconds: float = 60.0
    max_seconds: float = 1200.0
    max_repairs: int = 1

    def __post_init__(self) -> None:
        if not self.api_key.strip() or not self.model.strip():
            raise GenerationError("configuration", "missing API key or model")
        url = urlsplit(self.base_url)
        if url.scheme != "https" or not url.hostname or url.username or url.password:
            raise GenerationError("configuration", "base URL must be HTTPS without credentials")
        if url.query or url.fragment:
            raise GenerationError("configuration", "base URL cannot have query or fragment")
        for value in (self.max_cost, self.input_rate, self.output_rate):
            if not value.is_finite() or value <= 0:
                raise GenerationError(
                    "configuration", "budget and prices must be finite and positive"
                )
        if min(self.max_calls, self.max_output_tokens, self.max_input_bytes) < 1:
            raise GenerationError("configuration", "call/token/byte limits must be positive")
        if self.timeout_seconds <= 0 or self.max_seconds <= 0 or self.max_repairs not in (0, 1):
            raise GenerationError("configuration", "invalid time or repair limit")

    @classmethod
    def from_env(cls) -> Settings:
        required = [
            "DEEPSEEK_API_KEY",
            "UNFOLD_MODEL",
            "UNFOLD_MAX_COST",
            "UNFOLD_INPUT_COST_PER_MILLION",
            "UNFOLD_OUTPUT_COST_PER_MILLION",
        ]
        missing = [key for key in required if not os.environ.get(key, "").strip()]
        if missing:
            raise GenerationError("configuration", "missing configuration: " + ", ".join(missing))
        try:
            return cls(
                api_key=os.environ["DEEPSEEK_API_KEY"],
                model=os.environ["UNFOLD_MODEL"],
                base_url=os.environ.get("UNFOLD_BASE_URL", "https://api.deepseek.com"),
                max_cost=Decimal(os.environ["UNFOLD_MAX_COST"]),
                input_rate=Decimal(os.environ["UNFOLD_INPUT_COST_PER_MILLION"]),
                output_rate=Decimal(os.environ["UNFOLD_OUTPUT_COST_PER_MILLION"]),
                currency=os.environ.get("UNFOLD_CURRENCY", "CNY"),
            )
        except InvalidOperation as exc:
            raise GenerationError("configuration", "budget/prices must be decimal numbers") from exc


def load_env_file(path: Path) -> None:
    """Literal KEY=value only. Existing environment wins; never evaluate shell code."""
    allowed = {
        "DEEPSEEK_API_KEY",
        "UNFOLD_MODEL",
        "UNFOLD_BASE_URL",
        "UNFOLD_MAX_COST",
        "UNFOLD_INPUT_COST_PER_MILLION",
        "UNFOLD_OUTPUT_COST_PER_MILLION",
        "UNFOLD_CURRENCY",
    }
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or key not in allowed:
            raise GenerationError("configuration", "env file contains unsupported setting")
        value = value.strip()
        if value[:1] in ('"', "'"):
            if len(value) < 2 or value[-1] != value[0]:
                raise GenerationError("configuration", "unclosed quote in env file")
            value = value[1:-1]
        os.environ.setdefault(key, value)


def provider_schema(operation: str) -> dict[str, Any]:
    """Use a conservative schema subset; domain/size rules stay enforced locally.

    Keep unions, enum, refs, nullability and required fields. Removing bounds from
    the provider schema is explicit and does not weaken the canonical validator.
    """
    schema = OUTPUTS[operation].model_json_schema()
    omit = {"title", "default", "minLength", "maxLength", "pattern", "minItems", "maxItems"}

    def normalize(value: Any) -> Any:
        if isinstance(value, dict):
            result = {k: normalize(v) for k, v in value.items() if k not in omit}
            if "const" in result:
                result["enum"] = [result.pop("const")]
            return result
        if isinstance(value, list):
            return [normalize(v) for v in value]
        return value

    result: dict[str, Any] = normalize(schema)
    return result


@dataclass(frozen=True)
class ProviderResult:
    response_id: str | None
    actual_model: str | None
    output_text: str | None
    input_tokens: int | None
    output_tokens: int | None
    error: ErrorCode | None
    detail: str | None
    outcome_unknown: bool = False


def parse_response(raw: dict[str, Any]) -> ProviderResult:
    usage = raw.get("usage") or {}

    def token_value(key: str) -> int | None:
        value = usage.get(key)
        return value if type(value) is int and value >= 0 else None

    base: dict[str, Any] = {
        "response_id": raw.get("id"),
        "actual_model": raw.get("model"),
        "input_tokens": token_value("input_tokens"),
        "output_tokens": token_value("output_tokens"),
    }
    status = raw.get("status")
    if status == "incomplete":
        reason = (raw.get("incomplete_details") or {}).get("reason")
        code: ErrorCode = "truncated" if reason == "max_output_tokens" else "content_filter"
        return ProviderResult(**base, output_text=None, error=code, detail=str(reason))
    if status != "completed":
        return ProviderResult(
            **base, output_text=None, error="provider_error", detail="response not completed"
        )
    texts = []
    for item in raw.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") == "refusal":
                return ProviderResult(**base, output_text=None, error="refusal", detail="refused")
            if content.get("type") == "output_text" and isinstance(content.get("text"), str):
                texts.append(content["text"])
    if not texts:
        return ProviderResult(**base, output_text=None, error="provider_error", detail="no text")
    return ProviderResult(**base, output_text="".join(texts), error=None, detail=None)


class ResponsesProvider:
    def __init__(self, settings: Settings, client: httpx.Client | None = None):
        self.settings = settings
        self.client = client or httpx.Client(
            timeout=settings.timeout_seconds, follow_redirects=False
        )

    def close(self) -> None:
        self.client.close()

    def generate(self, body: dict[str, Any], timeout: float | None = None) -> ProviderResult:
        try:
            response = self.client.post(
                self.settings.base_url.rstrip("/") + "/responses",
                headers={"Authorization": "Bearer " + self.settings.api_key},
                json=body,
                timeout=timeout or self.settings.timeout_seconds,
            )
        except httpx.TimeoutException:
            return ProviderResult(
                None, None, None, None, None, "timeout", "provider timeout", outcome_unknown=True
            )
        except httpx.RequestError:
            return ProviderResult(
                None,
                None,
                None,
                None,
                None,
                "outcome_unknown",
                "network error",
                outcome_unknown=True,
            )
        if response.status_code != 200:
            codes: dict[int, ErrorCode] = {
                400: "invalid_input",
                401: "authentication",
                403: "authentication",
                429: "rate_limit",
            }
            code = codes.get(response.status_code, "provider_error")
            if response.status_code == 400:
                try:
                    error = response.json().get("error", {})
                    if "schema" in str(error.get("param", "")) or error.get("code") in (
                        "invalid_json_schema",
                        "unsupported_response_format",
                    ):
                        code = "schema_unsupported"
                except (ValueError, AttributeError):
                    pass
            # Do not serialize HTTP exception strings, headers, or arbitrary error bodies.
            return ProviderResult(
                None,
                None,
                None,
                None,
                None,
                code,
                f"HTTP {response.status_code}",
                outcome_unknown=response.status_code >= 500,
            )
        try:
            raw = response.json()
            if not isinstance(raw, dict):
                raise ValueError("not object")
            return parse_response(raw)
        except (ValueError, TypeError, AttributeError):
            return ProviderResult(
                None,
                None,
                None,
                None,
                None,
                "provider_error",
                "malformed provider envelope",
                outcome_unknown=True,
            )


def json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
