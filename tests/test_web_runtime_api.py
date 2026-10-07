from pathlib import Path

from fastapi.testclient import TestClient
from test_resource_agent import ReuseModel, web_setup

from unfold.app.api import create_app
from unfold.app.db import Database
from unfold.app.resource_agent import ResourceAgent


def test_runtime_http_actions_frames_stop_and_cross_space(
    database: Database, tmp_path: Path
) -> None:
    service, claim, _ = web_setup(database, tmp_path)
    proposal = ResourceAgent(service, ReuseModel()).generate(claim)
    assert service.finish_success(claim, proposal)
    trace = service.trace(claim.run_id)
    resource_id, space_id = trace["snapshot"]["result_ref"], trace["request"]["space_id"]
    with TestClient(create_app(service.settings, database)) as client:
        # Same-origin model HTML is never served as a runnable page.
        assert client.get(f"/api/v1/resources/{resource_id}/file").status_code == 409
        other = service.create_space("other space", "isolation")
        assert (
            client.post(f"/api/v1/spaces/{other['id']}/resources/{resource_id}/runtime").status_code
            == 404
        )
        started = client.post(f"/api/v1/spaces/{space_id}/resources/{resource_id}/runtime")
        assert started.status_code == 201 and not started.json()["source_script_in_app_dom"]
        session_id = started.json()["session_id"]
        frame = client.get(f"/api/v1/runtime/{session_id}/frame")
        assert frame.status_code == 200 and frame.content.startswith(b"\x89PNG")
        saved_source = client.get(
            f"/api/v1/spaces/{space_id}/resources/{resource_id}/source"
        ).json()
        assert (
            client.get(f"/api/v1/spaces/{other['id']}/resources/{resource_id}/source").status_code
            == 404
        )
        denied = client.post(
            f"/api/v1/spaces/{space_id}/resources/{resource_id}/runtime",
            json={"working_html": "<script>fetch('https://example.com')</script>"},
        )
        assert denied.status_code == 422 and denied.json()["code"] == "capability_limit"
        copy = client.post(
            f"/api/v1/spaces/{space_id}/resources/{resource_id}/runtime",
            json={"working_html": "<html><body><h1>临时工作副本</h1></body></html>"},
        )
        assert copy.status_code == 201 and copy.json()["working_copy"]
        assert client.get(f"/api/v1/runtime/{session_id}/frame").status_code == 410
        session_id = copy.json()["session_id"]
        assert client.get(f"/api/v1/runtime/{session_id}/frame").content != frame.content
        reset = client.post(f"/api/v1/runtime/{session_id}/actions", json={"action": "reset"})
        assert reset.status_code == 200 and not reset.json()["working_copy"]
        assert client.get(f"/api/v1/runtime/{session_id}/frame").content == frame.content
        assert (
            client.get(f"/api/v1/spaces/{space_id}/resources/{resource_id}/source").json()
            == saved_source
        )
        assert (
            client.post(
                f"/api/v1/runtime/{session_id}/actions", json={"action": "key", "key": "ArrowRight"}
            ).status_code
            == 200
        )
        assert (
            client.post(
                f"/api/v1/runtime/{session_id}/actions",
                json={"action": "click", "x": -1.0, "y": 1.0},
            ).status_code
            == 422
        )
        assert (
            client.post(
                f"/api/v1/runtime/{session_id}/actions", json={"action": "reset"}
            ).status_code
            == 200
        )
        assert client.post(f"/api/v1/runtime/{session_id}/actions", json={"action": "stop"}).json()[
            "stopped"
        ]
        assert client.get(f"/api/v1/runtime/{session_id}/frame").status_code == 410
        assert service.get_run(claim.run_id).status == "succeeded"
