"""Dependency-qualified derived summaries, never authoritative content."""

from pathlib import Path

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for statement in Path(__file__).with_name("0011_schema.sql").read_text().split(";"):
        if statement.strip():
            op.execute(statement)


def downgrade() -> None:
    op.drop_table("content_summaries")
