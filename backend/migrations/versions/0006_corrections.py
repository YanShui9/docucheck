"""Append-only source-backed correction audit.

Revision ID: 0006_corrections
Revises: 0005_background_parsing
"""

import sqlalchemy as sa
from alembic import op

revision = "0006_corrections"
down_revision = "0005_background_parsing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "corrections",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("case_id", sa.String(32), sa.ForeignKey("cases.id"), nullable=False),
        sa.Column("source_run_id", sa.String(32), sa.ForeignKey("agent_runs.id"),
                  nullable=False),
        sa.Column("from_snapshot_id", sa.String(64), nullable=False),
        sa.Column("to_snapshot_id", sa.String(64), nullable=False),
        sa.Column("fact_set_id", sa.String(64), nullable=False),
        sa.Column("fact_id", sa.String(64), nullable=False),
        sa.Column("field_key", sa.String(32), nullable=False),
        sa.Column("supplier", sa.String(64), nullable=False),
        sa.Column("old_fact_json", sa.JSON(), nullable=False),
        sa.Column("corrected_candidate_json", sa.JSON(), nullable=False),
        sa.Column("source_evidence_ids_json", sa.JSON(), nullable=False),
        sa.Column("target_evidence_ids_json", sa.JSON(), nullable=False),
        sa.Column("reason", sa.String(2000), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["case_id", "from_snapshot_id"],
                                ["input_snapshots.case_id", "input_snapshots.id"],
                                name="fk_corrections_from_snapshot"),
        sa.ForeignKeyConstraint(["case_id", "to_snapshot_id"],
                                ["input_snapshots.case_id", "input_snapshots.id"],
                                name="fk_corrections_to_snapshot"),
        sa.UniqueConstraint("to_snapshot_id", name="uq_corrections_to_snapshot"),
        mysql_engine="InnoDB",
    )
    op.create_index("ix_corrections_case_created", "corrections",
                    ["case_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_corrections_case_created", table_name="corrections")
    op.drop_table("corrections")
