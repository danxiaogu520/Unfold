from pathlib import Path

from test_app import settings
from test_resource_agent import ReuseModel

from unfold.app.db import Database
from unfold.app.resource_agent import ResourceAgent
from unfold.app.resources import Resources
from unfold.app.service import RunService
from unfold.eval_resources import public_space


def test_public_eval_seed_only_constructs_new_synthetic_content(
    database: Database, tmp_path: Path
) -> None:
    service = RunService(database, settings(database, data_directory=tmp_path))
    case = {
        "id": "search_component-1",
        "family": "search_component",
        "task": "公开二分查找组件任务",
        "split": "dev",
    }
    space_id, task_id = public_space(service, case, "public-seed-test")
    snapshot = Resources.retry(service, space_id, task_id)
    claim = service.claim(snapshot.run_id)
    assert claim and claim.input["content"]["text"].startswith("公开合成评测资料：")
    assert claim.context["web_check_case"] == "search_component-1"
    assert claim.input["profile"]["profile"]["primary_purpose"]["value"] == case["task"]
    proposal = ResourceAgent(service, ReuseModel()).generate(claim)
    assert service.finish_success(claim, proposal)
