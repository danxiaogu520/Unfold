from pathlib import Path

from test_learning import seed

from unfold.app.db import Database
from unfold.app.generation import FixtureGenerator
from unfold.app.learning import FeedbackInput, LearningAction, ProfileEdit
from unfold.app.worker import Worker


def test_feedback_keeps_content_and_profile_versions_through_replacement_and_carry(
    database: Database,
) -> None:
    service, space_id = seed(database)
    node = next(n for n in service.get_space(space_id)["nodes"] if n["decision"]["state"] == "leaf")
    worker = Worker(service, FixtureGenerator(database, Path(__file__).resolve().parents[1]))

    def feedback(revision: int, text: str) -> None:
        service.feedback(
            space_id,
            node["id"],
            FeedbackInput(
                expected_profile_revision=1,
                expected_base_revision=revision,
                kind="omission",
                text=text,
            ),
        )

    feedback(1, "概述遗漏条件")
    run = service.create_action(
        space_id,
        LearningAction(
            operation="explanation",
            target_id=node["id"],
            expected_profile_revision=1,
            expected_base_revision=1,
        ),
        "replace-overview",
    )
    claim = service.claim(run.run_id)
    assert claim and worker.execute(claim)
    feedback(2, "讲解遗漏边界例子")
    run = service.create_action(
        space_id,
        LearningAction(
            operation="reassessment",
            target_id=node["id"],
            expected_profile_revision=1,
            expected_base_revision=2,
        ),
        "carry-lecture",
    )
    claim = service.claim(run.run_id)
    assert claim and worker.execute(claim)
    service.edit_profile(
        space_id,
        ProfileEdit(
            expected_revision=1,
            fields={"secondary_purposes": ["验证边界"]},
        ),
    )
    saved = service.get_node(space_id, node["id"])
    assert saved["contents"][0]["source"]["content_carried_from"] == saved["contents"][1]["id"]
    assert [
        (f["kind"], f["content_revision"], f["profile_revision"]) for f in saved["feedback"]
    ] == [
        ("omission", 2, 1),
        ("omission", 1, 1),
    ]
    assert service.get_node(space_id, node["id"])["feedback"] == saved["feedback"]
