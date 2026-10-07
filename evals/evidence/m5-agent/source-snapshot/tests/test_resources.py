from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from test_learning import seed

from unfold.app.api import create_app
from unfold.app.db import Database
from unfold.app.files import FileStore, StoredFile
from unfold.app.fixtures import CachedCommons
from unfold.app.generation import FixtureGenerator
from unfold.app.images import CommonsImages, image_url
from unfold.app.learning import LearningAction
from unfold.app.models import ResourceCandidate, ResourceRevision, ResourceTask
from unfold.app.resources import ApplicationGenerator, ImageExecutor, Resources
from unfold.app.service import RunService, ServiceError
from unfold.app.worker import Worker
from unfold.provider import GenerationError

ROOT = Path(__file__).resolve().parents[1]


def resource_setup(database: Database, directory: Path) -> tuple[RunService, str, str, str]:
    original, space_id = seed(database)
    service = RunService(
        database, replace(original.settings, data_directory=directory, retry_base_seconds=0)
    )
    node_id = service.get_space(space_id)["root_id"]
    run = service.create_action(
        space_id,
        LearningAction(
            operation="explanation",
            target_id=node_id,
            expected_profile_revision=1,
            expected_base_revision=1,
        ),
        "with-image",
    )
    claim = service.claim(run.run_id)
    assert claim
    proposal = FixtureGenerator(database, ROOT).generate(claim)
    proposal["resource_requests"] = [
        {
            "key": "photo",
            "kind": "image",
            "purpose": "说明流程",
            "specification": "binary search",
            "runtime": None,
            "controls": [],
            "fallback_text": "按正文理解流程",
        }
    ]
    proposal["blocks"].append(
        {
            "type": "image",
            "resource_key": "photo",
            "caption": "二分查找示意图",
            "fallback_text": "图片失败时正文仍可阅读",
        }
    )
    assert service.finish_success(claim, proposal)
    task_id = service.get_node(space_id, node_id)["contents"][0]["resources"][0]["id"]
    return service, space_id, node_id, task_id


def cached_executor(service: RunService) -> ImageExecutor:
    return ImageExecutor(service, lambda: CachedCommons(ROOT))


def test_sourced_file_ready_served_by_id_and_read_does_not_refetch(
    database: Database, tmp_path: Path
) -> None:
    service, space_id, node_id, task_id = resource_setup(database, tmp_path)
    worker = Worker(
        service, ApplicationGenerator(FixtureGenerator(database, ROOT), cached_executor(service))
    )
    assert worker.tick()
    node = service.get_node(space_id, node_id)
    resource = node["contents"][0]["resources"][0]
    assert resource["status"] == "ready" and resource["versions"][0]["source"]["license"] == "CC0"
    with TestClient(create_app(service.settings, database)) as client:
        response = client.get(resource["versions"][0]["file_url"])
        assert response.status_code == 200 and response.headers["content-type"] == "image/png"
        assert response.content == (ROOT / "evals/assets/m4/commons-binary-search.png").read_bytes()
        assert response.headers["x-content-type-options"] == "nosniff"
    assert service.get_node(space_id, node_id)["revision"] == 2
    assert not worker.tick()
    result = Resources.retry(service, space_id, task_id)
    assert result.status == "succeeded"
    assert not worker.tick() and service.get_node(space_id, node_id) == node


def test_resource_failure_is_independent_and_can_retry(database: Database, tmp_path: Path) -> None:
    service, space_id, node_id, task_id = resource_setup(database, tmp_path)

    class Failure(CachedCommons):
        def search(self, query: str) -> list[dict]:
            raise GenerationError("provider_error", "controlled unavailable source")

    worker = Worker(
        service,
        ApplicationGenerator(
            FixtureGenerator(database, ROOT), ImageExecutor(service, lambda: Failure(ROOT))
        ),
    )
    assert worker.tick()
    before = service.get_node(space_id, node_id)
    assert before["contents"][0]["resources"][0]["status"] == "failed"
    assert before["revision"] == 2 and len(before["contents"]) == 2
    assert Resources.retry(service, space_id, task_id).status == "queued"
    assert Worker(
        service, ApplicationGenerator(FixtureGenerator(database, ROOT), cached_executor(service))
    ).tick()
    after = service.get_node(space_id, node_id)
    assert (
        after["revision"] == before["revision"]
        and after["contents"][0]["blocks"] == before["contents"][0]["blocks"]
    )
    assert after["contents"][0]["resources"][0]["status"] == "ready"


def test_completed_file_candidate_recovers_without_repeating_retrieval(
    database: Database, tmp_path: Path
) -> None:
    service, space_id, node_id, _ = resource_setup(database, tmp_path)
    assert Resources.dispatch(service) == 1
    first = service.claim()
    assert first and first.operation == "resource"
    proposal = cached_executor(service).generate(first)
    with database.transaction() as session:
        session.execute(
            text(
                "UPDATE generation_runs SET lease_until=clock_timestamp()-interval '1 second' "
                "WHERE id=:id"
            ),
            {"id": first.run_id},
        )
    assert service.recover_expired() == 1
    second = service.claim(first.run_id)
    assert second and second.attempt == 2

    def unavailable() -> CachedCommons:
        raise AssertionError("completed candidate must recover without another source call")

    assert ImageExecutor(service, unavailable).generate(second) == proposal
    assert service.finish_success(second, proposal)
    assert not service.finish_success(first, proposal)
    assert service.get_node(space_id, node_id)["contents"][0]["resources"][0]["status"] == "ready"
    assert service.trace(first.run_id)["tools"][-1]["output"]["network_call"] is False


def test_resource_old_content_conflicts_and_copy_preserves_original_asset(
    database: Database, tmp_path: Path
) -> None:
    service, space_id, node_id, _ = resource_setup(database, tmp_path)
    assert Resources.dispatch(service) == 1
    first = service.claim()
    assert first
    proposal = cached_executor(service).generate(first)
    # A fresh explanation replaces the dependency; the earlier file remains staged.
    run = service.create_action(
        space_id,
        LearningAction(
            operation="explanation",
            target_id=node_id,
            expected_profile_revision=1,
            expected_base_revision=2,
        ),
        "new-content",
    )
    claim = service.claim(run.run_id)
    assert claim
    raw = FixtureGenerator(database, ROOT).generate(claim)
    assert service.finish_success(claim, raw)
    assert not service.finish_success(first, proposal)
    node = service.get_node(space_id, node_id)
    assert node["revision"] == 3 and node["contents"][0]["resources"] == []
    assert node["contents"][1]["resources"][0]["status"] == "conflicted"
    with database.transaction() as session:
        assert session.scalar(select(func.count()).select_from(ResourceRevision)) == 0


def test_copied_content_keeps_original_resource_versions(
    database: Database, tmp_path: Path
) -> None:
    service, space_id, node_id, _ = resource_setup(database, tmp_path)
    worker = Worker(
        service, ApplicationGenerator(FixtureGenerator(database, ROOT), cached_executor(service))
    )
    assert worker.tick()
    before = service.get_node(space_id, node_id)["contents"][0]["resources"][0]
    service.create_action(
        space_id,
        LearningAction(
            operation="reassessment",
            target_id=node_id,
            expected_profile_revision=1,
            expected_base_revision=2,
        ),
        "copy-resource",
    )
    assert worker.tick()
    after = service.get_node(space_id, node_id)["contents"][0]["resources"][0]
    assert after["id"] == before["id"] and after["versions"] == before["versions"]
    assert after["status"] == "ready" and not after["can_retry"]


def test_missing_files_are_not_claimed_ready_and_orphans_are_identified(
    database: Database, tmp_path: Path
) -> None:
    service, space_id, node_id, _ = resource_setup(database, tmp_path)
    assert Resources.dispatch(service) == 1
    claim = service.claim()
    assert claim
    executor = cached_executor(service)
    proposal = executor.generate(claim)
    store = FileStore(tmp_path)
    with database.transaction() as session:
        candidate = session.scalar(select(ResourceCandidate))
        assert candidate
        stored = StoredFile(**candidate.file)
    store.path(stored.relative_path).unlink()
    with pytest.raises(ServiceError, match="no valid stored file"):
        service.finish_success(claim, proposal)
    with database.transaction() as session:
        assert session.scalar(select(func.count()).select_from(ResourceRevision)) == 0
    # A write before the metadata transaction can leave an orphan, not a ready reference.
    stored = store.write_image((ROOT / "evals/assets/m4/commons-binary-search.png").read_bytes())
    assert store.unreferenced(set()) == [stored.relative_path]
    assert store.unreferenced({stored.relative_path}) == []
    assert service.finish_success(claim, proposal)
    store.path(stored.relative_path).unlink()
    resource = service.get_node(space_id, node_id)["contents"][0]["resources"][0]
    assert resource["status"] == "missing" and resource["versions"][0]["file_url"] is None


def test_resource_capacity_leaves_outbox_pending(database: Database, tmp_path: Path) -> None:
    service, _, _, task_id = resource_setup(database, tmp_path)
    service.settings = replace(service.settings, queue_capacity=1)
    from test_app import request

    service.create_run(request(service), "full-capacity")
    assert Resources.dispatch(service) == 0
    with database.transaction() as session:
        task = session.get(ResourceTask, task_id)
        assert task and task.status == "pending" and task.dispatch_revision == 0


def test_source_metadata_requires_license_and_download_refuses_redirects() -> None:
    # CI uses the same frozen source metadata shape, not a live Commons query.
    page = {
        "title": "File:Sample.png",
        "imageinfo": [
            {
                "thumburl": "https://upload.wikimedia.org/wikipedia/commons/a/ab/x.png",
                "descriptionurl": "https://commons.wikimedia.org/wiki/File:Sample.png",
                "extmetadata": {
                    "LicenseShortName": {"value": "CC0"},
                    "Artist": {"value": "<b>Author</b>"},
                },
            }
        ],
    }
    payload = {"query": {"pages": {"1": page}}}

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.host == "commons.wikimedia.org":
            return httpx.Response(200, json=payload)
        return httpx.Response(302, headers={"location": "http://127.0.0.1/private"})

    source = CommonsImages(httpx.Client(transport=httpx.MockTransport(respond)))
    candidates = source.search("test")
    assert len(candidates) == 1 and candidates[0]["artist"] == "Author"
    with pytest.raises(GenerationError):
        source.download(candidates[0])
    page["imageinfo"][0]["extmetadata"]["LicenseShortName"]["value"] = "All rights reserved"
    assert source.search("test") == []
    source.client.close()
    for url in (
        "http://upload.wikimedia.org/x",
        "https://127.0.0.1/private",
        "https://upload.wikimedia.org:xyz/x",
        "https://user:secret@upload.wikimedia.org/wikipedia/commons/a.png",
    ):
        assert not image_url(url)


def test_store_rejects_html_svg_bad_rasters_and_path_traversal(tmp_path: Path) -> None:
    store = FileStore(tmp_path)
    for data in (
        b"<svg><script>alert(1)</script></svg>",
        b"<html>not an image</html>",
        b"\x89PNG\r\n\x1a\ntruncated",
    ):
        with pytest.raises(GenerationError):
            store.write_image(data)
    with pytest.raises(GenerationError):
        store.path("../../.env.local")
