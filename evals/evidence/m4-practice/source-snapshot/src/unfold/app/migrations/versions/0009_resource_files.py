"""Versioned actual files, candidates and recorded tool boundaries."""

from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "resource_tasks",
        sa.Column("dispatch_revision", sa.Integer, nullable=False, server_default="0"),
    )
    op.create_check_constraint(
        "ck_resource_dispatch_revision", "resource_tasks", "dispatch_revision >= 0"
    )
    for statement in Path(__file__).with_name("0009_schema.sql").read_text().split(";"):
        if statement.strip():
            op.execute(statement)


def downgrade() -> None:
    for table in ("tool_invocations", "resource_revisions", "resource_candidates"):
        op.drop_table(table)
    op.drop_constraint("ck_resource_dispatch_revision", "resource_tasks", type_="check")
    op.drop_column("resource_tasks", "dispatch_revision")
