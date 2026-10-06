"""Versioned user-defined procurement checklists."""

import sqlalchemy as sa
from alembic import op

from backend.app.db.checklist_schema_v1 import define_checklist_tables

revision = "0014_dynamic_checklists"
down_revision = "0013_layered_memory"
branch_labels = None
depends_on = None


def upgrade():
    meta = sa.MetaData()
    sa.Table("cases", meta, sa.Column("id", sa.String(32)))
    sa.Table(
        "input_snapshots",
        meta,
        sa.Column("case_id", sa.String(32)),
        sa.Column("id", sa.String(64)),
        sa.Column("revision", sa.Integer),
    )
    for table in define_checklist_tables(meta):
        table.create(op.get_bind(), checkfirst=True)


def downgrade():
    for name in ("checklist_operations", "checklist_heads", "checklist_versions"):
        op.drop_table(name)
