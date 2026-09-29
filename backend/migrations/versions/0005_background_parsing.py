"""Lease uploaded document parsing to one background process.

Revision ID: 0005_background_parsing
Revises: 0004_conversation_memory
"""

import sqlalchemy as sa
from alembic import op

revision = "0005_background_parsing"
down_revision = "0004_conversation_memory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("document_versions", sa.Column("parse_claim_id", sa.String(32)))
    op.add_column("document_versions", sa.Column(
        "parse_lease_until", sa.DateTime().with_variant(
            sa.dialects.mysql.DATETIME(fsp=6), "mysql")))
    op.add_column("document_versions", sa.Column(
        "parse_attempts", sa.Integer(), nullable=False, server_default="0"))
    op.create_index("ix_documents_parse_lease", "document_versions",
                    ["parse_status", "parse_lease_until"])


def downgrade() -> None:
    op.drop_index("ix_documents_parse_lease", table_name="document_versions")
    op.drop_column("document_versions", "parse_attempts")
    op.drop_column("document_versions", "parse_lease_until")
    op.drop_column("document_versions", "parse_claim_id")
