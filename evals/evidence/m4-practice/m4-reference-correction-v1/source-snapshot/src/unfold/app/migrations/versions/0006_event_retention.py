"""Retained event cursor identifies gaps without restarting generation."""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "generation_runs",
        sa.Column("first_event_seq", sa.Integer, nullable=False, server_default="1"),
    )
    op.execute(
        "UPDATE generation_runs g SET first_event_seq=COALESCE("
        "(SELECT min(sequence) FROM run_events e WHERE e.run_id=g.id),last_event_seq+1)"
    )
    op.create_check_constraint(
        "ck_event_cursor_range",
        "generation_runs",
        "first_event_seq > 0 AND first_event_seq <= last_event_seq+1",
    )
    op.create_index("ix_event_retention", "run_events", ["created_at", "run_id", "sequence"])


def downgrade() -> None:
    op.drop_index("ix_event_retention", "run_events")
    op.drop_constraint("ck_event_cursor_range", "generation_runs", type_="check")
    op.drop_column("generation_runs", "first_event_seq")
