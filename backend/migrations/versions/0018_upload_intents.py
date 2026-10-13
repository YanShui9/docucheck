"""Persist ownership before uploading a managed original."""
import sqlalchemy as sa
from alembic import op

from backend.app.db.upload_intent_schema_v1 import define_upload_intents

revision = "0018_upload_intents"
down_revision = "0017_item_bindings"
branch_labels = depends_on = None


def upgrade():
    define_upload_intents(sa.MetaData()).create(op.get_bind())
    op.create_index("ix_documents_storage_key", "document_versions", ["storage_key"])


def downgrade():
    op.drop_index("ix_documents_storage_key", table_name="document_versions")
    op.drop_table("upload_intents")
