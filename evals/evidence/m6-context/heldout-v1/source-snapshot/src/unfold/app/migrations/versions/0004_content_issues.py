"""Reported content issues remain visible through copied content lineage."""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "content_revisions",
        sa.Column("review_status", sa.String(16), nullable=False, server_default="unverified"),
    )
    op.create_check_constraint(
        "ck_content_review_status",
        "content_revisions",
        "review_status IN ('unverified','reported')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_content_review_status", "content_revisions", type_="check")
    op.drop_column("content_revisions", "review_status")
