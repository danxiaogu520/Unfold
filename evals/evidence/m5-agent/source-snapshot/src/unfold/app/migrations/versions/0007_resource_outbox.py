"""Resource requests commit with their originating explanation and completion event."""

from pathlib import Path

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint("uq_content_space_id", "content_revisions", ["space_id", "id"])
    source = Path(__file__).with_name("0007_schema.sql").read_text()
    for statement in source.split(";"):
        if statement.strip():
            op.execute(statement)


def downgrade() -> None:
    op.drop_table("resource_tasks")
    op.drop_constraint("uq_content_space_id", "content_revisions", type_="unique")
