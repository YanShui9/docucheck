"""Bind the offered model to each immutable document version.

Revision ID: 0011_document_model_binding
Revises: 0010_project_access
"""

import sqlalchemy as sa
from alembic import op

revision = "0011_document_model_binding"
down_revision = "0010_project_access"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("document_versions", sa.Column("model_name", sa.String(120)))


def downgrade() -> None:
    op.drop_column("document_versions", "model_name")
