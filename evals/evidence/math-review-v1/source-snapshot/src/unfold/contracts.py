"""Versioned model proposals, inputs and deterministic domain checks.

All provider output fields are required. Server IDs, publication, URLs and
verification claims are deliberately absent from proposals.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=6000)]
Key = Annotated[str, StringConstraints(pattern=r"^[a-zA-Z0-9_.:-]{1,80}$")]
Revision = Annotated[int, Field(ge=1)]
ChoiceMode = Literal["single", "multiple"]
Target = Literal["purpose", "background", "goal", "scope", "constraints"]
ResourceKind = Literal["image", "diagram", "playground", "interactive"]

MAX_QUESTIONS = 5
MAX_ROUNDS = 2
DEFAULT_EXERCISES = 3
MAX_CHILDREN = 8


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


def unique(values: Sequence[str], label: str) -> None:
    if len(set(values)) != len(values):
        raise ValueError(f"duplicate {label}")


class Option(Contract):
    key: Key
    text: Text


class Question(Contract):
    key: Key
    target: Target
    prompt: Text
    mode: ChoiceMode
    options: Annotated[list[Option], Field(min_length=2, max_length=6)]
    allow_free_text: Literal[True]
    allow_unknown: Literal[True]

    @model_validator(mode="after")
    def check_options(self) -> Self:
        unique([o.key for o in self.options], "option keys")
        return self


class QuestionnaireProposal(Contract):
    schema_version: Literal["questionnaire.v1"]
    questions: Annotated[list[Question], Field(max_length=MAX_QUESTIONS)]
    rationale: Text

    @model_validator(mode="after")
    def check_questions(self) -> Self:
        unique([q.key for q in self.questions], "question keys")
        unique([q.target for q in self.questions], "question targets")
        return self


class Answer(Contract):
    question_key: Key
    selected_keys: list[Key]
    free_text: Text | None
    unknown: bool

    @model_validator(mode="after")
    def check_answer(self) -> Self:
        unique(self.selected_keys, "selected keys")
        if self.unknown and (self.selected_keys or self.free_text):
            raise ValueError("unknown answer cannot also contain selections or text")
        if not self.unknown and not (self.selected_keys or self.free_text):
            raise ValueError("answer requires selections, text or explicit unknown")
        return self


class Fact(Contract):
    value: Text
    source: Literal["user", "assumption", "unknown"]
    evidence_keys: list[Key]

    @model_validator(mode="after")
    def check_source(self) -> Self:
        unique(self.evidence_keys, "evidence keys")
        if self.source == "user" and not self.evidence_keys:
            raise ValueError("user fact requires original evidence")
        if self.source != "user" and self.evidence_keys:
            raise ValueError("assumption/unknown cannot claim user evidence")
        return self


class LearningProfileProposal(Contract):
    schema_version: Literal["profile.v1"]
    topic: Fact
    primary_purpose: Fact
    secondary_purposes: list[Fact]
    background: Fact
    target_abilities: Annotated[list[Fact], Field(min_length=1, max_length=8)]
    included_scope: Annotated[list[Fact], Field(min_length=1, max_length=12)]
    excluded_scope: Annotated[list[Fact], Field(max_length=12)]
    constraints: Annotated[list[Fact], Field(max_length=8)]

    def facts(self) -> list[Fact]:
        return [
            self.topic,
            self.primary_purpose,
            self.background,
            *self.secondary_purposes,
            *self.target_abilities,
            *self.included_scope,
            *self.excluded_scope,
            *self.constraints,
        ]


class Decision(Contract):
    state: Literal["expandable", "leaf"]
    reason: Text


class NodeCandidate(Contract):
    key: Key
    title: Text
    kind: Literal["concept", "group"]
    concept_ref: Key | None
    scope: Text
    overview: Text
    decision: Decision

    @model_validator(mode="after")
    def check_kind(self) -> Self:
        if self.kind == "group" and self.concept_ref is not None:
            raise ValueError("teaching group has no concept identity")
        return self


class Relation(Contract):
    source_key: Key
    target_key: Key
    kind: Literal["related_to", "prerequisite_of", "contrasted_with"]
    explanation: Text


def check_layer(
    parent_key: str, decision: Decision, children: list[NodeCandidate], relations: list[Relation]
) -> None:
    unique([parent_key, *[c.key for c in children]], "node keys")
    if decision.state == "leaf" and children:
        raise ValueError("leaf cannot have proposed children")
    if decision.state == "expandable" and not children:
        raise ValueError("successful expansion requires at least one child")
    keys = {parent_key, *[c.key for c in children]}
    graph: dict[str, list[str]] = {key: [] for key in keys}
    unique([f"{r.source_key}/{r.kind}/{r.target_key}" for r in relations], "relations")
    for relation in relations:
        if relation.source_key not in keys or relation.target_key not in keys:
            raise ValueError("relation references an unknown local node")
        if relation.source_key == relation.target_key:
            raise ValueError("self relation is not allowed")
        if relation.kind == "prerequisite_of":
            graph[relation.source_key].append(relation.target_key)
    visited: set[str] = set()
    active: set[str] = set()

    def visit(key: str) -> None:
        if key in active:
            raise ValueError("prerequisite cycle")
        if key in visited:
            return
        active.add(key)
        for target in graph[key]:
            visit(target)
        active.remove(key)
        visited.add(key)

    for key in keys:
        visit(key)


class OutlineProposal(Contract):
    schema_version: Literal["outline.v1"]
    root: NodeCandidate
    division_basis: Text
    children: Annotated[list[NodeCandidate], Field(max_length=MAX_CHILDREN)]
    relations: Annotated[list[Relation], Field(max_length=24)]

    @model_validator(mode="after")
    def check_tree(self) -> Self:
        check_layer(self.root.key, self.root.decision, self.children, self.relations)
        return self


class ExpansionProposal(Contract):
    schema_version: Literal["expansion.v1"]
    target_key: Key
    decision: Decision
    division_basis: Text
    children: Annotated[list[NodeCandidate], Field(max_length=MAX_CHILDREN)]
    relations: Annotated[list[Relation], Field(max_length=24)]

    @model_validator(mode="after")
    def check_tree(self) -> Self:
        check_layer(self.target_key, self.decision, self.children, self.relations)
        return self


class ReassessmentProposal(Contract):
    schema_version: Literal["reassessment.v1"]
    target_key: Key
    decision: Decision
    restructuring_note: Text | None


class ResourceRequest(Contract):
    key: Key
    kind: ResourceKind
    purpose: Text
    specification: Text
    runtime: Literal["web"] | None
    controls: list[Literal["play", "pause", "step", "reset", "parameters", "run", "stop"]]
    fallback_text: Text

    @model_validator(mode="after")
    def check_runtime(self) -> Self:
        unique(self.controls, "controls")
        if self.kind in ("playground", "interactive"):
            if self.runtime != "web" or "reset" not in self.controls:
                raise ValueError("web resources require runtime and reset")
        elif self.runtime is not None or self.controls:
            raise ValueError("static resource has no runtime or controls")
        return self


class MarkdownBlock(Contract):
    type: Literal["markdown"]
    text: Text


class DiagramBlock(Contract):
    type: Literal["diagram"]
    language: Literal["mermaid"]
    source: Text
    caption: Text
    fallback_text: Text


class ResourceBlock(Contract):
    type: Literal["image", "playground", "interactive"]
    resource_key: Key
    caption: Text
    fallback_text: Text


class ExerciseBlock(Contract):
    type: Literal["exercise"]
    exercise_revision_ref: Key
    fallback_text: Text


class ExplanationProposal(Contract):
    schema_version: Literal["explanation.v1"]
    target_key: Key
    overview: Text
    blocks: Annotated[
        list[MarkdownBlock | DiagramBlock | ResourceBlock | ExerciseBlock],
        Field(min_length=1, max_length=20),
    ]
    resource_requests: Annotated[list[ResourceRequest], Field(max_length=6)]

    @model_validator(mode="after")
    def check_resources(self) -> Self:
        unique([r.key for r in self.resource_requests], "resource keys")
        resources = {r.key: r for r in self.resource_requests}
        referenced = set()
        for block in self.blocks:
            if isinstance(block, ResourceBlock):
                request = resources.get(block.resource_key)
                if request is None or request.kind != block.type:
                    raise ValueError("resource block must match a declared request")
                referenced.add(block.resource_key)
        if referenced != set(resources):
            raise ValueError("unreferenced resource request")
        return self


class Exercise(Contract):
    key: Key
    mode: ChoiceMode
    stem: Text
    testing_point: Text
    content_refs: Annotated[list[Key], Field(min_length=1)]
    options: Annotated[list[Option], Field(min_length=2, max_length=6)]
    correct_keys: Annotated[list[Key], Field(min_length=1)]
    explanation: Text

    @model_validator(mode="after")
    def check_answers(self) -> Self:
        unique([o.key for o in self.options], "option keys")
        unique(self.correct_keys, "answer keys")
        unique(self.content_refs, "content refs")
        if not set(self.correct_keys) <= {o.key for o in self.options}:
            raise ValueError("answer references unknown option")
        if self.mode == "single" and len(self.correct_keys) != 1:
            raise ValueError("single choice requires exactly one answer")
        return self


class ExerciseSetProposal(Contract):
    schema_version: Literal["exercises.v1"]
    target_key: Key
    exercises: Annotated[list[Exercise], Field(min_length=1, max_length=5)]

    @model_validator(mode="after")
    def check_exercises(self) -> Self:
        unique([q.key for q in self.exercises], "exercise keys")
        return self


class ResourceProposal(Contract):
    schema_version: Literal["resource.v1"]
    request_key: Key
    strategy: Literal["reuse", "retrieve", "draw", "build", "unsupported"]
    candidate_ref: Key | None
    specification: Text
    limitation: Text | None

    @model_validator(mode="after")
    def check_strategy(self) -> Self:
        if self.strategy == "reuse" and self.candidate_ref is None:
            raise ValueError("reuse requires an actual offered candidate")
        if self.strategy != "reuse" and self.candidate_ref is not None:
            raise ValueError("only reuse can name an existing candidate")
        if self.strategy == "unsupported" and self.limitation is None:
            raise ValueError("unsupported requires a reason")
        if self.strategy != "unsupported" and self.limitation is not None:
            raise ValueError("supported proposal cannot declare capability failure")
        return self


class QuestionnaireInput(Contract):
    schema_version: Literal["questionnaire-input.v1"]
    domain: Text
    user_brief: Text
    known_targets: list[Target]
    previous_question_keys: list[Key]
    questions_asked: Annotated[int, Field(ge=0, le=MAX_QUESTIONS)]
    round: Annotated[int, Field(ge=1, le=MAX_ROUNDS)]


class DomainQuestionnaireInput(QuestionnaireInput):
    schema_version: Literal["questionnaire-input.v2"]  # type: ignore[assignment]
    user_brief: Annotated[str, Field(max_length=6000)]


def check_answers(questionnaire: QuestionnaireProposal, answers: list[Answer]) -> None:
    unique([a.question_key for a in answers], "answer question keys")
    questions = {q.key: q for q in questionnaire.questions}
    if set(questions) != {a.question_key for a in answers}:
        raise ValueError("exactly one answer per question required")
    for answer in answers:
        question = questions[answer.question_key]
        if not set(answer.selected_keys) <= {o.key for o in question.options}:
            raise ValueError("unknown selected option")
        if question.mode == "single" and len(answer.selected_keys) > 1:
            raise ValueError("single choice intake cannot select multiple options")


class ProfileInput(Contract):
    schema_version: Literal["profile-input.v1"]
    domain: Text
    user_brief: Text
    questionnaire: QuestionnaireProposal
    answers: list[Answer]

    @model_validator(mode="after")
    def check_references(self) -> Self:
        check_answers(self.questionnaire, self.answers)
        return self


class IntakeRound(Contract):
    questionnaire: QuestionnaireProposal
    answers: list[Answer]

    @model_validator(mode="after")
    def check_references(self) -> Self:
        check_answers(self.questionnaire, self.answers)
        return self


class ProfileHistoryInput(Contract):
    """v2 retains distinct rounds; v1 continues to mean one questionnaire."""

    schema_version: Literal["profile-input.v2"]
    domain: Text
    user_brief: Text
    rounds: Annotated[list[IntakeRound], Field(min_length=1, max_length=MAX_ROUNDS)]

    @property
    def answers(self) -> list[Answer]:
        return [answer for item in self.rounds for answer in item.answers]

    @model_validator(mode="after")
    def check_history(self) -> Self:
        keys = [q.key for item in self.rounds for q in item.questionnaire.questions]
        unique(keys, "question keys across rounds")
        if len(keys) > MAX_QUESTIONS:
            raise ValueError("question budget exceeded")
        return self


class DomainProfileInput(ProfileHistoryInput):
    schema_version: Literal["profile-input.v3"]  # type: ignore[assignment]
    user_brief: Annotated[str, Field(max_length=6000)]


class ProfileSnapshot(Contract):
    revision: Revision
    profile: LearningProfileProposal


class OutlineInput(Contract):
    schema_version: Literal["outline-input.v1"]
    profile: ProfileSnapshot
    reusable_concept_refs: list[Key]


class NodeSnapshot(Contract):
    key: Key
    title: Text
    scope: Text
    overview: Text
    decision: Decision
    profile_revision: Revision
    content_revision: Revision


class NodeContext(Contract):
    profile: ProfileSnapshot
    target: NodeSnapshot
    path: Annotated[list[NodeSnapshot], Field(min_length=1)]
    existing_children: list[NodeSnapshot]
    sibling_scopes: list[Text]
    reusable_concept_refs: list[Key]

    @model_validator(mode="after")
    def check_path(self) -> Self:
        unique([n.key for n in self.path], "path keys")
        if self.path[-1] != self.target:
            raise ValueError("path must end at the exact target snapshot")
        unique([self.target.key, *[n.key for n in self.existing_children]], "existing keys")
        return self


class ExpansionInput(NodeContext):
    schema_version: Literal["expansion-input.v1"]
    operation: Literal["expand", "regenerate_expansion"]

    @model_validator(mode="after")
    def check_expandable(self) -> Self:
        if self.target.profile_revision != self.profile.revision:
            raise ValueError("stale decision requires reassessment")
        if self.target.decision.state == "leaf":
            raise ValueError("ordinary expansion and regeneration are forbidden on leaf")
        if self.existing_children and self.operation == "expand":
            raise ValueError("existing expansion must be read or explicitly regenerated")
        return self


class ReassessmentInput(NodeContext):
    schema_version: Literal["reassessment-input.v1"]
    feedback: Text


class ExplanationInput(NodeContext):
    schema_version: Literal["explanation-input.v1"]
    instruction: Text
    allowed_resource_kinds: list[ResourceKind]
    available_exercise_refs: list[Key]


class ContentSnapshot(Contract):
    revision_ref: Key
    text: Text


class ExerciseFields(NodeContext):
    schema_version: Literal["exercises-input.v1", "exercises-input.v2"]
    provided_content: Annotated[list[ContentSnapshot], Field(min_length=1)]
    allowed_modes: Annotated[list[ChoiceMode], Field(min_length=1)]
    count: Annotated[int, Field(ge=1, le=5)]

    @model_validator(mode="after")
    def check_content(self) -> Self:
        unique([c.revision_ref for c in self.provided_content], "provided content refs")
        unique(self.allowed_modes, "allowed modes")
        return self


class ExerciseInput(ExerciseFields):
    schema_version: Literal["exercises-input.v1"]


class GuidedExerciseInput(ExerciseFields):
    schema_version: Literal["exercises-input.v2"]
    instruction: Text
    required_modes: list[ChoiceMode]

    @model_validator(mode="after")
    def check_required_modes(self) -> Self:
        unique(self.required_modes, "required modes")
        if (
            not set(self.required_modes) <= set(self.allowed_modes)
            or len(self.required_modes) > self.count
        ):
            raise ValueError("required exercise modes must fit capability and count")
        return self


class ResourceInput(Contract):
    schema_version: Literal["resource-input.v1"]
    profile: ProfileSnapshot
    content: ContentSnapshot
    request: ResourceRequest
    supported_kinds: list[ResourceKind]
    offered_candidate_refs: list[Key]


Operation = Literal[
    "questionnaire",
    "profile",
    "outline",
    "expansion",
    "reassessment",
    "explanation",
    "exercises",
    "resource",
]
INPUTS: dict[str, type[Contract]] = {
    "questionnaire": QuestionnaireInput,
    "profile": ProfileInput,
    "outline": OutlineInput,
    "expansion": ExpansionInput,
    "reassessment": ReassessmentInput,
    "explanation": ExplanationInput,
    "exercises": ExerciseInput,
    "resource": ResourceInput,
}
OUTPUTS: dict[str, type[Contract]] = {
    "questionnaire": QuestionnaireProposal,
    "profile": LearningProfileProposal,
    "outline": OutlineProposal,
    "expansion": ExpansionProposal,
    "reassessment": ReassessmentProposal,
    "explanation": ExplanationProposal,
    "exercises": ExerciseSetProposal,
    "resource": ResourceProposal,
}


def parse_input(operation: str, raw_input: object) -> Contract:
    if isinstance(raw_input, dict):
        if (
            operation == "questionnaire"
            and raw_input.get("schema_version") == "questionnaire-input.v2"
        ):
            return DomainQuestionnaireInput.model_validate(raw_input)
        if operation == "profile" and raw_input.get("schema_version") == "profile-input.v3":
            return DomainProfileInput.model_validate(raw_input)
    if (
        operation == "exercises"
        and isinstance(raw_input, dict)
        and raw_input.get("schema_version") == "exercises-input.v2"
    ):
        return GuidedExerciseInput.model_validate(raw_input)
    if (
        operation == "profile"
        and isinstance(raw_input, dict)
        and raw_input.get("schema_version") == "profile-input.v2"
    ):
        return ProfileHistoryInput.model_validate(raw_input)
    return INPUTS[operation].model_validate(raw_input)


def validate_pair(operation: str, raw_input: object, raw_output: object) -> Contract:
    """Validate structure AND request-specific references; does not publish."""
    request = parse_input(operation, raw_input)
    proposal = OUTPUTS[operation].model_validate(raw_output)
    if isinstance(request, QuestionnaireInput) and isinstance(proposal, QuestionnaireProposal):
        if len(proposal.questions) + request.questions_asked > MAX_QUESTIONS:
            raise ValueError("question budget exceeded")
        if {q.key for q in proposal.questions} & set(request.previous_question_keys):
            raise ValueError("question key reused across rounds")
        if {q.target for q in proposal.questions} & set(request.known_targets):
            raise ValueError("already stated target must not be asked again")
    if isinstance(request, ProfileInput | ProfileHistoryInput) and isinstance(
        proposal, LearningProfileProposal
    ):
        evidence = {
            "brief",
            *[f"answer:{a.question_key}" for a in request.answers if not a.unknown],
        }
        if any(not set(f.evidence_keys) <= evidence for f in proposal.facts()):
            raise ValueError("profile references unavailable user evidence")
    if isinstance(request, NodeContext):
        if getattr(proposal, "target_key", None) != request.target.key:
            raise ValueError("proposal target does not match request")
    if isinstance(request, OutlineInput) and isinstance(proposal, OutlineProposal):
        nodes = [proposal.root, *proposal.children]
        check_concept_refs(nodes, request.reusable_concept_refs)
    if isinstance(request, ExpansionInput) and isinstance(proposal, ExpansionProposal):
        check_concept_refs(proposal.children, request.reusable_concept_refs)
        if {c.key for c in proposal.children} & {n.key for n in request.path}:
            raise ValueError("child key aliases ancestor")
        if request.existing_children and proposal.decision.state == "leaf":
            raise ValueError("existing subtree requires explicit restructuring review")
    if isinstance(request, ReassessmentInput) and isinstance(proposal, ReassessmentProposal):
        if request.existing_children and proposal.decision.state == "leaf":
            if proposal.restructuring_note is None:
                raise ValueError("leaf reassessment of existing subtree requires review note")
    if isinstance(request, ExplanationInput) and isinstance(proposal, ExplanationProposal):
        if not {r.kind for r in proposal.resource_requests} <= set(request.allowed_resource_kinds):
            raise ValueError("unsupported resource kind")
        for block in proposal.blocks:
            if isinstance(block, ExerciseBlock):
                if block.exercise_revision_ref not in request.available_exercise_refs:
                    raise ValueError("unknown exercise revision")
    if isinstance(request, ExerciseFields) and isinstance(proposal, ExerciseSetProposal):
        refs = {c.revision_ref for c in request.provided_content}
        if len(proposal.exercises) != request.count:
            raise ValueError("exercise count does not match request")
        for exercise in proposal.exercises:
            if not set(exercise.content_refs) <= refs or exercise.mode not in request.allowed_modes:
                raise ValueError("exercise uses unavailable content or type")
        if isinstance(request, GuidedExerciseInput) and not set(request.required_modes) <= {
            q.mode for q in proposal.exercises
        }:
            raise ValueError("requested exercise mode mix is missing")
    if isinstance(request, ResourceInput) and isinstance(proposal, ResourceProposal):
        if proposal.request_key != request.request.key:
            raise ValueError("resource request mismatch")
        if proposal.candidate_ref is not None:
            if proposal.candidate_ref not in request.offered_candidate_refs:
                raise ValueError("invented resource candidate")
        if request.request.kind not in request.supported_kinds:
            if proposal.strategy != "unsupported":
                raise ValueError("unsupported resource must exit with limitation")
    return proposal


def check_concept_refs(nodes: list[NodeCandidate], allowed: list[str]) -> None:
    if any(n.concept_ref is not None and n.concept_ref not in allowed for n in nodes):
        raise ValueError("concept reuse references an unavailable identity")


def grade(exercise: Exercise, selected_keys: list[str]) -> bool:
    unique(selected_keys, "submitted answer keys")
    if not set(selected_keys) <= {o.key for o in exercise.options}:
        raise ValueError("unknown submitted option")
    if exercise.mode == "single" and len(selected_keys) != 1:
        raise ValueError("single choice requires one selection")
    return set(selected_keys) == set(exercise.correct_keys)
