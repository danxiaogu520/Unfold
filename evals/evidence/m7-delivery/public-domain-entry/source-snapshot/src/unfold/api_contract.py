"""Shared run contracts; HTTP handlers and persistence are in unfold.app."""

from __future__ import annotations

from typing import Annotated, Any, Literal, Self

from pydantic import Field, model_validator

from unfold.contracts import (
    INPUTS,
    OUTPUTS,
    ChoiceMode,
    Contract,
    DomainProfileInput,
    DomainQuestionnaireInput,
    Exercise,
    GuidedExerciseInput,
    Key,
    NodeContext,
    Operation,
    Option,
    ProfileHistoryInput,
    Revision,
    Text,
    parse_input,
)
from unfold.provider import ErrorCode


class CreateRun(Contract):
    schema_version: Literal["api.v1"]
    space_id: Key
    operation: Operation
    profile_revision: Revision | None
    base_revision: Revision | None
    input: dict[str, Any]

    @model_validator(mode="after")
    def validate_input(self) -> Self:
        request = parse_input(self.operation, self.input)
        snapshot = getattr(request, "profile", None)
        if snapshot is not None and self.profile_revision != snapshot.revision:
            raise ValueError("envelope profile revision must match input")
        if self.operation in ("questionnaire", "profile"):
            if self.profile_revision is not None or self.base_revision is not None:
                raise ValueError("intake operation has no existing content/profile revision")
        if isinstance(request, NodeContext):
            if self.base_revision != request.target.content_revision:
                raise ValueError("envelope base revision must match target")
        return self


class ApiError(Contract):
    schema_version: Literal["error.v1"]
    code: ErrorCode
    message: Text
    retryable: bool
    outcome_unknown: bool
    run_id: Key | None


class SnapshotFields(Contract):
    schema_version: Literal["run.v1", "run.v2", "run.v3"]
    run_id: Key
    operation: Operation
    status: Literal["queued", "running", "succeeded", "failed", "cancelled", "conflicted"]
    attempt: Annotated[int, Field(ge=0)]
    last_event_seq: Annotated[int, Field(ge=0)]
    result_ref: Key | None
    error: ApiError | None

    @model_validator(mode="after")
    def validate_state(self) -> Self:
        if (self.status == "succeeded") != (self.result_ref is not None):
            raise ValueError("only success has a committed result reference")
        if (self.status in ("failed", "conflicted")) != (self.error is not None):
            raise ValueError(
                "failed/conflicted runs require an error, other states cannot have one"
            )
        return self


class RunSnapshot(SnapshotFields):
    schema_version: Literal["run.v1"]


class RuntimeRunSnapshot(SnapshotFields):
    schema_version: Literal["run.v2"]
    result_kind: Literal["knowledge", "structure_preview"] | None

    @model_validator(mode="after")
    def validate_result_kind(self) -> Self:
        if (self.status == "succeeded") != (self.result_kind is not None):
            raise ValueError("only success has an explicit result kind")
        return self


class RunEvent(Contract):
    schema_version: Literal["event.v1"]
    run_id: Key
    sequence: Revision
    kind: Literal["queued", "started", "progress", "completed", "failed", "cancelled", "conflicted"]
    snapshot: RunSnapshot

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        if self.run_id != self.snapshot.run_id or self.sequence != self.snapshot.last_event_seq:
            raise ValueError("event must match its run snapshot")
        return self


class LearningRunSnapshot(SnapshotFields):
    schema_version: Literal["run.v3"]
    result_kind: Literal["knowledge", "structure_preview", "exercise_set", "resource"] | None

    @model_validator(mode="after")
    def validate_result_kind(self) -> Self:
        if (self.status == "succeeded") != (self.result_kind is not None):
            raise ValueError("only success has an explicit result kind")
        return self


class LearningRunEvent(Contract):
    schema_version: Literal["event.v3"]
    run_id: Key
    sequence: Revision
    kind: Literal["queued", "started", "progress", "completed", "failed", "cancelled", "conflicted"]
    snapshot: LearningRunSnapshot

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        if self.run_id != self.snapshot.run_id or self.sequence != self.snapshot.last_event_seq:
            raise ValueError("event must match its run snapshot")
        return self


class ExerciseView(Contract):
    """Public question projection, deliberately excluding answer and explanation."""

    schema_version: Literal["exercise-view.v1"]
    exercise_revision_ref: Key
    key: Key
    mode: ChoiceMode
    stem: Text
    testing_point: Text
    content_refs: list[Key]
    options: list[Option]


class RuntimeRunEvent(Contract):
    schema_version: Literal["event.v2"]
    run_id: Key
    sequence: Revision
    kind: Literal["queued", "started", "progress", "completed", "failed", "cancelled", "conflicted"]
    snapshot: RuntimeRunSnapshot

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        if self.run_id != self.snapshot.run_id or self.sequence != self.snapshot.last_event_seq:
            raise ValueError("event must match its run snapshot")
        return self


def public_exercise(exercise: Exercise, revision_ref: str) -> ExerciseView:
    return ExerciseView(
        schema_version="exercise-view.v1",
        exercise_revision_ref=revision_ref,
        **exercise.model_dump(exclude={"correct_keys", "explanation"}),
    )


API_MODELS: dict[str, type[Contract]] = {
    "create-run": CreateRun,
    "api-error": ApiError,
    "run-snapshot": RunSnapshot,
    "run-event": RunEvent,
    "exercise-view": ExerciseView,
    "run-snapshot.v2": RuntimeRunSnapshot,
    "run-event.v2": RuntimeRunEvent,
    "run-snapshot.v3": LearningRunSnapshot,
    "run-event.v3": LearningRunEvent,
}


def openapi_document() -> dict[str, Any]:
    components: dict[str, Any] = {}

    def rewrite(value: Any) -> Any:
        if isinstance(value, str):
            return value.replace("#/$defs/", "#/components/schemas/")
        if isinstance(value, dict):
            return {k: rewrite(v) for k, v in value.items()}
        if isinstance(value, list):
            return [rewrite(v) for v in value]
        return value

    models: list[type[Contract]] = [
        *INPUTS.values(),
        ProfileHistoryInput,
        DomainProfileInput,
        DomainQuestionnaireInput,
        GuidedExerciseInput,
        *OUTPUTS.values(),
        *API_MODELS.values(),
    ]
    for cls in models:
        schema = cls.model_json_schema()
        components.update(rewrite(schema.pop("$defs", {})))
        components[cls.__name__] = rewrite(schema)
    # Strong operation/input pairing in OpenAPI; runtime uses the same INPUTS registry.
    components["CreateRun"]["oneOf"] = [
        {
            "properties": {
                "operation": {"const": operation},
                "input": {"$ref": f"#/components/schemas/{cls.__name__}"},
            }
        }
        for operation, cls in INPUTS.items()
    ]
    components["CreateRun"]["oneOf"].append(
        {
            "properties": {
                "operation": {"const": "profile"},
                "input": {"$ref": "#/components/schemas/ProfileHistoryInput"},
            }
        }
    )
    for operation, cls in (
        ("questionnaire", DomainQuestionnaireInput),
        ("profile", DomainProfileInput),
    ):
        components["CreateRun"]["oneOf"].append(
            {
                "properties": {
                    "operation": {"const": operation},
                    "input": {"$ref": "#/components/schemas/" + cls.__name__},
                }
            }
        )
    components["CreateRun"]["oneOf"].append(
        {
            "properties": {
                "operation": {"const": "exercises"},
                "input": {"$ref": "#/components/schemas/GuidedExerciseInput"},
            }
        }
    )

    def response(name: str, description: str) -> dict[str, Any]:
        return {
            "description": description,
            "content": {
                "application/json": {
                    "schema": {"$ref": f"#/components/schemas/{name}"},
                }
            },
        }

    run_parameter = {"name": "run_id", "in": "path", "required": True, "schema": {"type": "string"}}
    return {
        "openapi": "3.1.0",
        "info": {"title": "Unfold shared task API", "version": "api.v1"},
        "servers": [{"url": "http://127.0.0.1:8000"}],
        "paths": {
            "/api/v1/runs": {
                "post": {
                    "operationId": "createRun",
                    "parameters": [
                        {
                            "name": "Idempotency-Key",
                            "in": "header",
                            "required": True,
                            "schema": {"type": "string", "minLength": 1, "maxLength": 128},
                        }
                    ],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/CreateRun"}
                            }
                        },
                    },
                    "responses": {
                        "202": response("LearningRunSnapshot", "New or reused run"),
                        "409": response("ApiError", "Identity/version conflict"),
                        "422": response("ApiError", "Invalid request"),
                        "429": response("ApiError", "Queue or budget limit"),
                    },
                }
            },
            "/api/v1/runs/{run_id}": {
                "get": {
                    "operationId": "getRun",
                    "parameters": [run_parameter],
                    "responses": {
                        "200": response("LearningRunSnapshot", "Durable snapshot"),
                        "404": response("ApiError", "Unknown run"),
                    },
                }
            },
            "/api/v1/runs/{run_id}/cancel": {
                "post": {
                    "operationId": "cancelRun",
                    "parameters": [run_parameter],
                    "responses": {
                        "200": response("LearningRunSnapshot", "Current terminal/cancelled state"),
                        "404": response("ApiError", "Unknown run"),
                    },
                }
            },
            "/api/v1/runs/{run_id}/events": {
                "get": {
                    "operationId": "subscribeRun",
                    "parameters": [
                        run_parameter,
                        {
                            "name": "Last-Event-ID",
                            "in": "header",
                            "required": False,
                            "schema": {"type": "integer", "minimum": 0},
                        },
                    ],
                    "responses": {
                        "200": {
                            "description": "SSE id=sequence; data=LearningRunEvent JSON",
                            "content": {"text/event-stream": {"schema": {"type": "string"}}},
                        },
                        "410": response("ApiError", "Replay expired: get snapshot"),
                        "404": response("ApiError", "Unknown run"),
                    },
                }
            },
        },
        "components": {"schemas": components},
    }
