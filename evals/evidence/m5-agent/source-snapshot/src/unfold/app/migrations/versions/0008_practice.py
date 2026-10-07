"""Freeze questions, answer attempts and reported issues separately from content."""

from pathlib import Path

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_run_result_kind", "generation_runs", type_="check")
    op.create_check_constraint(
        "ck_run_result_kind",
        "generation_runs",
        "(status='succeeded') = (result_kind IS NOT NULL) AND "
        "(result_kind IS NULL OR result_kind IN "
        "('knowledge','structure_preview','exercise_set','resource'))",
    )
    for statement in Path(__file__).with_name("0008_schema.sql").read_text().split(";"):
        if statement.strip():
            op.execute(statement)


def downgrade() -> None:
    for table in ("exercise_issues", "exercise_answers", "exercise_questions", "exercise_sets"):
        op.drop_table(table)
    # New-generation rows cannot be represented by the earlier result contract.
    op.execute(
        "DELETE FROM run_events WHERE run_id IN "
        "(SELECT id FROM generation_runs WHERE result_kind IN ('exercise_set','resource'))"
    )
    op.execute(
        "UPDATE generation_runs SET status='cancelled', result_ref=NULL, result_kind=NULL "
        "WHERE result_kind IN ('exercise_set','resource')"
    )
    op.drop_constraint("ck_run_result_kind", "generation_runs", type_="check")
    op.create_check_constraint(
        "ck_run_result_kind",
        "generation_runs",
        "(status='succeeded') = (result_kind IS NOT NULL) AND "
        "(result_kind IS NULL OR result_kind IN ('knowledge','structure_preview'))",
    )
