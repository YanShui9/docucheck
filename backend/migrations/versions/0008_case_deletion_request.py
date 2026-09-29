"""Record Case deletion intent before controlled cleanup.

Revision ID: 0008_case_deletion_request
Revises: 0007_correction_reviews
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0008_case_deletion_request"
down_revision = "0007_correction_reviews"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("cases", sa.Column(
        "deletion_requested_at",
        sa.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql"),
        nullable=True,
    ))


def downgrade() -> None:
    op.drop_column("cases", "deletion_requested_at")
