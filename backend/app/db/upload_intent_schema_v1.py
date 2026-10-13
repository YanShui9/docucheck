"""Frozen schema for upload ownership before any managed file is written."""
from sqlalchemy import CheckConstraint, Column, DateTime, Index, String, Table


def define_upload_intents(metadata):
    return Table(
        "upload_intents", metadata,
        Column("id", String(32), primary_key=True),
        Column("storage_key", String(255), nullable=False, unique=True),
        Column("owner_token", String(32), nullable=False),
        Column("state", String(16), nullable=False),
        Column("created_at", DateTime, nullable=False),
        Column("next_check_at", DateTime, nullable=False),
        CheckConstraint("state IN ('pending', 'abandoned')", name="ck_upload_intents_state"),
        Index("ix_upload_intents_due", "next_check_at", "id"),
        mysql_engine="InnoDB",
    )
