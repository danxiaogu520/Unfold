from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Space(Base):
    __tablename__ = "spaces"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    owner: Mapped[str] = mapped_column(String(80), default="local", server_default="local")
    domain: Mapped[str] = mapped_column(Text)
    user_brief: Mapped[str] = mapped_column(Text)
    profile_revision: Mapped[int] = mapped_column(Integer, server_default="0")
    outline_revision: Mapped[int] = mapped_column(Integer, server_default="0")
    root_id: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        ForeignKeyConstraint(
            ["id", "root_id"],
            ["outline_items.space_id", "outline_items.id"],
            name="fk_space_root",
            use_alter=True,
        ),
        CheckConstraint(
            "profile_revision >= 0 AND outline_revision >= 0", name="ck_space_revisions"
        ),
    )


class ProfileRevision(Base):
    __tablename__ = "profile_revisions"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    space_id: Mapped[str] = mapped_column(ForeignKey("spaces.id"))
    revision: Mapped[int] = mapped_column(Integer)
    proposal: Mapped[dict[str, Any]] = mapped_column(JSONB)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        UniqueConstraint("space_id", "revision", name="uq_profile_revision"),
        CheckConstraint("revision > 0", name="ck_profile_revision_positive"),
    )


class IntakeSession(Base):
    __tablename__ = "intake_sessions"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    space_id: Mapped[str] = mapped_column(ForeignKey("spaces.id"))
    questionnaire: Mapped[dict[str, Any]] = mapped_column(JSONB)
    answers: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    questions_asked: Mapped[int] = mapped_column(Integer)
    round: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        CheckConstraint(
            "questions_asked BETWEEN 0 AND 5 AND round BETWEEN 1 AND 2", name="ck_intake_limits"
        ),
    )


class Concept(Base):
    __tablename__ = "concepts"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    space_id: Mapped[str] = mapped_column(ForeignKey("spaces.id"))
    title: Mapped[str] = mapped_column(Text)
    scope: Mapped[str] = mapped_column(Text)
    aliases: Mapped[list[str]] = mapped_column(JSONB, default=list)
    __table_args__ = (UniqueConstraint("space_id", "id", name="uq_concept_space_id"),)


class OutlineItem(Base):
    __tablename__ = "outline_items"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    space_id: Mapped[str] = mapped_column(ForeignKey("spaces.id"))
    parent_id: Mapped[str | None] = mapped_column(String(80))
    concept_id: Mapped[str | None] = mapped_column(String(80))
    position: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(Text)
    scope: Mapped[str] = mapped_column(Text)
    overview: Mapped[str] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(Integer, server_default="1")
    active: Mapped[bool] = mapped_column(Boolean, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        UniqueConstraint("space_id", "id", name="uq_item_space_id"),
        ForeignKeyConstraint(
            ["space_id", "parent_id"],
            ["outline_items.space_id", "outline_items.id"],
            name="fk_item_parent_space",
        ),
        ForeignKeyConstraint(
            ["space_id", "concept_id"],
            ["concepts.space_id", "concepts.id"],
            name="fk_item_concept_space",
        ),
        CheckConstraint("parent_id IS NULL OR parent_id <> id", name="ck_item_not_own_parent"),
        CheckConstraint("revision > 0 AND position >= 0", name="ck_item_revision_position"),
        CheckConstraint("kind IN ('concept','group')", name="ck_item_kind"),
        CheckConstraint(
            "(kind='group' AND concept_id IS NULL) OR kind='concept'", name="ck_item_group_identity"
        ),
        Index(
            "uq_active_root",
            "space_id",
            unique=True,
            postgresql_where=text("active AND parent_id IS NULL"),
        ),
        Index(
            "uq_active_sibling_position",
            "space_id",
            "parent_id",
            "position",
            unique=True,
            postgresql_where=text("active"),
        ),
    )


class NodeDecision(Base):
    __tablename__ = "node_decisions"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    space_id: Mapped[str] = mapped_column(ForeignKey("spaces.id"))
    node_id: Mapped[str] = mapped_column(String(80))
    profile_revision: Mapped[int] = mapped_column(Integer)
    content_revision: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        ForeignKeyConstraint(
            ["space_id", "node_id"],
            ["outline_items.space_id", "outline_items.id"],
            name="fk_decision_item_space",
        ),
        ForeignKeyConstraint(
            ["space_id", "profile_revision"],
            ["profile_revisions.space_id", "profile_revisions.revision"],
            name="fk_decision_profile_space",
        ),
        UniqueConstraint(
            "node_id", "profile_revision", "content_revision", name="uq_node_decision_basis"
        ),
        CheckConstraint(
            "state IN ('leaf','expandable') AND content_revision > 0", name="ck_decision_state"
        ),
    )


class ContentRevision(Base):
    __tablename__ = "content_revisions"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    space_id: Mapped[str] = mapped_column(ForeignKey("spaces.id"))
    node_id: Mapped[str] = mapped_column(String(80))
    revision: Mapped[int] = mapped_column(Integer)
    profile_revision: Mapped[int] = mapped_column(Integer)
    overview: Mapped[str] = mapped_column(Text)
    blocks: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    source: Mapped[dict[str, Any]] = mapped_column(JSONB)
    review_status: Mapped[str] = mapped_column(String(16), server_default="unverified")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        CheckConstraint(
            "review_status IN ('unverified','reported')", name="ck_content_review_status"
        ),
        ForeignKeyConstraint(
            ["space_id", "node_id"],
            ["outline_items.space_id", "outline_items.id"],
            name="fk_content_item_space",
        ),
        ForeignKeyConstraint(
            ["space_id", "profile_revision"],
            ["profile_revisions.space_id", "profile_revisions.revision"],
            name="fk_content_profile_space",
        ),
        UniqueConstraint("node_id", "revision", name="uq_content_revision"),
        UniqueConstraint("space_id", "id", name="uq_content_space_id"),
        CheckConstraint("revision > 0", name="ck_content_revision_positive"),
    )


class RelationRecord(Base):
    __tablename__ = "relations"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    space_id: Mapped[str] = mapped_column(ForeignKey("spaces.id"))
    source_id: Mapped[str] = mapped_column(String(80))
    target_id: Mapped[str] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(24))
    explanation: Mapped[str] = mapped_column(Text)
    __table_args__ = (
        ForeignKeyConstraint(
            ["space_id", "source_id"],
            ["outline_items.space_id", "outline_items.id"],
            name="fk_relation_source_space",
        ),
        ForeignKeyConstraint(
            ["space_id", "target_id"],
            ["outline_items.space_id", "outline_items.id"],
            name="fk_relation_target_space",
        ),
        UniqueConstraint("source_id", "target_id", "kind", name="uq_relation_edge"),
        CheckConstraint("source_id <> target_id", name="ck_relation_not_self"),
        CheckConstraint(
            "kind IN ('related_to','prerequisite_of','contrasted_with')", name="ck_relation_kind"
        ),
    )


class LearningFeedback(Base):
    __tablename__ = "learning_feedback"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    space_id: Mapped[str] = mapped_column(ForeignKey("spaces.id"))
    node_id: Mapped[str | None] = mapped_column(String(80))
    profile_revision: Mapped[int] = mapped_column(Integer)
    content_revision: Mapped[int | None] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(32))
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        ForeignKeyConstraint(
            ["space_id", "node_id"],
            ["outline_items.space_id", "outline_items.id"],
            name="fk_feedback_item_space",
        ),
        ForeignKeyConstraint(
            ["space_id", "profile_revision"],
            ["profile_revisions.space_id", "profile_revisions.revision"],
            name="fk_feedback_profile_space",
        ),
    )


class StructureSuggestion(Base):
    __tablename__ = "structure_suggestions"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    space_id: Mapped[str] = mapped_column(ForeignKey("spaces.id"))
    node_id: Mapped[str] = mapped_column(String(80))
    profile_revision: Mapped[int] = mapped_column(Integer)
    content_revision: Mapped[int] = mapped_column(Integer)
    proposal: Mapped[dict[str, Any]] = mapped_column(JSONB)
    operation: Mapped[str] = mapped_column(String(24), server_default="reassessment")
    status: Mapped[str] = mapped_column(String(16), server_default="pending")
    run_id: Mapped[str | None] = mapped_column(String(80))
    request: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        ForeignKeyConstraint(
            ["space_id", "run_id"],
            ["generation_runs.space_id", "generation_runs.id"],
            name="fk_suggestion_run_space",
        ),
        CheckConstraint("status IN ('pending','applied','dismissed')", name="ck_suggestion_status"),
        ForeignKeyConstraint(
            ["space_id", "node_id"],
            ["outline_items.space_id", "outline_items.id"],
            name="fk_suggestion_item_space",
        ),
        ForeignKeyConstraint(
            ["space_id", "profile_revision"],
            ["profile_revisions.space_id", "profile_revisions.revision"],
            name="fk_suggestion_profile_space",
        ),
    )


class GenerationRun(Base):
    __tablename__ = "generation_runs"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    space_id: Mapped[str] = mapped_column(ForeignKey("spaces.id"))
    operation: Mapped[str] = mapped_column(String(24))
    target_id: Mapped[str | None] = mapped_column(String(80))
    profile_revision: Mapped[int | None] = mapped_column(Integer)
    base_revision: Mapped[int | None] = mapped_column(Integer)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_digest: Mapped[str] = mapped_column(String(64))
    active_key: Mapped[str] = mapped_column(String(64))
    input_payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    context_dependencies: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16), server_default="queued")
    attempt: Mapped[int] = mapped_column(Integer, server_default="0")
    lease_token: Mapped[str | None] = mapped_column(String(80))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    not_before: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_event_seq: Mapped[int] = mapped_column(Integer, server_default="0")
    first_event_seq: Mapped[int] = mapped_column(Integer, server_default="1")
    result_ref: Mapped[str | None] = mapped_column(String(80))
    result_kind: Mapped[str | None] = mapped_column(String(24))
    proposal: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        UniqueConstraint("space_id", "id", name="uq_run_space_id"),
        UniqueConstraint("space_id", "idempotency_key", name="uq_run_request_identity"),
        ForeignKeyConstraint(
            ["space_id", "target_id"],
            ["outline_items.space_id", "outline_items.id"],
            name="fk_run_target_space",
        ),
        CheckConstraint(
            "status IN ('queued','running','succeeded','failed','cancelled','conflicted')",
            name="ck_run_status",
        ),
        CheckConstraint("attempt >= 0 AND last_event_seq >= 0", name="ck_run_counters"),
        CheckConstraint(
            "first_event_seq > 0 AND first_event_seq <= last_event_seq+1",
            name="ck_event_cursor_range",
        ),
        CheckConstraint(
            "(status='succeeded') = (result_ref IS NOT NULL)", name="ck_run_success_result"
        ),
        CheckConstraint(
            "(status='succeeded') = (result_kind IS NOT NULL) AND "
            "(result_kind IS NULL OR result_kind IN "
            "('knowledge','structure_preview','exercise_set','resource'))",
            name="ck_run_result_kind",
        ),
        CheckConstraint(
            "(status IN ('failed','conflicted')) = (error IS NOT NULL)", name="ck_run_error_state"
        ),
        CheckConstraint(
            "status <> 'running' OR "
            "(attempt > 0 AND lease_token IS NOT NULL AND lease_until IS NOT NULL)",
            name="ck_running_lease",
        ),
        Index(
            "uq_active_generation",
            "space_id",
            "active_key",
            unique=True,
            postgresql_where=text("status IN ('queued','running')"),
        ),
        Index("ix_claimable_run", "status", "not_before", "created_at"),
    )


class RequestIdentity(Base):
    __tablename__ = "request_identities"
    space_id: Mapped[str] = mapped_column(ForeignKey("spaces.id"), primary_key=True)
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(80))
    request_digest: Mapped[str] = mapped_column(String(64))
    __table_args__ = (
        ForeignKeyConstraint(
            ["space_id", "run_id"],
            ["generation_runs.space_id", "generation_runs.id"],
            name="fk_request_run_space",
        ),
        CheckConstraint("length(key) > 0", name="ck_request_key_nonempty"),
    )


class RunEventRecord(Base):
    __tablename__ = "run_events"
    run_id: Mapped[str] = mapped_column(ForeignKey("generation_runs.id"), primary_key=True)
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        CheckConstraint("sequence > 0", name="ck_event_sequence_positive"),
        Index("ix_event_retention", "created_at", "run_id", "sequence"),
    )


class RunAttempt(Base):
    __tablename__ = "run_attempts"
    run_id: Mapped[str] = mapped_column(ForeignKey("generation_runs.id"), primary_key=True)
    attempt: Mapped[int] = mapped_column(Integer, primary_key=True)
    lease_token: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(20))
    outcome_unknown: Mapped[bool] = mapped_column(Boolean, server_default="false")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (CheckConstraint("attempt > 0", name="ck_attempt_positive"),)


class ModelCall(Base):
    __tablename__ = "model_calls"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(80))
    attempt: Mapped[int] = mapped_column(Integer)
    step: Mapped[int] = mapped_column(Integer)
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(120))
    prompt_version: Mapped[str] = mapped_column(String(80))
    schema_version: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(20))
    request: Mapped[dict[str, Any]] = mapped_column(JSONB)
    response: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    reserved_cost: Mapped[Decimal] = mapped_column(Numeric(18, 8), server_default="0")
    accounted_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    currency: Mapped[str] = mapped_column(String(8), server_default="CNY")
    latency_seconds: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    error_code: Mapped[str | None] = mapped_column(String(40))
    outcome_unknown: Mapped[bool] = mapped_column(Boolean, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id", "attempt"],
            ["run_attempts.run_id", "run_attempts.attempt"],
            name="fk_call_attempt",
        ),
        UniqueConstraint("run_id", "attempt", "step", name="uq_call_step"),
        CheckConstraint("step > 0 AND reserved_cost >= 0", name="ck_call_step_cost"),
        CheckConstraint("input_tokens IS NULL OR input_tokens >= 0", name="ck_call_input_tokens"),
        CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0", name="ck_call_output_tokens"
        ),
    )


class ExerciseSetRevision(Base):
    __tablename__ = "exercise_sets"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    space_id: Mapped[str] = mapped_column(ForeignKey("spaces.id"))
    node_id: Mapped[str] = mapped_column(String(80))
    content_id: Mapped[str] = mapped_column(String(80))
    revision: Mapped[int] = mapped_column(Integer)
    profile_revision: Mapped[int] = mapped_column(Integer)
    source_run_id: Mapped[str] = mapped_column(ForeignKey("generation_runs.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        UniqueConstraint("node_id", "revision", name="uq_exercise_set_revision"),
        UniqueConstraint("space_id", "id", name="uq_exercise_set_space_id"),
        ForeignKeyConstraint(
            ["space_id", "node_id"],
            ["outline_items.space_id", "outline_items.id"],
            name="fk_exercise_node_space",
        ),
        ForeignKeyConstraint(
            ["space_id", "content_id"],
            ["content_revisions.space_id", "content_revisions.id"],
            name="fk_exercise_content_space",
        ),
        CheckConstraint("revision > 0 AND profile_revision > 0", name="ck_exercise_set_versions"),
    )


class ExerciseQuestion(Base):
    __tablename__ = "exercise_questions"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    space_id: Mapped[str] = mapped_column(ForeignKey("spaces.id"))
    set_id: Mapped[str] = mapped_column(String(80))
    position: Mapped[int] = mapped_column(Integer)
    proposal: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16), server_default="active")
    __table_args__ = (
        ForeignKeyConstraint(
            ["space_id", "set_id"],
            ["exercise_sets.space_id", "exercise_sets.id"],
            name="fk_exercise_question_set_space",
        ),
        UniqueConstraint("set_id", "position", name="uq_exercise_question_position"),
        CheckConstraint(
            "status IN ('active','disabled') AND position >= 0", name="ck_exercise_question_status"
        ),
    )


class ExerciseAnswer(Base):
    __tablename__ = "exercise_answers"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    question_id: Mapped[str] = mapped_column(ForeignKey("exercise_questions.id"))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    selected_keys: Mapped[list[str]] = mapped_column(JSONB)
    correct: Mapped[bool] = mapped_column(Boolean)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        UniqueConstraint("question_id", "idempotency_key", name="uq_answer_identity"),
    )


class ExerciseIssue(Base):
    __tablename__ = "exercise_issues"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    question_id: Mapped[str] = mapped_column(ForeignKey("exercise_questions.id"))
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ResourceTask(Base):
    """Transactional outbox: saved teaching content owns each immutable request."""

    __tablename__ = "resource_tasks"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    space_id: Mapped[str] = mapped_column(ForeignKey("spaces.id"))
    content_id: Mapped[str] = mapped_column(String(80))
    source_run_id: Mapped[str] = mapped_column(String(80))
    request_key: Mapped[str] = mapped_column(String(80))
    request: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16), server_default="pending")
    dispatch_revision: Mapped[int] = mapped_column(Integer, server_default="0")
    dispatched_run_id: Mapped[str | None] = mapped_column(ForeignKey("generation_runs.id"))
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        UniqueConstraint("content_id", "request_key", name="uq_resource_content_request"),
        ForeignKeyConstraint(
            ["space_id", "content_id"],
            ["content_revisions.space_id", "content_revisions.id"],
            name="fk_resource_content_space",
        ),
        ForeignKeyConstraint(
            ["space_id", "source_run_id"],
            ["generation_runs.space_id", "generation_runs.id"],
            name="fk_resource_source_run_space",
        ),
        CheckConstraint(
            "status IN ('pending','dispatched','failed')", name="ck_resource_task_status"
        ),
        CheckConstraint(
            "(status='dispatched') = (dispatched_run_id IS NOT NULL)",
            name="ck_resource_dispatch_run",
        ),
        Index("ix_pending_resource_task", "status", "created_at"),
        CheckConstraint("dispatch_revision >= 0", name="ck_resource_dispatch_revision"),
    )


class ResourceCandidate(Base):
    __tablename__ = "resource_candidates"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(80))
    attempt: Mapped[int] = mapped_column(Integer)
    file: Mapped[dict[str, Any]] = mapped_column(JSONB)
    source: Mapped[dict[str, Any]] = mapped_column(JSONB)
    proposal: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id", "attempt"],
            ["run_attempts.run_id", "run_attempts.attempt"],
            name="fk_resource_candidate_attempt",
        ),
    )


class ResourceRevision(Base):
    __tablename__ = "resource_revisions"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("resource_tasks.id"))
    revision: Mapped[int] = mapped_column(Integer)
    candidate_id: Mapped[str] = mapped_column(ForeignKey("resource_candidates.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        UniqueConstraint("task_id", "revision", name="uq_resource_revision"),
        CheckConstraint("revision > 0", name="ck_resource_revision"),
    )


class ToolInvocation(Base):
    __tablename__ = "tool_invocations"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(80))
    attempt: Mapped[int] = mapped_column(Integer)
    step: Mapped[int] = mapped_column(Integer)
    tool: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(16))
    input: Mapped[dict[str, Any]] = mapped_column(JSONB)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    outcome_unknown: Mapped[bool] = mapped_column(Boolean, server_default="false")
    latency_seconds: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        UniqueConstraint("run_id", "attempt", "step", name="uq_tool_attempt_step"),
        ForeignKeyConstraint(
            ["run_id", "attempt"],
            ["run_attempts.run_id", "run_attempts.attempt"],
            name="fk_tool_attempt",
        ),
        CheckConstraint(
            "step > 0 AND status IN ('started','completed','failed')", name="ck_tool_state"
        ),
    )


class BudgetLedger(Base):
    __tablename__ = "budget_ledgers"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    currency: Mapped[str] = mapped_column(String(8))
    limit_cost: Mapped[Decimal] = mapped_column(Numeric(18, 8))
    accounted_cost: Mapped[Decimal] = mapped_column(Numeric(18, 8), server_default="0")
    __table_args__ = (
        CheckConstraint("limit_cost > 0 AND accounted_cost >= 0", name="ck_ledger_cost"),
    )
