"""Lease retryable Case cleanup and persist original-file work items.

Revision ID: 0009_case_cleanup
Revises: 0008_case_deletion_request
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0009_case_cleanup"
down_revision = "0008_case_deletion_request"
branch_labels = None
depends_on = None

precise_datetime = sa.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql")


def upgrade() -> None:
    op.add_column("cases", sa.Column("deletion_cleanup_started_at",
                                   precise_datetime))
    op.add_column("cases", sa.Column("deletion_claim_id", sa.String(32)))
    op.add_column("cases", sa.Column("deletion_lease_until", precise_datetime))
    op.add_column("cases", sa.Column("deletion_attempts", sa.Integer(),
                                   nullable=False, server_default="0"))
    op.add_column("cases", sa.Column("deletion_error_code", sa.String(64)))
    op.add_column("cases", sa.Column("deletion_completed_at", precise_datetime))
    op.create_index("ix_cases_cleanup_due", "cases", [
        "deletion_completed_at", "deletion_requested_at", "deletion_lease_until",
    ])
    op.create_table(
        "case_cleanup_files",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("case_id", sa.String(32), sa.ForeignKey("cases.id"),
                  nullable=False),
        sa.Column("storage_key", sa.String(512), nullable=False),
        sa.Column("disposition", sa.String(20)),
        sa.Column("processed_at", precise_datetime),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("case_id", "storage_key",
                            name="uq_cleanup_case_storage"),
        mysql_engine="InnoDB",
    )


def downgrade() -> None:
    op.drop_table("case_cleanup_files")
    op.drop_index("ix_cases_cleanup_due", table_name="cases")
    op.drop_column("cases", "deletion_completed_at")
    op.drop_column("cases", "deletion_error_code")
    op.drop_column("cases", "deletion_attempts")
    op.drop_column("cases", "deletion_lease_until")
    op.drop_column("cases", "deletion_claim_id")
    op.drop_column("cases", "deletion_cleanup_started_at")
