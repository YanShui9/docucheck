"""Frozen 0016 adjunct schema for conversational runs and their fixed edit base."""

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Table,
)


def define_assistant_table(metadata):
    return Table(
        "assistant_runs", metadata,
        Column("run_id", String(32), ForeignKey("agent_runs.id"), primary_key=True),
        Column("case_id", String(32), ForeignKey("cases.id"), nullable=False),
        Column("snapshot_id", String(64), nullable=False),
        Column("checklist_revision", Integer, nullable=False),
        Column("checklist_version_id", String(32)),
        Column("base_json", JSON),
        Column("base_digest", String(64), nullable=False),
        Column("payload_json", JSON),
        Column("payload_digest", String(64)),
        ForeignKeyConstraint(["case_id", "snapshot_id"], ["input_snapshots.case_id", "input_snapshots.id"], name="fk_assistant_snapshot"),
        ForeignKeyConstraint(["case_id", "checklist_version_id"], ["checklist_versions.case_id", "checklist_versions.id"], name="fk_assistant_confirmation"),
        CheckConstraint("checklist_revision >= 0", name="ck_assistant_checklist_revision"),
        mysql_engine="InnoDB",
    )
