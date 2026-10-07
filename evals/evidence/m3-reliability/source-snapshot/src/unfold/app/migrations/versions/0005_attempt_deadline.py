"""Bound effective attempt lifetime separately from heartbeat renewal."""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("generation_runs", sa.Column("attempt_deadline", sa.DateTime(timezone=True)))
    op.execute(
        "UPDATE generation_runs SET attempt_deadline=clock_timestamp()+interval '180 seconds' "
        "WHERE status='running'"
    )


def downgrade() -> None:
    op.drop_column("generation_runs", "attempt_deadline")
