"""Fixed-base assistant runs and explicit revision-zero chat snapshots."""

import sqlalchemy as sa
from alembic import op

from backend.app.db.assistant_schema_v1 import define_assistant_table

revision = "0016_assistant_runs"
down_revision = "0015_dynamic_agent"
branch_labels = depends_on = None


def upgrade():
    with op.batch_alter_table("input_snapshots") as batch:
        batch.drop_constraint("ck_snapshots_revision", type_="check")
        batch.create_check_constraint("ck_snapshots_revision", "revision >= 0")
    metadata = sa.MetaData()
    sa.Table("agent_runs", metadata, sa.Column("id", sa.String(32)))
    sa.Table("cases", metadata, sa.Column("id", sa.String(32)))
    sa.Table("input_snapshots", metadata, sa.Column("case_id", sa.String(32)), sa.Column("id", sa.String(64)))
    sa.Table("checklist_versions", metadata, sa.Column("case_id", sa.String(32)), sa.Column("id", sa.String(32)))
    define_assistant_table(metadata).create(op.get_bind(), checkfirst=True)


def downgrade():
    # Revision-zero snapshots can have dependent historical runs. Refuse to
    # discard them or produce a database violating the older constraint.
    if op.get_bind().scalar(sa.text("SELECT COUNT(*) FROM input_snapshots WHERE revision = 0")):
        raise ValueError("assistant_empty_snapshots_present")
    op.drop_table("assistant_runs")
    with op.batch_alter_table("input_snapshots") as batch:
        batch.drop_constraint("ck_snapshots_revision", type_="check")
        batch.create_check_constraint("ck_snapshots_revision", "revision >= 1")
