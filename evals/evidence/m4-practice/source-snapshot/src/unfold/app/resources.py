"""Outbox dispatch, actual raster acquisition and version-qualified file publication."""

from __future__ import annotations

import time
from collections.abc import Callable
from decimal import Decimal
from typing import Any, Protocol

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from unfold.api_contract import CreateRun, LearningRunSnapshot
from unfold.app.files import FileStore, StoredFile
from unfold.app.generation import Generator
from unfold.app.images import CommonsImages
from unfold.app.models import (
    ContentRevision,
    GenerationRun,
    OutlineItem,
    ProfileRevision,
    ResourceCandidate,
    ResourceRevision,
    ResourceTask,
    Space,
    ToolInvocation,
)
from unfold.app.practice import content_text
from unfold.app.service import Claim, RunService, ServiceError, database_clock, entity_id
from unfold.contracts import ResourceProposal, ResourceRequest
from unfold.provider import GenerationError


class ImageSource(Protocol):
    def search(self, query: str) -> list[dict[str, Any]]: ...
    def download(self, candidate: dict[str, Any]) -> bytes: ...
    def close(self) -> None: ...


class Resources:
    @staticmethod
    def task(session: Session, task_id: str, space_id: str) -> ResourceTask:
        task = session.scalar(
            select(ResourceTask)
            .where(
                ResourceTask.id == task_id,
                ResourceTask.space_id == space_id,
            )
            .with_for_update(key_share=True)
            .execution_options(populate_existing=True)
        )
        if task is None:
            raise ServiceError("invalid_input", "resource task not found", 404)
        return task

    @staticmethod
    def current(session: Session, space: Space, task: ResourceTask) -> ContentRevision:
        content = session.get(ContentRevision, task.content_id)
        assert content
        node = session.get(OutlineItem, content.node_id)
        source = session.get(GenerationRun, task.source_run_id)
        if (
            not node
            or not node.active
            or node.revision != content.revision
            or not source
            or source.profile_revision != space.profile_revision
        ):
            raise ServiceError("version_conflict", "resource teaching dependencies changed", 409)
        return content

    @classmethod
    def enqueue(
        cls, service: RunService, session: Session, space: Space, task: ResourceTask
    ) -> LearningRunSnapshot:
        content = cls.current(session, space, task)
        if task.request["kind"] != "image":
            raise ServiceError("capability_limit", "only sourced raster images available", 409)
        profile = session.scalar(
            select(ProfileRevision).where(
                ProfileRevision.space_id == space.id,
                ProfileRevision.revision == space.profile_revision,
            )
        )
        assert profile
        task.dispatch_revision += 1
        session.flush()
        raw = {
            "schema_version": "resource-input.v1",
            "profile": {"revision": space.profile_revision, "profile": profile.proposal},
            "content": {"revision_ref": content.id, "text": content_text(content)},
            "request": task.request,
            "supported_kinds": ["image"],
            "offered_candidate_refs": [],
        }
        request = CreateRun(
            schema_version="api.v1",
            space_id=space.id,
            operation="resource",
            profile_revision=space.profile_revision,
            base_revision=content.revision,
            input=raw,
        )
        snapshot = service.enqueue(
            session, space, request, f"resource:{task.id}:{task.dispatch_revision}"
        )
        task.status = "dispatched"
        task.error = None
        task.dispatched_run_id = snapshot.run_id
        return snapshot

    @classmethod
    def dispatch(cls, service: RunService) -> int:
        with service.database.transaction() as session:
            session.execute(text("SELECT pg_advisory_xact_lock(180001)"))
            task_id = session.scalar(
                select(ResourceTask.id)
                .where(
                    ResourceTask.status == "pending",
                )
                .order_by(ResourceTask.created_at, ResourceTask.id)
                .limit(1)
            )
            if not task_id:
                return 0
            space_id = session.scalar(
                select(ResourceTask.space_id).where(ResourceTask.id == task_id)
            )
            assert space_id
            space = service.require_space(session, space_id, lock=True)
            task = cls.task(session, task_id, space_id)
            try:
                # A capacity rejection rolls back the dispatch counter too.
                with session.begin_nested():
                    cls.enqueue(service, session, space, task)
            except ServiceError as exc:
                if exc.http_status == 429:
                    return 0
                task.status = "failed"
                task.error = {"code": exc.code, "message": str(exc)}
            return 1

    @classmethod
    def retry(cls, service: RunService, space_id: str, task_id: str) -> LearningRunSnapshot:
        with service.database.transaction() as session:
            session.execute(text("SELECT pg_advisory_xact_lock(180001)"))
            space = service.require_space(session, space_id, lock=True)
            task = cls.task(session, task_id, space_id)
            run = (
                session.get(GenerationRun, task.dispatched_run_id)
                if task.dispatched_run_id
                else None
            )
            if run and run.status in ("queued", "running"):
                return service.snapshot(run)
            if run and run.status == "succeeded":
                revision = session.get(ResourceRevision, run.result_ref)
                assert revision
                candidate = session.get(ResourceCandidate, revision.candidate_id)
                assert candidate
                if FileStore(service.settings.data_directory).verify(StoredFile(**candidate.file)):
                    return service.snapshot(run)
            return cls.enqueue(service, session, space, task)

    @classmethod
    def verify_input(cls, session: Session, space: Space, request: CreateRun) -> dict[str, Any]:
        raw = request.input
        task = session.scalar(
            select(ResourceTask).where(
                ResourceTask.space_id == space.id,
                ResourceTask.content_id == raw["content"]["revision_ref"],
                ResourceTask.request_key == raw["request"]["key"],
            )
        )
        if task is None:
            raise ServiceError("invalid_input", "resource input requires a saved content request")
        content = cls.current(session, space, task)
        if (
            raw["request"] != task.request
            or raw["content"]["text"] != content_text(content)
            or request.base_revision != content.revision
            or raw["supported_kinds"] != ["image"]
            or raw["offered_candidate_refs"] != []
        ):
            raise ServiceError(
                "version_conflict", "resource snapshot differs from saved request", 409
            )
        return {
            "resource_task_id": task.id,
            "resource_dispatch_revision": task.dispatch_revision,
            "node_revisions": {content.node_id: content.revision},
            "selected_context": {},
            "context_strategy": "resource-content.v1",
        }

    @staticmethod
    def publish(
        service: RunService, session: Session, run: GenerationRun, proposal: ResourceProposal
    ) -> str:
        task = session.get(ResourceTask, run.context_dependencies["resource_task_id"])
        assert task
        if task.dispatched_run_id != run.id:
            raise ServiceError(
                "version_conflict", "a newer resource run replaced this attempt", 409
            )
        candidate = session.scalar(
            select(ResourceCandidate)
            .where(
                ResourceCandidate.run_id == run.id,
            )
            .order_by(ResourceCandidate.created_at.desc(), ResourceCandidate.id.desc())
            .limit(1)
        )
        if (
            candidate is None
            or candidate.proposal != proposal.model_dump(mode="json")
            or not FileStore(service.settings.data_directory).verify(StoredFile(**candidate.file))
        ):
            raise ServiceError("domain_validation", "resource candidate has no valid stored file")
        revision = (
            session.scalar(
                select(func.max(ResourceRevision.revision)).where(
                    ResourceRevision.task_id == task.id,
                )
            )
            or 0
        ) + 1
        saved = ResourceRevision(
            id=entity_id("resource"), task_id=task.id, revision=revision, candidate_id=candidate.id
        )
        session.add(saved)
        return saved.id

    @staticmethod
    def read(session: Session, content_id: str, store: FileStore) -> list[dict[str, Any]]:
        seen: set[str] = set()
        while content_id not in seen:
            seen.add(content_id)
            if session.scalar(
                select(ResourceTask.id).where(ResourceTask.content_id == content_id).limit(1)
            ):
                break
            content = session.get(ContentRevision, content_id)
            carried = content.source.get("content_carried_from") if content else None
            if not isinstance(carried, str):
                break
            content_id = carried
        result = []
        for task in session.scalars(
            select(ResourceTask)
            .where(ResourceTask.content_id == content_id)
            .order_by(ResourceTask.created_at, ResourceTask.id)
        ):
            run = (
                session.get(GenerationRun, task.dispatched_run_id)
                if task.dispatched_run_id
                else None
            )
            state = run.status if run else task.status
            content = session.get(ContentRevision, task.content_id)
            node = session.get(OutlineItem, content.node_id) if content else None
            space = session.get(Space, task.space_id)
            origin = session.get(GenerationRun, task.source_run_id)
            can_retry = bool(
                content
                and node
                and node.active
                and node.revision == content.revision
                and space
                and origin
                and origin.profile_revision == space.profile_revision
            )
            versions = []
            for revision in session.scalars(
                select(ResourceRevision)
                .where(
                    ResourceRevision.task_id == task.id,
                )
                .order_by(ResourceRevision.revision.desc())
            ):
                candidate = session.get(ResourceCandidate, revision.candidate_id)
                assert candidate
                stored = StoredFile(**candidate.file)
                exists = store.verify(stored)
                versions.append(
                    {
                        "id": revision.id,
                        "revision": revision.revision,
                        "status": "ready" if exists else "missing",
                        "mime": stored.mime,
                        "size": stored.size,
                        "width": stored.width,
                        "height": stored.height,
                        "digest": stored.digest,
                        "file_url": f"/api/v1/resources/{revision.id}/file" if exists else None,
                        "source": candidate.source,
                    }
                )
            if state == "succeeded":
                state = str(versions[0]["status"]) if versions else "missing"
            result.append(
                {
                    "id": task.id,
                    "request_key": task.request_key,
                    "request": task.request,
                    "status": state,
                    "run_id": task.dispatched_run_id,
                    "error": run.error if run else task.error,
                    "versions": versions,
                    "can_retry": can_retry,
                    "content_origin_id": task.content_id,
                }
            )
        return result


class ImageExecutor:
    def __init__(self, service: RunService, source: Callable[[], ImageSource] = CommonsImages):
        self.service = service
        self.source = source

    def generate(self, claim: Claim) -> dict[str, Any]:
        store = FileStore(self.service.settings.data_directory)
        with self.service.database.transaction() as session:
            existing = session.scalar(
                select(ResourceCandidate)
                .where(ResourceCandidate.run_id == claim.run_id)
                .order_by(ResourceCandidate.created_at.desc())
                .limit(1)
            )
            if existing and store.verify(StoredFile(**existing.file)):
                invocation = session.scalar(
                    select(ToolInvocation).where(
                        ToolInvocation.run_id == claim.run_id,
                        ToolInvocation.attempt == claim.attempt,
                        ToolInvocation.step == 1,
                    )
                )
                if invocation is None:
                    session.add(
                        ToolInvocation(
                            id=entity_id("tool"),
                            run_id=claim.run_id,
                            attempt=claim.attempt,
                            step=1,
                            tool="stored_image_candidate.v1",
                            status="completed",
                            input={"reuse_candidate": existing.id},
                            output={"candidate_id": existing.id, "network_call": False},
                            outcome_unknown=False,
                        )
                    )
                return existing.proposal
        if not self.service.heartbeat(claim):
            raise GenerationError("cancelled", "resource attempt no longer eligible")
        invocation_id = entity_id("tool")
        with self.service.database.transaction() as session:
            run = session.scalar(
                select(GenerationRun)
                .where(GenerationRun.id == claim.run_id)
                .with_for_update(key_share=True)
            )
            if not self.service.eligible(run, claim, database_clock(session)):
                raise GenerationError("cancelled", "resource attempt no longer eligible")
            session.add(
                ToolInvocation(
                    id=invocation_id,
                    run_id=claim.run_id,
                    attempt=claim.attempt,
                    step=1,
                    tool="commons_raster_pipeline.v1",
                    status="started",
                    input=claim.input,
                    output=None,
                    outcome_unknown=False,
                )
            )
        started = time.monotonic()
        source = self.source()
        try:
            request = ResourceRequest.model_validate(claim.input["request"])
            candidates = source.search(request.specification[:200])
            if not candidates:
                raise GenerationError("capability_limit", "no licensed raster candidate found")
            candidate = candidates[0]
            stored = store.write_image(source.download(candidate))
            proposal = ResourceProposal(
                schema_version="resource.v1",
                request_key=request.key,
                strategy="retrieve",
                candidate_ref=None,
                specification="已获取具备来源记录的图片；教学适配尚待核对。",
                limitation=None,
            ).model_dump(mode="json")
            with self.service.database.transaction() as session:
                saved = ResourceCandidate(
                    id=entity_id("candidate"),
                    run_id=claim.run_id,
                    attempt=claim.attempt,
                    file=stored.dump(),
                    source=candidate,
                    proposal=proposal,
                )
                session.add(saved)
                invocation = session.get(ToolInvocation, invocation_id)
                assert invocation
                invocation.status = "completed"
                invocation.latency_seconds = Decimal(str(time.monotonic() - started))
                invocation.output = {
                    "candidate_id": saved.id,
                    "source_mode": getattr(source, "provenance", "live_retrieval"),
                    "file": stored.dump(),
                    "source": candidate,
                }
            return proposal
        except GenerationError as exc:
            with self.service.database.transaction() as session:
                invocation = session.get(ToolInvocation, invocation_id)
                assert invocation
                invocation.status = "failed"
                invocation.output = {"code": exc.code, "message": str(exc)}
                invocation.latency_seconds = Decimal(str(time.monotonic() - started))
            raise
        finally:
            source.close()


class ApplicationGenerator:
    resources_enabled = True

    def __init__(self, content: Generator, images: ImageExecutor):
        self.content = content
        self.images = images

    def generate(self, claim: Claim) -> dict[str, Any]:
        return (
            self.images.generate(claim)
            if claim.operation == "resource"
            else self.content.generate(claim)
        )
