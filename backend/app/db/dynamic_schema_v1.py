"""Frozen 0015 schema: dynamic workflow binding, validated batches and corrections."""

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
)
from sqlalchemy.dialects import mysql


def define_dynamic_tables(metadata: MetaData):
    time = DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql")
    runs = Table(
        "dynamic_runs",
        metadata,
        Column("run_id", String(32), ForeignKey("agent_runs.id"), primary_key=True),
        Column("case_id", String(32), ForeignKey("cases.id"), nullable=False),
        Column("snapshot_id", String(64), nullable=False),
        Column("workflow", String(16), nullable=False),
        Column("checklist_revision", Integer, nullable=False),
        Column("checklist_version_id", String(32)),
        Column("definition_digest", String(64)),
        Column("payload_json", JSON),
        Column("payload_digest", String(64)),
        ForeignKeyConstraint(
            ["case_id", "snapshot_id"],
            ["input_snapshots.case_id", "input_snapshots.id"],
            name="fk_dynamic_snapshot",
        ),
        ForeignKeyConstraint(
            ["case_id", "checklist_version_id"],
            ["checklist_versions.case_id", "checklist_versions.id"],
            name="fk_dynamic_confirmation",
        ),
        CheckConstraint("checklist_revision >= 0", name="ck_dynamic_revision"),
        CheckConstraint(
            "(workflow = 'proposal' AND checklist_version_id IS NULL AND definition_digest IS NULL) OR "
            "(workflow = 'review' AND checklist_version_id IS NOT NULL AND definition_digest IS NOT NULL)",
            name="ck_dynamic_workflow",
        ),
        mysql_engine="InnoDB",
    )
    batches = Table(
        "dynamic_batches",
        metadata,
        Column(
            "run_id", String(32), ForeignKey("dynamic_runs.run_id"), primary_key=True
        ),
        Column("batch_id", String(64), primary_key=True),
        Column("input_digest", String(64), nullable=False),
        Column("output_json", JSON, nullable=False),
        Column("output_digest", String(64), nullable=False),
        Column("created_at", time, nullable=False),
        mysql_engine="InnoDB",
    )
    corrections = Table(
        "dynamic_corrections",
        metadata,
        Column("id", String(32), primary_key=True),
        Column("run_id", String(32), ForeignKey("dynamic_runs.run_id"), nullable=False),
        Column("principal_key", String(64), nullable=False),
        Column("idempotency_key", String(255), nullable=False),
        Column("request_digest", String(64), nullable=False),
        Column("old_payload_json", JSON, nullable=False),
        Column("new_payload_json", JSON, nullable=False),
        Column("reason", String(1000), nullable=False),
        Column("created_at", time, nullable=False),
        mysql_engine="InnoDB",
    )
    from sqlalchemy import UniqueConstraint

    corrections.append_constraint(
        UniqueConstraint(
            "run_id", "principal_key", "idempotency_key", name="uq_dynamic_correction"
        )
    )
    return runs, batches, corrections
