"""Frozen 0017 tables: immutable item attribution, never original evidence."""

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


def define_item_binding_tables(metadata: MetaData) -> tuple[Table, Table, Table]:
    time = DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql")
    versions = Table(
        "item_binding_versions",
        metadata,
        Column("id", String(32), primary_key=True),
        Column("case_id", String(32), ForeignKey("cases.id"), nullable=False),
        Column("snapshot_id", String(64), nullable=False),
        Column("checklist_version_id", String(32), nullable=False),
        Column("version", Integer, nullable=False),
        Column("payload_json", JSON, nullable=False),
        Column("content_digest", String(64), nullable=False),
        Column("confirmed_by", String(64), nullable=False),
        Column("reason", String(1000), nullable=False),
        Column("created_at", time, nullable=False),
        ForeignKeyConstraint(
            ["case_id", "snapshot_id"],
            ["input_snapshots.case_id", "input_snapshots.id"],
            name="fk_item_binding_snapshot",
        ),
        ForeignKeyConstraint(
            ["case_id", "checklist_version_id"],
            ["checklist_versions.case_id", "checklist_versions.id"],
            name="fk_item_binding_checklist",
        ),
        UniqueConstraint("case_id", "id", name="uq_item_binding_case_id"),
        UniqueConstraint("case_id", "version", name="uq_item_binding_version"),
        CheckConstraint("version >= 1", name="ck_item_binding_version"),
        mysql_engine="InnoDB",
    )
    heads = Table(
        "item_binding_heads",
        metadata,
        Column("case_id", String(32), ForeignKey("cases.id"), primary_key=True),
        Column("version_id", String(32), nullable=False),
        Column("revision", Integer, nullable=False),
        ForeignKeyConstraint(
            ["case_id", "version_id"],
            ["item_binding_versions.case_id", "item_binding_versions.id"],
            name="fk_item_binding_head",
        ),
        CheckConstraint("revision >= 1", name="ck_item_binding_head_revision"),
        mysql_engine="InnoDB",
    )
    operations = Table(
        "item_binding_operations",
        metadata,
        Column("id", String(32), primary_key=True),
        Column("case_id", String(32), ForeignKey("cases.id"), nullable=False),
        Column("principal_key", String(64), nullable=False),
        Column("idempotency_key", String(255), nullable=False),
        Column("request_digest", String(64), nullable=False),
        Column("response_json", JSON, nullable=False),
        Column("created_at", time, nullable=False),
        UniqueConstraint(
            "case_id",
            "principal_key",
            "idempotency_key",
            name="uq_item_binding_operation",
        ),
        mysql_engine="InnoDB",
    )
    return versions, heads, operations


def add_run_binding_columns(table: Table) -> None:
    """NULL/NULL is historical; new empty sets have a non-null digest."""
    table.append_column(Column("item_binding_version_id", String(32)))
    table.append_column(Column("item_binding_digest", String(64)))
    table.append_constraint(
        ForeignKeyConstraint(
            ["case_id", "item_binding_version_id"],
            ["item_binding_versions.case_id", "item_binding_versions.id"],
            name=f"fk_{table.name}_item_binding",
        )
    )
    table.append_constraint(
        CheckConstraint(
            "item_binding_version_id IS NULL OR item_binding_digest IS NOT NULL",
            name=f"ck_{table.name}_item_binding",
        )
    )
