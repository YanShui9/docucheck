"""Pinned dynamic workflows and validated batch progress."""

import sqlalchemy as sa
from alembic import op

from backend.app.db.dynamic_schema_v1 import define_dynamic_tables

revision = "0015_dynamic_agent"
down_revision = "0014_dynamic_checklists"
branch_labels = depends_on = None


def upgrade():
    meta = sa.MetaData()
    sa.Table("agent_runs", meta, sa.Column("id", sa.String(32)))
    sa.Table("cases", meta, sa.Column("id", sa.String(32)))
    sa.Table(
        "input_snapshots",
        meta,
        sa.Column("case_id", sa.String(32)),
        sa.Column("id", sa.String(64)),
    )
    sa.Table(
        "checklist_versions",
        meta,
        sa.Column("case_id", sa.String(32)),
        sa.Column("id", sa.String(32)),
    )
    for table in define_dynamic_tables(meta):
        table.create(op.get_bind(), checkfirst=True)


def downgrade():
    for name in ("dynamic_corrections", "dynamic_batches", "dynamic_runs"):
        op.drop_table(name)
