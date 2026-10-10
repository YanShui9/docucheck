"""Immutable procurement item attribution and frozen run identities."""

import sqlalchemy as sa
from alembic import op

from backend.app.db.item_binding_schema_v1 import define_item_binding_tables

revision = "0017_item_bindings"
down_revision = "0016_assistant_runs"
branch_labels = depends_on = None


def upgrade():
    metadata = sa.MetaData()
    sa.Table("cases", metadata, sa.Column("id", sa.String(32)))
    sa.Table(
        "input_snapshots",
        metadata,
        sa.Column("case_id", sa.String(32)),
        sa.Column("id", sa.String(64)),
    )
    sa.Table(
        "checklist_versions",
        metadata,
        sa.Column("case_id", sa.String(32)),
        sa.Column("id", sa.String(32)),
    )
    for table in define_item_binding_tables(metadata):
        table.create(op.get_bind())
    for name in ("dynamic_runs", "assistant_runs"):
        with op.batch_alter_table(name) as batch:
            batch.add_column(sa.Column("item_binding_version_id", sa.String(32)))
            batch.add_column(sa.Column("item_binding_digest", sa.String(64)))
            batch.create_foreign_key(
                f"fk_{name}_item_binding",
                "item_binding_versions",
                ["case_id", "item_binding_version_id"],
                ["case_id", "id"],
            )
            batch.create_check_constraint(
                f"ck_{name}_item_binding",
                "item_binding_version_id IS NULL OR item_binding_digest IS NOT NULL",
            )


def downgrade():
    # A downgrade cannot discard the identity used to validate existing reports.
    if op.get_bind().scalar(sa.text("SELECT COUNT(*) FROM item_binding_versions")):
        raise ValueError("item_binding_history_present")
    for name in ("dynamic_runs", "assistant_runs"):
        if op.get_bind().scalar(
            sa.text(
                f"SELECT COUNT(*) FROM {name} WHERE item_binding_digest IS NOT NULL"
            )
        ):
            raise ValueError("item_binding_runs_present")
    for name in ("dynamic_runs", "assistant_runs"):
        with op.batch_alter_table(name) as batch:
            batch.drop_constraint(f"fk_{name}_item_binding", type_="foreignkey")
            batch.drop_constraint(f"ck_{name}_item_binding", type_="check")
            batch.drop_column("item_binding_version_id")
            batch.drop_column("item_binding_digest")
    for name in (
        "item_binding_operations",
        "item_binding_heads",
        "item_binding_versions",
    ):
        op.drop_table(name)
