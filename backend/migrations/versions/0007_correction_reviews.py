"""Persist immutable deterministic correction reviews.

Revision ID: 0007_correction_reviews
Revises: 0006_corrections
"""

import sqlalchemy as sa
from alembic import op

revision = "0007_correction_reviews"
down_revision = "0006_corrections"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "correction_reviews",
        sa.Column("correction_id", sa.String(32), sa.ForeignKey("corrections.id"),
                  primary_key=True),
        sa.Column("case_id", sa.String(32), sa.ForeignKey("cases.id"),
                  nullable=False),
        sa.Column("snapshot_id", sa.String(64), nullable=False),
        sa.Column("review_status", sa.String(20), nullable=False),
        sa.Column("repository_fingerprint", sa.String(64), nullable=False),
        sa.Column("corrected_fact_id", sa.String(64), nullable=False),
        sa.Column("fact_set_json", sa.JSON(), nullable=False),
        sa.Column("check_result_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["case_id", "snapshot_id"],
                                ["input_snapshots.case_id", "input_snapshots.id"],
                                name="fk_correction_reviews_snapshot"),
        sa.CheckConstraint("review_status IN ('complete', 'needs_review')",
                           name="ck_correction_reviews_status"),
        mysql_engine="InnoDB",
    )


def downgrade() -> None:
    op.drop_table("correction_reviews")
