"""Logical Agent steps survive worker attempt changes."""

from pathlib import Path

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for statement in Path(__file__).with_name("0010_schema.sql").read_text().split(";"):
        if statement.strip():
            op.execute(statement)


def downgrade() -> None:
    op.drop_table("agent_steps")
