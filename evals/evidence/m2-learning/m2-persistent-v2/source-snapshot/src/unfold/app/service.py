from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from unfold.api_contract import ApiError, CreateRun, RuntimeRunEvent, RuntimeRunSnapshot
from unfold.app.config import AppSettings
from unfold.app.db import Database
from unfold.app.models import (
    GenerationRun,
    IntakeSession,
    ModelCall,
    ProfileRevision,
    RequestIdentity,
    RunAttempt,
    RunEventRecord,
    Space,
)
from unfold.app.security import sanitized
from unfold.contracts import (
    Contract,
    LearningProfileProposal,
    ProfileHistoryInput,
    ProfileInput,
    QuestionnaireProposal,
    parse_input,
    validate_pair,
)
from unfold.provider import ErrorCode, GenerationError, json_bytes

if TYPE_CHECKING:
    from unfold.app.learning import FeedbackInput, IntakeAction, LearningAction, ProfileEdit


def entity_id(prefix: str) -> str:
    return prefix + "_" + uuid4().hex


def database_clock(session: Session) -> datetime:
    now = session.scalar(select(func.clock_timestamp()))
    assert isinstance(now, datetime)
    return now


class ServiceError(GenerationError):
    def __init__(self, code: ErrorCode, message: str, http_status: int = 422):
        super().__init__(code, message)
        self.http_status = http_status


@dataclass(frozen=True)
class Claim:
    run_id: str
    attempt: int
    token: str
    operation: str
    input: dict[str, Any]
    context: dict[str, Any] = field(default_factory=dict)


class RunService:
    def __init__(self, database: Database, settings: AppSettings):
        self.database = database
        self.settings = settings

    @staticmethod
    def snapshot(run: GenerationRun) -> RuntimeRunSnapshot:
        return RuntimeRunSnapshot.model_validate(
            {
                "schema_version": "run.v2",
                "run_id": run.id,
                "operation": run.operation,
                "status": run.status,
                "attempt": run.attempt,
                "last_event_seq": run.last_event_seq,
                "result_ref": run.result_ref,
                "result_kind": run.result_kind,
                "error": run.error,
            }
        )

    def emit(self, session: Session, run: GenerationRun, kind: str) -> None:
        run.last_event_seq += 1
        run.updated_at = database_clock(session)
        snapshot = self.snapshot(run)
        event = RuntimeRunEvent.model_validate(
            {
                "schema_version": "event.v2",
                "run_id": run.id,
                "sequence": run.last_event_seq,
                "kind": kind,
                "snapshot": snapshot.model_dump(mode="json"),
            }
        )
        session.add(
            RunEventRecord(
                run_id=run.id, sequence=run.last_event_seq, payload=event.model_dump(mode="json")
            )
        )

    @staticmethod
    def require_space(session: Session, space_id: str, *, lock: bool = False) -> Space:
        query = select(Space).where(Space.id == space_id, Space.owner == "local")
        if lock:
            query = query.with_for_update()
        space = session.scalar(query)
        if space is None:
            raise ServiceError("invalid_input", "knowledge space not found", 404)
        return space

    @staticmethod
    def space_view(space: Space) -> dict[str, Any]:
        return {
            "id": space.id,
            "domain": space.domain,
            "user_brief": space.user_brief,
            "profile_revision": space.profile_revision,
            "outline_revision": space.outline_revision,
            "root_id": space.root_id,
            "created_at": space.created_at.isoformat(),
        }

    def create_space(self, domain: str, brief: str) -> dict[str, Any]:
        with self.database.transaction() as session:
            space = Space(
                id=entity_id("space"),
                owner="local",
                domain=domain,
                user_brief=brief,
                profile_revision=0,
                outline_revision=0,
            )
            session.add(space)
            session.flush()
            return self.space_view(space)

    def list_spaces(self) -> list[dict[str, Any]]:
        with self.database.transaction() as session:
            return [
                self.space_view(space)
                for space in session.scalars(
                    select(Space).where(Space.owner == "local").order_by(Space.created_at.desc())
                )
            ]

    def get_space(self, space_id: str) -> dict[str, Any]:
        from unfold.app.learning import Knowledge

        with self.database.transaction() as session:
            space = self.require_space(session, space_id)
            intakes = list(
                session.scalars(
                    select(IntakeSession)
                    .where(
                        IntakeSession.space_id == space.id,
                    )
                    .order_by(IntakeSession.created_at)
                )
            )
            profile = session.scalar(
                select(ProfileRevision).where(
                    ProfileRevision.space_id == space.id,
                    ProfileRevision.revision == space.profile_revision,
                )
            )
            runs = list(
                session.scalars(
                    select(GenerationRun)
                    .where(
                        GenerationRun.space_id == space.id,
                    )
                    .order_by(GenerationRun.created_at.desc())
                    .limit(30)
                )
            )
            return {
                **self.space_view(space),
                "intakes": [
                    {
                        "id": i.id,
                        "questionnaire": i.questionnaire,
                        "answers": i.answers,
                        "questions_asked": i.questions_asked,
                        "round": i.round,
                    }
                    for i in intakes
                ],
                "profile": profile.proposal if profile else None,
                "runs": [self.snapshot(run).model_dump(mode="json") for run in runs],
                "nodes": Knowledge.space_nodes(session, space),
                "relations": Knowledge.space_relations(session, space),
            }

    def check_input(self, session: Session, space: Space, request: CreateRun) -> dict[str, Any]:
        from unfold.app.learning import Knowledge

        if request.operation in ("exercises", "resource"):
            raise ServiceError("capability_limit", "this operation is not available yet", 409)
        raw = request.input
        extra: dict[str, Any] = {}
        if request.operation in ("questionnaire", "profile"):
            if raw["domain"] != space.domain or raw["user_brief"] != space.user_brief:
                raise ServiceError(
                    "version_conflict", "intake source does not match saved space", 409
                )
            if space.profile_revision:
                raise ServiceError("version_conflict", "learning profile already exists", 409)
            if request.operation == "questionnaire":
                sessions = list(
                    session.scalars(
                        select(IntakeSession)
                        .where(IntakeSession.space_id == space.id)
                        .order_by(IntakeSession.created_at)
                    )
                )
                asked = sum(len(item.questionnaire["questions"]) for item in sessions)
                keys = [q["key"] for item in sessions for q in item.questionnaire["questions"]]
                if raw["questions_asked"] != asked or raw["round"] != len(sessions) + 1:
                    raise ServiceError(
                        "version_conflict", "questionnaire budget snapshot changed", 409
                    )
                if set(raw["previous_question_keys"]) != set(keys):
                    raise ServiceError("invalid_input", "previous question references mismatch")
            else:
                intakes = Knowledge.intakes(session, space)
                parsed = parse_input("profile", raw)
                if isinstance(parsed, ProfileHistoryInput):
                    proposed_questions = [
                        item.questionnaire.model_dump(mode="json") for item in parsed.rounds
                    ]
                else:
                    assert isinstance(parsed, ProfileInput)
                    proposed_questions = [parsed.questionnaire.model_dump(mode="json")]
                if proposed_questions != [item.questionnaire for item in intakes]:
                    raise ServiceError(
                        "version_conflict", "profile must use saved questionnaires", 409
                    )
                by_key = {a.question_key: a.model_dump(mode="json") for a in parsed.answers}
                for item in intakes:
                    if item.answers is not None and any(
                        by_key[a["question_key"]] != a for a in item.answers
                    ):
                        raise ServiceError(
                            "version_conflict", "profile must preserve saved answers", 409
                        )
            extra = {
                "intake_digest": Knowledge.intake_digest(session, space),
                "selected_context": {
                    "previous_intakes": [
                        {
                            "id": item.id,
                            "questionnaire": item.questionnaire,
                            "answers": item.answers,
                        }
                        for item in Knowledge.intakes(session, space)
                    ]
                },
            }
        else:
            if request.profile_revision != space.profile_revision:
                raise ServiceError("version_conflict", "learning profile revision changed", 409)
            profile = session.scalar(
                select(ProfileRevision).where(
                    ProfileRevision.space_id == space.id,
                    ProfileRevision.revision == space.profile_revision,
                )
            )
            if profile is None or raw["profile"]["profile"] != profile.proposal:
                raise ServiceError(
                    "version_conflict", "profile snapshot does not match saved revision", 409
                )
            extra = Knowledge.verify_input(session, space, request)
        return {
            "profile_revision": space.profile_revision,
            "outline_revision": space.outline_revision,
            **extra,
        }

    def create_action(self, space_id: str, action: LearningAction, key: str) -> RuntimeRunSnapshot:
        from unfold.app.learning import Knowledge

        digest = hashlib.sha256(
            json_bytes({"space_id": space_id, "action": action.model_dump(mode="json")})
        ).hexdigest()
        with self.database.transaction() as session:
            session.execute(text("SELECT pg_advisory_xact_lock(180001)"))
            space = self.require_space(session, space_id, lock=True)
            identity = session.get(RequestIdentity, (space.id, key))
            if identity:
                if identity.request_digest != digest:
                    raise ServiceError(
                        "version_conflict", "same idempotency key has different input", 409
                    )
                saved = session.get(GenerationRun, identity.run_id)
                assert saved is not None
                return self.snapshot(saved)
            request = Knowledge.build_request(session, space, action)
            return self.enqueue(session, space, request, key, identity_digest=digest)

    def get_node(self, space_id: str, node_id: str) -> dict[str, Any]:
        from unfold.app.learning import Knowledge

        with self.database.transaction() as session:
            return Knowledge.read_node(session, self.require_space(session, space_id), node_id)

    def create_intake_action(
        self, space_id: str, action: IntakeAction, key: str
    ) -> RuntimeRunSnapshot:
        from unfold.app.learning import Knowledge

        digest = hashlib.sha256(
            json_bytes({"space_id": space_id, "intake_action": action.model_dump(mode="json")})
        ).hexdigest()
        with self.database.transaction() as session:
            session.execute(text("SELECT pg_advisory_xact_lock(180001)"))
            space = self.require_space(session, space_id, lock=True)
            identity = session.get(RequestIdentity, (space.id, key))
            if identity:
                if identity.request_digest != digest:
                    raise ServiceError(
                        "version_conflict", "same idempotency key has different input", 409
                    )
                saved = session.get(GenerationRun, identity.run_id)
                assert saved is not None
                return self.snapshot(saved)
            request = Knowledge.build_intake_request(session, space, action)
            return self.enqueue(session, space, request, key, identity_digest=digest)

    def edit_profile(self, space_id: str, edit: ProfileEdit) -> dict[str, Any]:
        from unfold.app.learning import Knowledge

        with self.database.transaction() as session:
            return Knowledge.edit_profile(
                session, self.require_space(session, space_id, lock=True), edit
            )

    def feedback(self, space_id: str, node_id: str, feedback: FeedbackInput) -> dict[str, Any]:
        from unfold.app.learning import Knowledge
        from unfold.app.models import LearningFeedback

        with self.database.transaction() as session:
            space = self.require_space(session, space_id, lock=True)
            node = Knowledge.item(session, space, node_id)
            if (
                node.revision != feedback.expected_base_revision
                or space.profile_revision != feedback.expected_profile_revision
            ):
                raise ServiceError("version_conflict", "feedback uses an obsolete snapshot", 409)
            item = LearningFeedback(
                id=entity_id("feedback"),
                space_id=space.id,
                node_id=node.id,
                profile_revision=space.profile_revision,
                content_revision=node.revision,
                kind=feedback.kind,
                text=str(sanitized(feedback.text)),
            )
            session.add(item)
            return {"id": item.id, "kind": item.kind, "text": item.text}

    def create_run(self, request: CreateRun, idempotency_key: str) -> RuntimeRunSnapshot:
        with self.database.transaction() as session:
            session.execute(text("SELECT pg_advisory_xact_lock(180001)"))
            space = self.require_space(session, request.space_id, lock=True)
            return self.enqueue(session, space, request, idempotency_key)

    def review_structure(self, space_id: str, suggestion_id: str, decision: str) -> dict[str, Any]:
        from unfold.app.learning import Knowledge
        from unfold.app.models import StructureSuggestion

        with self.database.transaction() as session:
            space = self.require_space(session, space_id, lock=True)
            suggestion = session.scalar(
                select(StructureSuggestion)
                .where(
                    StructureSuggestion.space_id == space.id,
                    StructureSuggestion.id == suggestion_id,
                )
                .with_for_update()
            )
            if suggestion is None:
                raise ServiceError("invalid_input", "structure preview not found", 404)
            if decision not in ("apply", "dismiss"):
                raise ServiceError("invalid_input", "unknown structure review action")
            if suggestion.status == "applied":
                if decision == "dismiss":
                    raise ServiceError(
                        "version_conflict", "applied preview cannot be dismissed", 409
                    )
                return {"id": suggestion.id, "status": "applied"}
            if suggestion.status == "dismissed":
                if decision == "apply":
                    raise ServiceError("version_conflict", "dismissed preview cannot publish", 409)
                return {"id": suggestion.id, "status": "dismissed"}
            if decision == "dismiss":
                suggestion.status = "dismissed"
                return {"id": suggestion.id, "status": "dismissed"}
            run = session.get(GenerationRun, suggestion.run_id) if suggestion.run_id else None
            if (
                run is None
                or run.status != "succeeded"
                or run.result_ref != suggestion.id
                or run.space_id != space.id
            ):
                raise ServiceError("version_conflict", "preview has no valid saved generation", 409)
            if (
                space.profile_revision != suggestion.profile_revision
                or not Knowledge.dependencies_current(session, space, run.context_dependencies)
            ):
                raise ServiceError("version_conflict", "preview dependencies changed", 409)
            node = Knowledge.item(session, space, suggestion.node_id)
            if node.revision != suggestion.content_revision:
                raise ServiceError("version_conflict", "preview node revision changed", 409)
            request = CreateRun.model_validate(suggestion.request)
            self.check_input(session, space, request)
            proposal = validate_pair(request.operation, request.input, suggestion.proposal)
            result_ref = Knowledge.publish(
                session, run, proposal, space, approved=True, review_id=suggestion.id
            )
            suggestion.status = "applied"
            return {"id": suggestion.id, "status": "applied", "result_ref": result_ref}

    def enqueue(
        self,
        session: Session,
        space: Space,
        request: CreateRun,
        idempotency_key: str,
        identity_digest: str | None = None,
    ) -> RuntimeRunSnapshot:
        if not 1 <= len(idempotency_key) <= 128:
            raise ServiceError("invalid_input", "Idempotency-Key length must be 1..128")
        request_dump = sanitized(request.model_dump(mode="json"))
        digest = hashlib.sha256(json_bytes(request.model_dump(mode="json"))).hexdigest()
        identity_digest = identity_digest or digest
        identity = session.get(RequestIdentity, (space.id, idempotency_key))
        if identity:
            if identity.request_digest != identity_digest:
                raise ServiceError(
                    "version_conflict", "same idempotency key has different input", 409
                )
            existing = session.get(GenerationRun, identity.run_id)
            assert existing is not None
            return self.snapshot(existing)
        dependencies = self.check_input(session, space, request)
        active_key = hashlib.sha256(
            json_bytes(
                {
                    "operation": request.operation,
                    "profile": request.profile_revision,
                    "base": request.base_revision,
                    "target": request.input.get("target", {}).get("key"),
                }
            )
        ).hexdigest()
        active = session.scalar(
            select(GenerationRun).where(
                GenerationRun.space_id == space.id,
                GenerationRun.active_key == active_key,
                GenerationRun.status.in_(["queued", "running"]),
            )
        )
        if active:
            if active.request_digest == digest:
                session.add(
                    RequestIdentity(
                        space_id=space.id,
                        key=idempotency_key,
                        run_id=active.id,
                        request_digest=identity_digest,
                    )
                )
                return self.snapshot(active)
            raise ServiceError(
                "version_conflict", "another operation is active at this revision", 409
            )
        if (
            session.scalar(
                select(func.count())
                .select_from(GenerationRun)
                .where(GenerationRun.status.in_(["queued", "running"]))
            )
            or 0
        ) >= self.settings.queue_capacity:
            raise ServiceError("budget_exceeded", "generation queue is full", 429)
        run = GenerationRun(
            id=entity_id("run"),
            space_id=space.id,
            operation=request.operation,
            target_id=request.input.get("target", {}).get("key"),
            profile_revision=request.profile_revision,
            base_revision=request.base_revision,
            idempotency_key=idempotency_key,
            request_digest=digest,
            active_key=active_key,
            input_payload=request_dump["input"],
            context_dependencies=dependencies,
            status="queued",
            attempt=0,
            last_event_seq=0,
        )
        session.add(run)
        session.flush()
        session.add(
            RequestIdentity(
                space_id=space.id,
                key=idempotency_key,
                run_id=run.id,
                request_digest=identity_digest,
            )
        )
        self.emit(session, run, "queued")
        return self.snapshot(run)

    def get_run(self, run_id: str) -> RuntimeRunSnapshot:
        with self.database.transaction() as session:
            run = session.get(GenerationRun, run_id)
            if run is None:
                raise ServiceError("invalid_input", "run not found", 404)
            return self.snapshot(run)

    def events(self, run_id: str, after: int = 0) -> list[dict[str, Any]]:
        with self.database.transaction() as session:
            if session.get(GenerationRun, run_id) is None:
                raise ServiceError("invalid_input", "run not found", 404)
            return [
                event.payload
                for event in session.scalars(
                    select(RunEventRecord)
                    .where(RunEventRecord.run_id == run_id, RunEventRecord.sequence > after)
                    .order_by(RunEventRecord.sequence)
                )
            ]

    def claim(self, run_id: str | None = None) -> Claim | None:
        with self.database.transaction() as session:
            session.execute(text("SELECT pg_advisory_xact_lock(180001)"))
            now = database_clock(session)
            active_count = (
                session.scalar(
                    select(func.count())
                    .select_from(GenerationRun)
                    .where(
                        GenerationRun.status == "running",
                        GenerationRun.lease_until > now,
                    )
                )
                or 0
            )
            if active_count >= self.settings.worker_concurrency:
                return None
            query = select(GenerationRun)
            if run_id:
                query = query.where(GenerationRun.id == run_id)
            run = session.scalar(
                query.where(
                    GenerationRun.status == "queued",
                    GenerationRun.not_before <= now,
                )
                .order_by(GenerationRun.created_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if run is None:
                return None
            run.attempt += 1
            run.lease_token = entity_id("lease")
            run.lease_until = now + timedelta(seconds=self.settings.lease_seconds)
            run.status = "running"
            session.add(
                RunAttempt(
                    run_id=run.id,
                    attempt=run.attempt,
                    lease_token=run.lease_token,
                    status="running",
                    outcome_unknown=False,
                )
            )
            self.emit(session, run, "started")
            return Claim(
                run.id,
                run.attempt,
                run.lease_token,
                run.operation,
                run.input_payload,
                run.context_dependencies.get("selected_context", {}),
            )

    def heartbeat(self, claim: Claim) -> bool:
        with self.database.transaction() as session:
            run = session.scalar(
                select(GenerationRun).where(GenerationRun.id == claim.run_id).with_for_update()
            )
            now = database_clock(session)
            if not self.eligible(run, claim, now):
                return False
            assert run is not None
            run.lease_until = now + timedelta(seconds=self.settings.lease_seconds)
            return True

    @staticmethod
    def eligible(run: GenerationRun | None, claim: Claim, now: datetime) -> bool:
        return bool(
            run
            and run.status == "running"
            and run.attempt == claim.attempt
            and run.lease_token == claim.token
            and run.lease_until is not None
            and run.lease_until > now
        )

    def cancel(self, run_id: str) -> RuntimeRunSnapshot:
        with self.database.transaction() as session:
            run = session.scalar(
                select(GenerationRun).where(GenerationRun.id == run_id).with_for_update()
            )
            if run is None:
                raise ServiceError("invalid_input", "run not found", 404)
            if run.status in ("queued", "running"):
                run.status = "cancelled"
                run.lease_until = None
                attempt = session.get(RunAttempt, (run.id, run.attempt)) if run.attempt else None
                if attempt:
                    attempt.status = "cancelled"
                self.emit(session, run, "cancelled")
            return self.snapshot(run)

    def finish_failure(self, claim: Claim, error: GenerationError) -> bool:
        with self.database.transaction() as session:
            run = session.scalar(
                select(GenerationRun).where(GenerationRun.id == claim.run_id).with_for_update()
            )
            now = database_clock(session)
            if not self.eligible(run, claim, now):
                return False
            assert run is not None
            run.status = "failed"
            run.error = ApiError(
                schema_version="error.v1",
                code=error.code,
                message=str(sanitized(str(error))),
                retryable=False,
                outcome_unknown=error.outcome_unknown,
                run_id=run.id,
            ).model_dump(mode="json")
            attempt = session.get(RunAttempt, (run.id, run.attempt))
            assert attempt is not None
            attempt.status = "failed"
            attempt.outcome_unknown = error.outcome_unknown
            attempt.finished_at = now
            self.emit(session, run, "failed")
            return True

    def publish(
        self, session: Session, run: GenerationRun, proposal: Contract, space: Space
    ) -> str:
        if isinstance(proposal, QuestionnaireProposal):
            intake = IntakeSession(
                id=entity_id("intake"),
                space_id=space.id,
                questionnaire=proposal.model_dump(mode="json"),
                questions_asked=run.input_payload["questions_asked"] + len(proposal.questions),
                round=run.input_payload["round"],
            )
            session.add(intake)
            return intake.id
        if isinstance(proposal, LearningProfileProposal):
            space.profile_revision += 1
            profile = ProfileRevision(
                id=entity_id("profile"),
                space_id=space.id,
                revision=space.profile_revision,
                proposal=proposal.model_dump(mode="json"),
                evidence=run.input_payload,
            )
            session.add(profile)
            parsed = parse_input("profile", run.input_payload)
            assert isinstance(parsed, ProfileInput | ProfileHistoryInput)
            answers = {
                answer.question_key: answer.model_dump(mode="json") for answer in parsed.answers
            }
            for intake_row in session.scalars(
                select(IntakeSession).where(IntakeSession.space_id == space.id)
            ):
                intake_row.answers = [
                    answers[q["key"]] for q in intake_row.questionnaire["questions"]
                ]
            return profile.id
        from unfold.app.learning import Knowledge

        return Knowledge.publish(session, run, proposal, space)

    def finish_success(self, claim: Claim, raw_proposal: object) -> bool:
        proposal = validate_pair(claim.operation, claim.input, raw_proposal)
        with self.database.transaction() as session:
            # Consistent lock order with enqueue: space, then run.
            unlocked = session.get(GenerationRun, claim.run_id)
            if unlocked is None:
                return False
            space = self.require_space(session, unlocked.space_id, lock=True)
            run = session.scalar(
                select(GenerationRun).where(GenerationRun.id == claim.run_id).with_for_update()
            )
            now = database_clock(session)
            if not self.eligible(run, claim, now):
                return False
            assert run is not None
            from unfold.app.learning import Knowledge

            if space.profile_revision != run.context_dependencies[
                "profile_revision"
            ] or not Knowledge.dependencies_current(session, space, run.context_dependencies):
                run.status = "conflicted"
                run.error = ApiError(
                    schema_version="error.v1",
                    code="version_conflict",
                    message="learning dependencies changed during generation",
                    retryable=False,
                    outcome_unknown=False,
                    run_id=run.id,
                ).model_dump(mode="json")
                attempt = session.get(RunAttempt, (run.id, run.attempt))
                assert attempt is not None
                attempt.status = "conflicted"
                attempt.finished_at = now
                self.emit(session, run, "conflicted")
                return False
            result_ref = self.publish(session, run, proposal, space)
            from unfold.app.models import StructureSuggestion

            # The artifact type is determined by saved records, never proposal prose.
            session.flush()
            result_kind = (
                "structure_preview" if session.get(StructureSuggestion, result_ref) else "knowledge"
            )
            run.proposal = proposal.model_dump(mode="json")
            run.result_ref = result_ref
            run.result_kind = result_kind
            run.status = "succeeded"
            attempt = session.get(RunAttempt, (run.id, run.attempt))
            assert attempt is not None
            attempt.status = "succeeded"
            attempt.finished_at = now
            self.emit(session, run, "completed")
            return True

    def trace(self, run_id: str) -> dict[str, Any]:
        with self.database.transaction() as session:
            run = session.get(GenerationRun, run_id)
            if run is None:
                raise ServiceError("invalid_input", "run not found", 404)
            return {
                "snapshot": self.snapshot(run).model_dump(mode="json"),
                "input": run.input_payload,
                "request": {
                    "schema_version": "api.v1",
                    "space_id": run.space_id,
                    "operation": run.operation,
                    "profile_revision": run.profile_revision,
                    "base_revision": run.base_revision,
                    "input": run.input_payload,
                },
                "dependencies": run.context_dependencies,
                "proposal": run.proposal,
                "attempts": [
                    {
                        "attempt": item.attempt,
                        "status": item.status,
                        "outcome_unknown": item.outcome_unknown,
                    }
                    for item in session.scalars(
                        select(RunAttempt).where(RunAttempt.run_id == run_id)
                    )
                ],
                "calls": [
                    {
                        "id": item.id,
                        "attempt": item.attempt,
                        "step": item.step,
                        "provider": item.provider,
                        "model": item.model,
                        "prompt_version": item.prompt_version,
                        "schema_version": item.schema_version,
                        "status": item.status,
                        "request": item.request,
                        "response": item.response,
                        "error_code": item.error_code,
                        "outcome_unknown": item.outcome_unknown,
                        "reserved_cost": str(item.reserved_cost),
                        "currency": item.currency,
                        "latency_seconds": str(item.latency_seconds)
                        if item.latency_seconds is not None
                        else None,
                        "input_tokens": item.input_tokens,
                        "output_tokens": item.output_tokens,
                        "accounted_cost": str(item.accounted_cost)
                        if item.accounted_cost is not None
                        else None,
                    }
                    for item in session.scalars(select(ModelCall).where(ModelCall.run_id == run_id))
                ],
            }
