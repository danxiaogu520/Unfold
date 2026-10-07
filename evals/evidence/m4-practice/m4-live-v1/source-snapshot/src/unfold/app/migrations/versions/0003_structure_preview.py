"""Persist explicit previews before replacing an existing active subtree."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "structure_suggestions",
        sa.Column("operation", sa.String(24), nullable=False, server_default="reassessment"),
    )
    op.add_column(
        "structure_suggestions",
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
    )
    op.add_column("structure_suggestions", sa.Column("run_id", sa.String(80)))
    op.add_column(
        "structure_suggestions",
        sa.Column("request", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.create_foreign_key(
        "fk_suggestion_run_space",
        "structure_suggestions",
        "generation_runs",
        ["space_id", "run_id"],
        ["space_id", "id"],
    )
    op.create_check_constraint(
        "ck_suggestion_status",
        "structure_suggestions",
        "status IN ('pending','applied','dismissed')",
    )
    op.execute(
        "UPDATE structure_suggestions s SET run_id=g.id, operation=g.operation, "
        "request=jsonb_build_object('schema_version','api.v1','space_id',g.space_id, "
        "'operation',g.operation,'profile_revision',g.profile_revision, "
        "'base_revision',g.base_revision,'input',g.input_payload) "
        "FROM generation_runs g WHERE g.result_ref=s.id AND g.status='succeeded'"
    )
    op.add_column("generation_runs", sa.Column("result_kind", sa.String(24)))
    op.execute(
        "UPDATE generation_runs SET result_kind = CASE WHEN EXISTS "
        "(SELECT 1 FROM structure_suggestions s WHERE s.id=generation_runs.result_ref) "
        "THEN 'structure_preview' ELSE 'knowledge' END WHERE status='succeeded'"
    )
    op.create_check_constraint(
        "ck_run_result_kind",
        "generation_runs",
        "(status='succeeded') = (result_kind IS NOT NULL) AND "
        "(result_kind IS NULL OR result_kind IN ('knowledge','structure_preview'))",
    )


def downgrade() -> None:
    op.drop_constraint("ck_run_result_kind", "generation_runs", type_="check")
    op.drop_column("generation_runs", "result_kind")
    op.drop_constraint("ck_suggestion_status", "structure_suggestions", type_="check")
    op.drop_constraint("fk_suggestion_run_space", "structure_suggestions", type_="foreignkey")
    for name in ("request", "run_id", "status", "operation"):
        op.drop_column("structure_suggestions", name)
