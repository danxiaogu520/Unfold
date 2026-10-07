"""Versioned feedback and explicit subtree review suggestions."""

from pathlib import Path

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for statement in (
        Path(__file__).with_name("0002_schema.sql").read_text().split("\n-- unfold statement --\n")
    ):
        op.execute(statement)


def downgrade() -> None:
    op.drop_table("structure_suggestions")
    op.drop_table("learning_feedback")
