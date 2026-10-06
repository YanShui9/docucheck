"""Frozen 0014 schema for drafts, immutable confirmations and operation receipts."""

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
)
from sqlalchemy.dialects import mysql


def define_checklist_tables(metadata: MetaData):
    time = DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql")
    versions = Table(
        "checklist_versions",
        metadata,
        Column("id", String(32), primary_key=True),
        Column("case_id", String(32), nullable=False),
        Column("snapshot_id", String(64), nullable=False),
        Column("input_revision", Integer, nullable=False),
        Column("version", Integer, nullable=False),
        Column("definition_json", JSON, nullable=False),
        Column("source_bindings_json", JSON, nullable=False),
        Column("content_digest", String(64), nullable=False),
        Column("confirmed_by", String(64), nullable=False),
        Column("reason", String(1000), nullable=False),
        Column("created_at", time, nullable=False),
        ForeignKeyConstraint(
            ["case_id", "snapshot_id", "input_revision"],
            [
                "input_snapshots.case_id",
                "input_snapshots.id",
                "input_snapshots.revision",
            ],
            name="fk_checklist_version_snapshot",
        ),
        UniqueConstraint("case_id", "version", name="uq_checklist_version"),
        UniqueConstraint("case_id", "id", name="uq_checklist_case_version_id"),
        CheckConstraint("version >= 1", name="ck_checklist_version"),
        mysql_engine="InnoDB",
    )
    heads = Table(
        "checklist_heads",
        metadata,
        Column("case_id", String(32), ForeignKey("cases.id"), primary_key=True),
        Column("snapshot_id", String(64), nullable=False),
        Column("revision", Integer, nullable=False),
        Column("draft_json", JSON),
        Column("source_bindings_json", JSON),
        Column("draft_digest", String(64)),
        Column("confirmed_version_id", String(32)),
        Column("draft_base_version_id", String(32)),
        Column("updated_by", String(64), nullable=False),
        Column("updated_at", time, nullable=False),
        ForeignKeyConstraint(
            ["case_id", "snapshot_id"],
            ["input_snapshots.case_id", "input_snapshots.id"],
            name="fk_checklist_head_snapshot",
        ),
        ForeignKeyConstraint(
            ["case_id", "confirmed_version_id"],
            ["checklist_versions.case_id", "checklist_versions.id"],
            name="fk_checklist_head_confirmation",
        ),
        ForeignKeyConstraint(
            ["case_id", "draft_base_version_id"],
            ["checklist_versions.case_id", "checklist_versions.id"],
            name="fk_checklist_head_draft_base",
        ),
        CheckConstraint("revision >= 1", name="ck_checklist_head_revision"),
        mysql_engine="InnoDB",
    )
    operations = Table(
        "checklist_operations",
        metadata,
        Column("id", String(32), primary_key=True),
        Column("case_id", String(32), ForeignKey("cases.id"), nullable=False),
        Column("principal_key", String(64), nullable=False),
        Column("operation", String(16), nullable=False),
        Column("idempotency_key", String(255), nullable=False),
        Column("request_digest", String(64), nullable=False),
        Column("response_json", JSON, nullable=False),
        Column("created_at", time, nullable=False),
        UniqueConstraint(
            "case_id",
            "principal_key",
            "operation",
            "idempotency_key",
            name="uq_checklist_operation",
        ),
        CheckConstraint(
            "operation IN ('save','confirm')", name="ck_checklist_operation"
        ),
        mysql_engine="InnoDB",
    )
    return versions, heads, operations
