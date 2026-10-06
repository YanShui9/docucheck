"""Durable index jobs, generation manifests and authenticated Run requester."""
import sqlalchemy as sa
from alembic import op

from backend.app.db.index_schema_v1 import define_index_tables

revision = "0012_durable_indexes"
down_revision = "0011_document_model_binding"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("document_versions") as batch:
        batch.create_unique_constraint("uq_documents_case_snapshot_id", ["case_id", "snapshot_id", "id"])
    op.add_column("agent_runs", sa.Column("requester_key", sa.String(64)))
    # Parent stubs resolve frozen FK references; they are not created or modified.
    meta = sa.MetaData()
    sa.Table("cases", meta, sa.Column("id", sa.String(32)))
    sa.Table("input_snapshots", meta, sa.Column("id", sa.String(64)), sa.Column("case_id", sa.String(32)))
    sa.Table("document_versions", meta, sa.Column("id", sa.String(32)),
             sa.Column("case_id", sa.String(32)), sa.Column("snapshot_id", sa.String(64)))
    jobs, manifests = define_index_tables(meta)
    manifests.create(op.get_bind())
    jobs.create(op.get_bind())


def downgrade():
    op.drop_table("document_index_jobs")
    op.drop_table("index_manifests")
    op.drop_column("agent_runs", "requester_key")
    with op.batch_alter_table("document_versions") as batch:
        batch.drop_constraint("uq_documents_case_snapshot_id", type_="unique")
