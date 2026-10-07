"""Authoritative learning snapshots, local proposals and versioned publication."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field, ValidationError, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from unfold.api_contract import CreateRun
from unfold.app.models import (
    Concept,
    ContentRevision,
    GenerationRun,
    IntakeSession,
    LearningFeedback,
    NodeDecision,
    OutlineItem,
    ProfileRevision,
    RelationRecord,
    ResourceTask,
    Space,
    StructureSuggestion,
)
from unfold.app.security import sanitized
from unfold.app.service import ServiceError, entity_id
from unfold.contracts import (
    Answer,
    Contract,
    Decision,
    ExerciseSetProposal,
    ExpansionProposal,
    ExplanationProposal,
    IntakeRound,
    Key,
    LearningProfileProposal,
    NodeCandidate,
    NodeSnapshot,
    OutlineProposal,
    ReassessmentProposal,
    Revision,
    Text,
)
from unfold.provider import json_bytes


class LearningAction(Contract):
    operation: Literal["outline", "expansion", "reassessment", "explanation", "exercises"]
    expected_profile_revision: Revision
    expected_base_revision: Revision | None = None
    target_id: Key | None = None
    instruction: Text = "结合当前用途说明这个节点，并给出一个例子。"
    regenerate: bool = False
    count: Annotated[int, Field(ge=1, le=5)] = 3
    context_strategy: Literal["all_history", "current_path", "structural_relevance"] = (
        "current_path"
    )
    exercise_content_ids: Annotated[list[Key], Field(max_length=4)] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_target(self) -> LearningAction:
        if self.operation != "outline" and (
            self.target_id is None or self.expected_base_revision is None
        ):
            raise ValueError("node operation requires target and expected base revision")
        if self.operation == "outline" and self.target_id is not None:
            raise ValueError("outline operation has no existing target")
        if self.exercise_content_ids and self.operation != "exercises":
            raise ValueError("extra exercise content is only valid for exercises")
        if len(set(self.exercise_content_ids)) != len(self.exercise_content_ids):
            raise ValueError("duplicate exercise content references")
        return self


class ProfileEdit(Contract):
    expected_revision: Revision
    fields: Annotated[dict[str, Text | list[Text] | None], Field(min_length=1, max_length=9)]


class FeedbackInput(Contract):
    expected_profile_revision: Revision
    expected_base_revision: Revision
    kind: Literal[
        "too_easy",
        "too_hard",
        "need_example",
        "wrong_scope",
        "stopping",
        "factual_error",
        "omission",
        "other",
    ]
    text: Text


class IntakeAction(Contract):
    operation: Literal["questionnaire", "profile"]
    expected_intake_id: Key
    answers: Annotated[list[Answer], Field(max_length=5)]


class Knowledge:
    @staticmethod
    def intakes(session: Session, space: Space) -> list[IntakeSession]:
        return list(
            session.scalars(
                select(IntakeSession)
                .where(
                    IntakeSession.space_id == space.id,
                )
                .order_by(IntakeSession.created_at)
            )
        )

    @classmethod
    def intake_digest(cls, session: Session, space: Space) -> str:
        return hashlib.sha256(
            json_bytes(
                [
                    {"id": item.id, "questionnaire": item.questionnaire, "answers": item.answers}
                    for item in cls.intakes(session, space)
                ]
            )
        ).hexdigest()

    @classmethod
    def build_intake_request(
        cls, session: Session, space: Space, action: IntakeAction
    ) -> CreateRun:
        if space.profile_revision:
            raise ServiceError("version_conflict", "learning profile already exists", 409)
        intakes = cls.intakes(session, space)
        if not intakes or intakes[-1].id != action.expected_intake_id:
            raise ServiceError("version_conflict", "intake snapshot changed", 409)
        latest = intakes[-1]
        try:
            IntakeRound.model_validate(
                {
                    "questionnaire": latest.questionnaire,
                    "answers": [answer.model_dump(mode="json") for answer in action.answers],
                }
            )
        except ValidationError as exc:
            raise ServiceError("invalid_input", "answers do not match saved questions") from exc
        answers = [answer.model_dump(mode="json") for answer in action.answers]
        if latest.answers is not None and latest.answers != answers:
            raise ServiceError(
                "version_conflict", "saved intake answers cannot be silently replaced", 409
            )
        latest.answers = sanitized(answers)
        if action.operation == "profile":
            if any(item.answers is None for item in intakes):
                raise ServiceError("invalid_input", "all intake rounds require answers")
            raw: dict[str, Any] = {
                "schema_version": "profile-input.v2" if space.user_brief else "profile-input.v3",
                "domain": space.domain,
                "user_brief": space.user_brief,
                "rounds": [
                    {"questionnaire": item.questionnaire, "answers": item.answers}
                    for item in intakes
                ],
            }
        else:
            asked = sum(len(item.questionnaire["questions"]) for item in intakes)
            if len(intakes) >= 2 or asked >= 5:
                raise ServiceError(
                    "budget_exceeded", "intake limited to two rounds and five questions", 409
                )
            known: set[str] = set()
            for item in intakes:
                source = session.scalar(
                    select(GenerationRun).where(
                        GenerationRun.result_ref == item.id,
                        GenerationRun.operation == "questionnaire",
                    )
                )
                if source:
                    known.update(source.input_payload["known_targets"])
                by_key = {answer["question_key"]: answer for answer in item.answers or []}
                known.update(
                    q["target"]
                    for q in item.questionnaire["questions"]
                    if not by_key[q["key"]]["unknown"]
                )
            raw = {
                "schema_version": "questionnaire-input.v1"
                if space.user_brief
                else "questionnaire-input.v2",
                "domain": space.domain,
                "user_brief": space.user_brief,
                "known_targets": sorted(known),
                "previous_question_keys": [
                    q["key"] for item in intakes for q in item.questionnaire["questions"]
                ],
                "questions_asked": asked,
                "round": len(intakes) + 1,
            }
        return CreateRun(
            schema_version="api.v1",
            space_id=space.id,
            operation=action.operation,
            profile_revision=None,
            base_revision=None,
            input=raw,
        )

    @staticmethod
    def profile(session: Session, space: Space) -> ProfileRevision:
        saved = session.scalar(
            select(ProfileRevision).where(
                ProfileRevision.space_id == space.id,
                ProfileRevision.revision == space.profile_revision,
            )
        )
        if saved is None:
            raise ServiceError("invalid_input", "a saved learning profile is required", 409)
        return saved

    @staticmethod
    def item(session: Session, space: Space, node_id: str, *, active: bool = True) -> OutlineItem:
        node = session.scalar(
            select(OutlineItem).where(
                OutlineItem.space_id == space.id,
                OutlineItem.id == node_id,
            )
        )
        if node is None or (active and not node.active):
            raise ServiceError("invalid_input", "active node not found in this space", 404)
        return node

    @staticmethod
    def decision(session: Session, node: OutlineItem) -> NodeDecision:
        saved = session.scalar(
            select(NodeDecision)
            .where(
                NodeDecision.node_id == node.id,
                NodeDecision.content_revision == node.revision,
            )
            .order_by(NodeDecision.profile_revision.desc())
        )
        if saved is None:
            raise ServiceError("invalid_input", "node has no saved expansion decision", 409)
        return saved

    @classmethod
    def snapshot(cls, session: Session, node: OutlineItem) -> dict[str, Any]:
        decision = cls.decision(session, node)
        return NodeSnapshot(
            key=node.id,
            title=node.title,
            scope=node.scope,
            overview=node.overview,
            decision=Decision.model_validate({"state": decision.state, "reason": decision.reason}),
            profile_revision=decision.profile_revision,
            content_revision=node.revision,
        ).model_dump(mode="json")

    @staticmethod
    def children(session: Session, node: OutlineItem) -> list[OutlineItem]:
        return list(
            session.scalars(
                select(OutlineItem)
                .where(
                    OutlineItem.space_id == node.space_id,
                    OutlineItem.parent_id == node.id,
                    OutlineItem.active.is_(True),
                )
                .order_by(OutlineItem.position)
            )
        )

    @classmethod
    def context(cls, session: Session, space: Space, node: OutlineItem) -> dict[str, Any]:
        path: list[OutlineItem] = [node]
        seen = {node.id}
        while path[-1].parent_id:
            parent = cls.item(session, space, path[-1].parent_id or "")
            if parent.id in seen:
                raise ServiceError("domain_validation", "saved outline has a parent cycle", 409)
            seen.add(parent.id)
            path.append(parent)
        path.reverse()
        siblings = (
            list(
                session.scalars(
                    select(OutlineItem)
                    .where(
                        OutlineItem.space_id == space.id,
                        OutlineItem.parent_id == node.parent_id,
                        OutlineItem.active.is_(True),
                        OutlineItem.id != node.id,
                    )
                    .order_by(OutlineItem.position)
                )
            )
            if node.parent_id
            else []
        )
        profile = cls.profile(session, space)
        concepts = list(
            session.scalars(
                select(Concept)
                .where(
                    Concept.space_id == space.id,
                )
                .order_by(Concept.id)
                .limit(24)
            )
        )
        return {
            "profile": {"revision": space.profile_revision, "profile": profile.proposal},
            "target": cls.snapshot(session, node),
            "path": [cls.snapshot(session, item) for item in path],
            "existing_children": [
                cls.snapshot(session, child) for child in cls.children(session, node)
            ],
            "sibling_scopes": [sibling.scope for sibling in siblings],
            "reusable_concept_refs": [concept.id for concept in concepts],
        }

    @classmethod
    def build_request(cls, session: Session, space: Space, action: LearningAction) -> CreateRun:
        if action.expected_profile_revision != space.profile_revision:
            raise ServiceError("version_conflict", "learning profile revision changed", 409)
        if action.operation == "outline":
            if space.root_id and not action.regenerate:
                raise ServiceError(
                    "version_conflict", "outline exists; read it or explicitly regenerate", 409
                )
            if action.expected_base_revision != (space.outline_revision or None):
                raise ServiceError("version_conflict", "outline revision changed", 409)
            profile = cls.profile(session, space)
            refs = list(
                session.scalars(
                    select(Concept.id)
                    .where(
                        Concept.space_id == space.id,
                    )
                    .order_by(Concept.id)
                    .limit(24)
                )
            )
            raw = {
                "schema_version": "outline-input.v1",
                "profile": {"revision": space.profile_revision, "profile": profile.proposal},
                "reusable_concept_refs": refs,
            }
        else:
            assert action.target_id is not None
            node = cls.item(session, space, action.target_id)
            if node.revision != action.expected_base_revision:
                raise ServiceError("version_conflict", "node revision changed", 409)
            raw = cls.context(session, space, node)
            raw["schema_version"] = action.operation + "-input.v1"
            if action.operation == "expansion":
                decision = cls.decision(session, node)
                if decision.profile_revision != space.profile_revision:
                    raise ServiceError(
                        "version_conflict", "stale decision requires reassessment", 409
                    )
                if decision.state == "leaf":
                    raise ServiceError(
                        "domain_validation", "leaf cannot expand or regenerate expansion", 409
                    )
                if raw["existing_children"] and not action.regenerate:
                    raise ServiceError(
                        "version_conflict", "children exist; read or explicitly regenerate", 409
                    )
                raw["operation"] = "regenerate_expansion" if action.regenerate else "expand"
            elif action.operation == "reassessment":
                raw["feedback"] = action.instruction
            elif action.operation == "exercises":
                from unfold.app.practice import Practice

                Practice.augment_input(
                    session,
                    space,
                    node.id,
                    raw,
                    count=action.count,
                    regenerate=action.regenerate,
                    additional_content_ids=action.exercise_content_ids,
                )
                raw.update(
                    schema_version="exercises-input.v2",
                    instruction=action.instruction
                    if "instruction" in action.model_fields_set
                    else "结合已保存正文生成选择题，包含理解和边界辨析。",
                    required_modes=["single", "multiple"] if action.count >= 2 else [],
                )
            else:
                from unfold.app.practice import Practice
                from unfold.app.resources import Resources

                raw.update(
                    instruction=action.instruction,
                    allowed_resource_kinds=Resources.capabilities()[0],
                    available_exercise_refs=Practice.available_refs(session, space.id, node.id),
                )
        return CreateRun(
            schema_version="api.v1",
            space_id=space.id,
            operation=action.operation,
            profile_revision=space.profile_revision,
            base_revision=action.expected_base_revision,
            input=raw,
        )

    @classmethod
    def verify_input(cls, session: Session, space: Space, request: CreateRun) -> dict[str, Any]:
        if request.operation == "outline":
            if request.base_revision != (space.outline_revision or None):
                raise ServiceError("version_conflict", "outline revision changed", 409)
            refs = list(
                session.scalars(
                    select(Concept.id)
                    .where(
                        Concept.space_id == space.id,
                    )
                    .order_by(Concept.id)
                    .limit(24)
                )
            )
            if request.input["reusable_concept_refs"] != refs:
                raise ServiceError("version_conflict", "concept context changed", 409)
            nodes: dict[str, int] = (
                {
                    item.id: item.revision
                    for item in session.scalars(
                        select(OutlineItem).where(
                            OutlineItem.space_id == space.id, OutlineItem.active.is_(True)
                        )
                    )
                }
                if space.root_id
                else {}
            )
        else:
            node = cls.item(session, space, request.input["target"]["key"])
            raw = cls.context(session, space, node)
            for field, value in raw.items():
                if request.input.get(field) != value:
                    raise ServiceError(
                        "version_conflict", "node context does not match saved state", 409
                    )
            if request.operation == "explanation":
                from unfold.app.practice import Practice

                if request.input["available_exercise_refs"] != Practice.available_refs(
                    session, space.id, node.id
                ):
                    raise ServiceError(
                        "version_conflict", "available question references changed", 409
                    )
            nodes = {
                item["key"]: item["content_revision"]
                for item in [
                    *raw["path"],
                    *raw["existing_children"],
                ]
            }
        refs = request.input["reusable_concept_refs"]
        selected = [
            {
                "id": concept.id,
                "title": concept.title,
                "scope": concept.scope,
                "aliases": concept.aliases,
            }
            for concept in session.scalars(select(Concept).where(Concept.id.in_(refs)))
        ]
        feedback = []
        if request.operation != "outline":
            feedback = [
                {
                    "kind": item.kind,
                    "text": item.text,
                    "profile_revision": item.profile_revision,
                    "content_revision": item.content_revision,
                }
                for item in session.scalars(
                    select(LearningFeedback)
                    .where(
                        LearningFeedback.space_id == space.id,
                        LearningFeedback.node_id == request.input["target"]["key"],
                    )
                    .order_by(LearningFeedback.created_at.desc())
                    .limit(5)
                )
            ]
        origins = []
        for node_id in nodes if request.operation != "outline" else []:
            origin_node = session.get(OutlineItem, node_id)
            assert origin_node is not None
            content = session.scalar(
                select(ContentRevision).where(
                    ContentRevision.node_id == origin_node.id,
                    ContentRevision.revision == origin_node.revision,
                )
            )
            if content:
                origins.append(
                    {
                        "node_id": node_id,
                        "content_ref": content.id,
                        "profile_revision": content.profile_revision,
                        "carried_from": content.source.get("content_carried_from"),
                    }
                )
        material = {"concepts": selected, "feedback": feedback, "content_origins": origins}
        size = len(json_bytes({"input": request.input, "context": material}))
        if size > 24000:
            raise ServiceError(
                "budget_exceeded", "required learning context exceeds byte budget", 413
            )
        return {
            "node_revisions": nodes,
            "selected_context": sanitized(material),
            "context_strategy": "minimal.v1",
            "context_bytes": size,
            "output_tokens_reserved": 4000,
        }

    @staticmethod
    def dependencies_current(session: Session, space: Space, dependencies: dict[str, Any]) -> bool:
        if space.outline_revision != dependencies["outline_revision"]:
            return False
        if dependencies.get("intake_digest") is not None and dependencies[
            "intake_digest"
        ] != Knowledge.intake_digest(session, space):
            return False
        for node_id, revision in dependencies.get("node_revisions", {}).items():
            node = session.get(OutlineItem, node_id)
            if node is None or not node.active or node.revision != revision:
                return False
        if dependencies.get("resource_task_id"):
            task = session.get(ResourceTask, dependencies["resource_task_id"])
            if not task or task.dispatch_revision != dependencies["resource_dispatch_revision"]:
                return False
        return True

    @classmethod
    def read_node(
        cls, session: Session, space: Space, node_id: str, data_directory: Path = Path(".data")
    ) -> dict[str, Any]:
        from unfold.app.files import FileStore
        from unfold.app.practice import Practice
        from unfold.app.resources import Resources

        node = cls.item(session, space, node_id, active=False)
        decision = cls.decision(session, node)
        revisions = list(
            session.scalars(
                select(ContentRevision)
                .where(
                    ContentRevision.node_id == node.id,
                )
                .order_by(ContentRevision.revision.desc())
            )
        )
        suggestions = list(
            session.scalars(
                select(StructureSuggestion)
                .where(
                    StructureSuggestion.node_id == node.id,
                )
                .order_by(StructureSuggestion.created_at.desc())
            )
        )
        children = cls.children(session, node)
        return {
            "id": node.id,
            "parent_id": node.parent_id,
            "concept_id": node.concept_id,
            "kind": node.kind,
            "active": node.active,
            "revision": node.revision,
            "title": node.title,
            "scope": node.scope,
            "overview": node.overview,
            "decision": {
                "state": decision.state,
                "reason": decision.reason,
                "profile_revision": decision.profile_revision,
                "status": "current"
                if decision.profile_revision == space.profile_revision
                else "stale",
            },
            "children": [child.id for child in children],
            "feedback": [
                {
                    "id": item.id,
                    "kind": item.kind,
                    "text": item.text,
                    "profile_revision": item.profile_revision,
                    "content_revision": item.content_revision,
                    "created_at": item.created_at.isoformat(),
                }
                for item in session.scalars(
                    select(LearningFeedback)
                    .where(
                        LearningFeedback.space_id == space.id, LearningFeedback.node_id == node.id
                    )
                    .order_by(LearningFeedback.created_at.desc(), LearningFeedback.id)
                )
            ],
            "exercise_sets": Practice.read(session, space.id, node.id),
            "contents": [
                {
                    "id": item.id,
                    "revision": item.revision,
                    "profile_revision": item.profile_revision,
                    "overview": item.overview,
                    "blocks": item.blocks,
                    "source": item.source,
                    "review_status": item.review_status,
                    "resources": Resources.read(session, item.id, FileStore(data_directory)),
                }
                for item in revisions
            ],
            "suggestions": [
                {
                    "id": item.id,
                    "proposal": item.proposal,
                    "profile_revision": item.profile_revision,
                    "content_revision": item.content_revision,
                    "operation": item.operation,
                    "status": cls.preview_status(session, space, node, item),
                }
                for item in suggestions
            ],
        }

    @staticmethod
    def preview_status(
        session: Session, space: Space, node: OutlineItem, item: StructureSuggestion
    ) -> str:
        if item.status != "pending":
            return item.status
        run = session.get(GenerationRun, item.run_id) if item.run_id else None
        if (
            node.revision != item.content_revision
            or space.profile_revision != item.profile_revision
            or run is None
            or not Knowledge.dependencies_current(session, space, run.context_dependencies)
        ):
            return "stale"
        return "pending"

    @classmethod
    def space_nodes(
        cls, session: Session, space: Space, data_directory: Path = Path(".data")
    ) -> list[dict[str, Any]]:
        return [
            cls.read_node(session, space, item.id, data_directory)
            for item in session.scalars(
                select(OutlineItem)
                .where(OutlineItem.space_id == space.id, OutlineItem.active.is_(True))
                .order_by(OutlineItem.created_at, OutlineItem.position)
            )
        ]

    @staticmethod
    def space_relations(session: Session, space: Space) -> list[dict[str, Any]]:
        active_ids = select(OutlineItem.id).where(
            OutlineItem.space_id == space.id, OutlineItem.active.is_(True)
        )
        return [
            {
                "id": item.id,
                "source_id": item.source_id,
                "target_id": item.target_id,
                "kind": item.kind,
                "explanation": item.explanation,
            }
            for item in session.scalars(
                select(RelationRecord)
                .where(
                    RelationRecord.space_id == space.id,
                    RelationRecord.source_id.in_(active_ids),
                    RelationRecord.target_id.in_(active_ids),
                )
                .order_by(RelationRecord.id)
            )
        ]

    @classmethod
    def save_content(
        cls,
        session: Session,
        space: Space,
        node: OutlineItem,
        blocks: list[dict[str, Any]],
        source: dict[str, Any],
        decision: Decision,
        decision_profile: int | None = None,
        content_profile: int | None = None,
        review_status: str = "unverified",
    ) -> str:
        content = ContentRevision(
            id=entity_id("content"),
            space_id=space.id,
            node_id=node.id,
            revision=node.revision,
            profile_revision=content_profile or space.profile_revision,
            overview=node.overview,
            blocks=blocks,
            source=source,
            review_status=review_status,
        )
        session.add(content)
        session.add(
            NodeDecision(
                id=entity_id("decision"),
                space_id=space.id,
                node_id=node.id,
                profile_revision=decision_profile or space.profile_revision,
                content_revision=node.revision,
                state=decision.state,
                reason=decision.reason,
            )
        )
        return content.id

    @classmethod
    def add_candidate(
        cls,
        session: Session,
        space: Space,
        candidate: NodeCandidate,
        parent_id: str | None,
        position: int,
        source: dict[str, Any],
    ) -> OutlineItem:
        concept_id = candidate.concept_ref
        if candidate.kind == "concept":
            if concept_id:
                concept = session.get(Concept, concept_id)
                if (
                    concept is None
                    or concept.space_id != space.id
                    or concept.scope != candidate.scope
                ):
                    raise ServiceError(
                        "domain_validation", "concept reuse scope or identity mismatch"
                    )
            else:
                concept_id = entity_id("concept")
                session.add(
                    Concept(
                        id=concept_id,
                        space_id=space.id,
                        title=candidate.title,
                        scope=candidate.scope,
                        aliases=[],
                    )
                )
                session.flush()
        node = OutlineItem(
            id=entity_id("node"),
            space_id=space.id,
            parent_id=parent_id,
            concept_id=concept_id,
            position=position,
            kind=candidate.kind,
            title=candidate.title,
            scope=candidate.scope,
            overview=candidate.overview,
            revision=1,
            active=True,
        )
        session.add(node)
        session.flush()
        cls.save_content(
            session,
            space,
            node,
            [{"type": "markdown", "text": node.overview}],
            source,
            candidate.decision,
        )
        return node

    @classmethod
    def archive_children(cls, session: Session, node: OutlineItem) -> None:
        pending = cls.children(session, node)
        while pending:
            child = pending.pop()
            pending.extend(cls.children(session, child))
            child.active = False
        session.flush()

    @classmethod
    def save_relations(
        cls, session: Session, space: Space, proposal: Any, local_ids: dict[str, str]
    ) -> None:
        for relation in proposal.relations:
            session.add(
                RelationRecord(
                    id=entity_id("relation"),
                    space_id=space.id,
                    source_id=local_ids[relation.source_key],
                    target_id=local_ids[relation.target_key],
                    kind=relation.kind,
                    explanation=relation.explanation,
                )
            )

    @staticmethod
    def save_preview(
        session: Session, run: GenerationRun, proposal: Contract, space: Space, node: OutlineItem
    ) -> str:
        suggestion = StructureSuggestion(
            id=entity_id("suggestion"),
            space_id=space.id,
            node_id=node.id,
            profile_revision=space.profile_revision,
            content_revision=node.revision,
            proposal=proposal.model_dump(mode="json"),
            operation=run.operation,
            status="pending",
            run_id=run.id,
            request={
                "schema_version": "api.v1",
                "space_id": run.space_id,
                "operation": run.operation,
                "profile_revision": run.profile_revision,
                "base_revision": run.base_revision,
                "input": run.input_payload,
            },
        )
        session.add(suggestion)
        return suggestion.id

    @classmethod
    def publish(
        cls,
        session: Session,
        run: GenerationRun,
        proposal: Contract,
        space: Space,
        *,
        approved: bool = False,
        review_id: str | None = None,
    ) -> str:
        source = {
            "run_id": run.id,
            "operation": run.operation,
            "input": run.input_payload,
            "division_basis": getattr(proposal, "division_basis", None),
            "review_id": review_id,
            "publication_provenance": run.context_dependencies.get(
                "publication_provenance", "model_candidate"
            ),
            "review_references": run.context_dependencies.get("review_references", []),
        }
        if isinstance(proposal, OutlineProposal):
            if space.root_id:
                old_root = cls.item(session, space, space.root_id)
                if not approved:
                    return cls.save_preview(session, run, proposal, space, old_root)
                cls.archive_children(session, old_root)
                old_root.active = False
                session.flush()
            root = cls.add_candidate(session, space, proposal.root, None, 0, source)
            local_ids = {proposal.root.key: root.id}
            for position, candidate in enumerate(proposal.children):
                local_ids[candidate.key] = cls.add_candidate(
                    session, space, candidate, root.id, position, source
                ).id
            cls.save_relations(session, space, proposal, local_ids)
            space.root_id = root.id
            space.outline_revision += 1
            return root.id
        node = cls.item(session, space, run.target_id or "")
        if isinstance(proposal, ExerciseSetProposal):
            from unfold.app.practice import Practice

            return Practice.publish(session, run, proposal)
        old_decision = cls.decision(session, node)
        if isinstance(proposal, ExpansionProposal) and cls.children(session, node) and not approved:
            return cls.save_preview(session, run, proposal, space, node)
        if (
            isinstance(proposal, ReassessmentProposal)
            and cls.children(session, node)
            and proposal.decision.state == "leaf"
        ):
            if not approved:
                return cls.save_preview(session, run, proposal, space, node)
            cls.archive_children(session, node)
            space.outline_revision += 1
        previous = session.scalar(
            select(ContentRevision).where(
                ContentRevision.node_id == node.id,
                ContentRevision.revision == node.revision,
            )
        )
        assert previous is not None
        node.revision += 1
        source["content_carried_from"] = previous.id
        if isinstance(proposal, ExpansionProposal):
            cls.archive_children(session, node)
            local_ids = {proposal.target_key: node.id}
            for position, candidate in enumerate(proposal.children):
                local_ids[candidate.key] = cls.add_candidate(
                    session, space, candidate, node.id, position, source
                ).id
            cls.save_relations(session, space, proposal, local_ids)
            space.outline_revision += 1
            return cls.save_content(
                session,
                space,
                node,
                previous.blocks,
                source,
                proposal.decision,
                content_profile=previous.profile_revision,
                review_status=previous.review_status,
            )
        if isinstance(proposal, ExplanationProposal):
            node.overview = proposal.overview
            source.pop("content_carried_from", None)
            content_id = cls.save_content(
                session,
                space,
                node,
                [block.model_dump(mode="json") for block in proposal.blocks],
                source,
                Decision.model_validate(
                    {"state": old_decision.state, "reason": old_decision.reason}
                ),
                old_decision.profile_revision,
            )
            session.flush()
            for request in proposal.resource_requests:
                session.add(
                    ResourceTask(
                        id=entity_id("resource_task"),
                        space_id=space.id,
                        content_id=content_id,
                        source_run_id=run.id,
                        request_key=request.key,
                        request=request.model_dump(mode="json"),
                        status="pending",
                    )
                )
            return content_id
        if isinstance(proposal, ReassessmentProposal):
            return cls.save_content(
                session,
                space,
                node,
                previous.blocks,
                source,
                proposal.decision,
                content_profile=previous.profile_revision,
                review_status=previous.review_status,
            )
        raise ServiceError("capability_limit", "publication unavailable for this operation")

    @classmethod
    def edit_profile(cls, session: Session, space: Space, edit: ProfileEdit) -> dict[str, Any]:
        if space.profile_revision != edit.expected_revision:
            raise ServiceError("version_conflict", "profile edit uses obsolete revision", 409)
        previous = cls.profile(session, space)
        single = {"topic", "primary_purpose", "background"}
        multiple = {
            "secondary_purposes",
            "target_abilities",
            "included_scope",
            "excluded_scope",
            "constraints",
        }
        proposal = dict(previous.proposal)
        edit_id = entity_id("edit")
        for field, value in edit.fields.items():
            if field not in single | multiple or (field in single and isinstance(value, list)):
                raise ServiceError("invalid_input", "invalid profile field")
            if field in multiple and value is not None and not isinstance(value, list):
                raise ServiceError(
                    "invalid_input", "profile scope and ability fields require lists"
                )

            def fact(text: str | None) -> dict[str, Any]:
                return {
                    "value": str(sanitized(text)) if text else "尚未确定",
                    "source": "user" if text else "unknown",
                    "evidence_keys": [edit_id] if text else [],
                }

            if field in multiple:
                assert value is None or isinstance(value, list)
                proposal[field] = [fact(text) for text in value or []]
            else:
                assert value is None or isinstance(value, str)
                proposal[field] = fact(value)
        try:
            validated = LearningProfileProposal.model_validate(proposal)
        except ValidationError as exc:
            raise ServiceError(
                "invalid_input", "profile fields exceed limits or required scope is empty"
            ) from exc
        space.profile_revision += 1
        profile = ProfileRevision(
            id=entity_id("profile"),
            space_id=space.id,
            revision=space.profile_revision,
            proposal=validated.model_dump(mode="json"),
            evidence={
                "previous_revision": previous.revision,
                "edit_id": edit_id,
                "fields": sanitized(edit.fields),
                "previous_evidence": previous.evidence,
            },
        )
        session.add(profile)
        return {"revision": profile.revision, "profile": profile.proposal}
