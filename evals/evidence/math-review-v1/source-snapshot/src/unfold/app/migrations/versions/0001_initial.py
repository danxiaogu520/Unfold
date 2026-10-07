"""Initial durable learning and generation records; SQL is frozen with this revision."""

from pathlib import Path

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    sql = Path(__file__).with_name("0001_schema.sql").read_text()
    for statement in sql.split("\n-- unfold statement --\n"):
        op.execute(statement)


def downgrade() -> None:
    op.drop_constraint("fk_space_root", "spaces", type_="foreignkey")
    for name in (
        "model_calls",
        "request_identities",
        "run_events",
        "run_attempts",
        "generation_runs",
        "content_revisions",
        "node_decisions",
        "relations",
        "outline_items",
        "concepts",
        "intake_sessions",
        "profile_revisions",
        "budget_ledgers",
        "spaces",
    ):
        op.drop_table(name)
