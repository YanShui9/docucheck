"""Frozen 0013 schema. Later migrations extend it without rewriting history."""

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


def define_memory_tables(metadata: MetaData) -> tuple[Table, Table, Table, Table]:
    time = DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql")
    profiles = Table(
        "memory_profiles",
        metadata,
        Column("principal_key", String(64), primary_key=True),
        Column("revision", Integer, nullable=False),
        Column("created_at", time, nullable=False),
        Column("updated_at", time, nullable=False),
        CheckConstraint("revision >= 0", name="ck_memory_profile_revision"),
        mysql_engine="InnoDB",
    )
    records = Table(
        "memory_records",
        metadata,
        Column(
            "principal_key",
            String(64),
            ForeignKey("memory_profiles.principal_key"),
            primary_key=True,
        ),
        Column("key", String(32), primary_key=True),
        Column("value", String(32), nullable=False),
        Column("expires_at", time),
        Column("updated_at", time, nullable=False),
        CheckConstraint(
            "(`key` = 'answer_length' AND value IN ('concise','standard','detailed')) OR "
            "(`key` = 'answer_format' AND value IN ('paragraph','bullets','table')) OR "
            "(`key` = 'language' AND value IN ('zh-CN','en'))",
            name="ck_memory_preference",
        ),
        mysql_engine="InnoDB",
    )
    tasks = Table(
        "task_memory",
        metadata,
        Column("id", String(32), primary_key=True),
        Column("case_id", String(32), nullable=False),
        Column("conversation_id", String(32), nullable=False),
        Column("snapshot_id", String(64), nullable=False),
        Column("source_run_id", String(32), nullable=False),
        Column("version", Integer, nullable=False),
        Column("memory_digest", String(64), nullable=False),
        Column("state_json", JSON, nullable=False),
        Column("created_at", time, nullable=False),
        UniqueConstraint("conversation_id", "version", name="uq_task_memory_version"),
        UniqueConstraint(
            "conversation_id",
            "source_run_id",
            "memory_digest",
            name="uq_task_memory_source",
        ),
        ForeignKeyConstraint(
            ["case_id", "conversation_id"],
            ["conversations.case_id", "conversations.id"],
            name="fk_task_memory_conversation",
        ),
        ForeignKeyConstraint(
            ["case_id", "conversation_id", "snapshot_id", "source_run_id"],
            [
                "agent_runs.case_id",
                "agent_runs.conversation_id",
                "agent_runs.snapshot_id",
                "agent_runs.id",
            ],
            name="fk_task_memory_run_scope",
            ondelete="CASCADE",
        ),
        CheckConstraint("version >= 1", name="ck_task_memory_version"),
        mysql_engine="InnoDB",
    )
    summaries = Table(
        "conversation_summaries",
        metadata,
        Column("id", String(32), primary_key=True),
        Column("case_id", String(32), nullable=False),
        Column("conversation_id", String(32), nullable=False),
        Column("snapshot_id", String(64), nullable=False),
        Column("version", Integer, nullable=False),
        Column("source_digest", String(64), nullable=False),
        Column("summary_json", JSON, nullable=False),
        Column("created_at", time, nullable=False),
        UniqueConstraint(
            "conversation_id", "version", name="uq_conversation_summary_version"
        ),
        ForeignKeyConstraint(
            ["case_id", "conversation_id"],
            ["conversations.case_id", "conversations.id"],
            name="fk_summary_conversation",
        ),
        ForeignKeyConstraint(
            ["case_id", "snapshot_id"],
            ["input_snapshots.case_id", "input_snapshots.id"],
            name="fk_summary_snapshot",
        ),
        CheckConstraint("version >= 1", name="ck_summary_version"),
        mysql_engine="InnoDB",
    )
    return profiles, records, tasks, summaries
