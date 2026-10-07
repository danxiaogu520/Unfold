"""Immutable questions, public projections and deterministic saved-answer grading."""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from unfold.api_contract import CreateRun, public_exercise
from unfold.app.models import (
    ContentRevision,
    ExerciseAnswer,
    ExerciseIssue,
    ExerciseQuestion,
    ExerciseSetRevision,
    GenerationRun,
    OutlineItem,
    Space,
)
from unfold.app.security import sanitized
from unfold.app.service import ServiceError, entity_id
from unfold.contracts import Contract, Exercise, ExerciseSetProposal, Key, Text, grade


class AnswerSubmission(Contract):
    selected_keys: Annotated[list[Key], Field(min_length=1, max_length=6)]


class QuestionIssue(Contract):
    text: Text


def content_text(content: ContentRevision) -> str:
    text = "\n\n".join(
        block.get("text", block.get("fallback_text", "")) for block in content.blocks
    )
    if len(text) > 6000:
        raise ServiceError(
            "budget_exceeded", "saved content exceeds snapshot size; no truncation", 413
        )
    return text or content.overview


class Practice:
    @staticmethod
    def available_refs(session: Session, space_id: str, node_id: str) -> list[str]:
        return list(
            session.scalars(
                select(ExerciseQuestion.id)
                .join(ExerciseSetRevision)
                .where(
                    ExerciseSetRevision.space_id == space_id,
                    ExerciseSetRevision.node_id == node_id,
                    ExerciseQuestion.status == "active",
                )
                .order_by(ExerciseQuestion.id)
                .limit(20)
            )
        )

    @staticmethod
    def augment_input(
        session: Session,
        space: Space,
        node_id: str,
        raw: dict[str, Any],
        *,
        count: int,
        regenerate: bool,
        additional_content_ids: list[str] | None = None,
    ) -> None:
        content = session.scalar(
            select(ContentRevision).where(
                ContentRevision.node_id == node_id,
                ContentRevision.revision == raw["target"]["content_revision"],
            )
        )
        assert content
        existing = session.scalar(
            select(ExerciseSetRevision.id)
            .where(
                ExerciseSetRevision.content_id == content.id,
            )
            .limit(1)
        )
        if existing and not regenerate:
            raise ServiceError(
                "version_conflict", "exercises exist; read or explicitly regenerate", 409
            )
        provided = [{"revision_ref": content.id, "text": content_text(content)}]
        for content_id in additional_content_ids or []:
            extra = session.scalar(
                select(ContentRevision).where(
                    ContentRevision.space_id == space.id, ContentRevision.id == content_id
                )
            )
            if not extra or extra.id == content.id:
                raise ServiceError(
                    "invalid_input", "additional content not available in this space"
                )
            provided.append({"revision_ref": extra.id, "text": content_text(extra)})
        raw.update(
            provided_content=provided,
            allowed_modes=["single", "multiple"],
            count=count,
        )

    @staticmethod
    def verify_input(session: Session, request: CreateRun) -> dict[str, int]:
        provided = request.input["provided_content"]
        node = request.input["target"]
        content = session.scalar(
            select(ContentRevision).where(
                ContentRevision.space_id == request.space_id,
                ContentRevision.node_id == node["key"],
                ContentRevision.revision == node["content_revision"],
            )
        )
        if not content or provided[0] != {
            "revision_ref": content.id,
            "text": content_text(content),
        }:
            raise ServiceError(
                "version_conflict", "exercise content must equal the saved version", 409
            )
        if request.input["allowed_modes"] != ["single", "multiple"]:
            raise ServiceError("invalid_input", "exercise modes do not match server capability")
        if len(provided) > 5 or len({p["revision_ref"] for p in provided}) != len(provided):
            raise ServiceError(
                "invalid_input", "exercise references must be unique and at most five"
            )
        dependencies = {}
        for item in provided:
            saved = session.scalar(
                select(ContentRevision).where(
                    ContentRevision.space_id == request.space_id,
                    ContentRevision.id == item["revision_ref"],
                )
            )
            owner = session.get(OutlineItem, saved.node_id) if saved else None
            if (
                not saved
                or not owner
                or not owner.active
                or owner.revision != saved.revision
                or item["text"] != content_text(saved)
            ):
                raise ServiceError(
                    "version_conflict", "exercise source is not current saved content", 409
                )
            dependencies[owner.id] = owner.revision
        return dependencies

    @staticmethod
    def publish(session: Session, run: GenerationRun, proposal: ExerciseSetProposal) -> str:
        revision = (
            session.scalar(
                select(func.max(ExerciseSetRevision.revision)).where(
                    ExerciseSetRevision.node_id == proposal.target_key,
                )
            )
            or 0
        ) + 1
        saved = ExerciseSetRevision(
            id=entity_id("exercise_set"),
            space_id=run.space_id,
            node_id=proposal.target_key,
            content_id=run.input_payload["provided_content"][0]["revision_ref"],
            revision=revision,
            profile_revision=run.profile_revision,
            source_run_id=run.id,
        )
        session.add(saved)
        session.flush()
        for position, question in enumerate(proposal.exercises):
            session.add(
                ExerciseQuestion(
                    id=entity_id("question"),
                    space_id=run.space_id,
                    set_id=saved.id,
                    position=position,
                    proposal=question.model_dump(mode="json"),
                    status="active",
                )
            )
        return saved.id

    @staticmethod
    def read(session: Session, space_id: str, node_id: str) -> list[dict[str, Any]]:
        result = []
        for saved in session.scalars(
            select(ExerciseSetRevision)
            .where(
                ExerciseSetRevision.space_id == space_id,
                ExerciseSetRevision.node_id == node_id,
            )
            .order_by(ExerciseSetRevision.revision.desc())
        ):
            questions = []
            for question in session.scalars(
                select(ExerciseQuestion)
                .where(
                    ExerciseQuestion.set_id == saved.id,
                )
                .order_by(ExerciseQuestion.position)
            ):
                public = public_exercise(Exercise.model_validate(question.proposal), question.id)
                attempts = [
                    answer.result
                    for answer in session.scalars(
                        select(ExerciseAnswer)
                        .where(
                            ExerciseAnswer.question_id == question.id,
                        )
                        .order_by(ExerciseAnswer.created_at)
                    )
                ]
                questions.append(
                    {
                        **public.model_dump(mode="json"),
                        "status": question.status,
                        "attempts": attempts,
                    }
                )
            result.append(
                {
                    "id": saved.id,
                    "revision": saved.revision,
                    "content_id": saved.content_id,
                    "profile_revision": saved.profile_revision,
                    "questions": questions,
                }
            )
        return result

    @staticmethod
    def question(session: Session, space_id: str, question_id: str) -> ExerciseQuestion:
        row = session.scalar(
            select(ExerciseQuestion)
            .where(
                ExerciseQuestion.space_id == space_id,
                ExerciseQuestion.id == question_id,
            )
            .with_for_update(key_share=True)
        )
        if row is None:
            raise ServiceError("invalid_input", "exercise question not found", 404)
        return row

    @classmethod
    def submit(
        cls,
        session: Session,
        space_id: str,
        question_id: str,
        submission: AnswerSubmission,
        key: str,
    ) -> dict[str, Any]:
        if not 1 <= len(key) <= 128:
            raise ServiceError("invalid_input", "Idempotency-Key length must be 1..128")
        question = cls.question(session, space_id, question_id)
        selected = sorted(submission.selected_keys)
        existing = session.scalar(
            select(ExerciseAnswer).where(
                ExerciseAnswer.question_id == question_id,
                ExerciseAnswer.idempotency_key == key,
            )
        )
        if existing:
            if existing.selected_keys != selected:
                raise ServiceError(
                    "version_conflict", "same answer key has different selection", 409
                )
            return existing.result
        if question.status != "active":
            raise ServiceError("version_conflict", "question disabled after issue report", 409)
        exercise = Exercise.model_validate(question.proposal)
        try:
            correct = grade(exercise, submission.selected_keys)
        except ValueError as exc:
            raise ServiceError("invalid_input", str(exc)) from exc
        result = {
            "schema_version": "answer-result.v1",
            "answer_id": entity_id("answer"),
            "question_id": question.id,
            "exercise_set_id": question.set_id,
            "selected_keys": selected,
            "correct": correct,
            "correct_keys": exercise.correct_keys,
            "explanation": exercise.explanation,
            "content_refs": exercise.content_refs,
            "grading": "exact-set.v1",
        }
        session.add(
            ExerciseAnswer(
                id=result["answer_id"],
                question_id=question.id,
                idempotency_key=key,
                selected_keys=selected,
                correct=correct,
                result=result,
            )
        )
        return result

    @classmethod
    def report(
        cls, session: Session, space_id: str, question_id: str, issue: QuestionIssue
    ) -> dict[str, Any]:
        question = cls.question(session, space_id, question_id)
        saved = ExerciseIssue(
            id=entity_id("question_issue"), question_id=question.id, text=str(sanitized(issue.text))
        )
        session.add(saved)
        question.status = "disabled"
        return {"id": saved.id, "question_id": question.id, "status": "disabled"}
