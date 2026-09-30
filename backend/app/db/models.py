"""First M3 schema: immutable input versions and durable run envelopes.

Application services must still enforce role, revision and state transitions.
Cross-process transitions need conditional updates in the repository layer.
"""

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects import mysql

metadata = MetaData()
precise_datetime = DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql")

cases = Table(
    "cases", metadata,
    Column("id", String(32), primary_key=True),
    Column("revision", Integer, nullable=False),
    Column("deletion_requested_at", precise_datetime),
    Column("deletion_cleanup_started_at", precise_datetime),
    Column("deletion_claim_id", String(32)),
    Column("deletion_lease_until", precise_datetime),
    Column("deletion_attempts", Integer, nullable=False, server_default="0"),
    Column("deletion_error_code", String(64)),
    Column("deletion_completed_at", precise_datetime),
    Column("created_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
    CheckConstraint("revision >= 0", name="ck_cases_revision"),
    mysql_engine="InnoDB",
)
Index("ix_cases_cleanup_due", cases.c.deletion_completed_at,
      cases.c.deletion_requested_at, cases.c.deletion_lease_until)

suppliers = Table(
    "suppliers", metadata,
    Column("id", String(32), primary_key=True),
    Column("case_id", String(32), ForeignKey("cases.id"), nullable=False),
    Column("handle", String(64), nullable=False),
    Column("name", String(255), nullable=False),
    Column("created_at", DateTime, nullable=False),
    UniqueConstraint("case_id", "handle", name="uq_suppliers_case_handle"),
    UniqueConstraint("case_id", "id", name="uq_suppliers_case_id"),
    mysql_engine="InnoDB",
)

input_snapshots = Table(
    "input_snapshots", metadata,
    Column("id", String(64), primary_key=True),
    Column("case_id", String(32), ForeignKey("cases.id"), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("created_at", DateTime, nullable=False),
    CheckConstraint("revision >= 1", name="ck_snapshots_revision"),
    UniqueConstraint("case_id", "revision", name="uq_snapshots_case_revision"),
    UniqueConstraint("case_id", "id", name="uq_snapshots_case_id"),
    UniqueConstraint("case_id", "id", "revision", name="uq_snapshots_case_id_revision"),
    mysql_engine="InnoDB",
)

document_versions = Table(
    "document_versions", metadata,
    Column("id", String(32), primary_key=True),
    Column("case_id", String(32), nullable=False),
    Column("snapshot_id", String(64), nullable=False),
    Column("supplier_id", String(32)),
    Column("role", String(20), nullable=False),
    Column("original_name", String(255), nullable=False),
    Column("storage_key", String(512), nullable=False),
    Column("source_sha256", String(64), nullable=False),
    Column("media_type", String(100), nullable=False),
    Column("parser_version", String(64)),
    Column("parse_status", String(20), nullable=False),
    Column("parse_claim_id", String(32)),
    Column("parse_lease_until", precise_datetime),
    Column("parse_attempts", Integer, nullable=False, server_default="0"),
    Column("coverage_json", JSON),
    Column("created_at", DateTime, nullable=False),
    ForeignKeyConstraint(["case_id", "snapshot_id"],
                         ["input_snapshots.case_id", "input_snapshots.id"],
                         name="fk_documents_snapshot"),
    ForeignKeyConstraint(["case_id", "supplier_id"],
                         ["suppliers.case_id", "suppliers.id"],
                         name="fk_documents_supplier"),
    CheckConstraint(
        "(role = 'requirement' AND supplier_id IS NULL) OR "
        "(role IN ('quote', 'spec') AND supplier_id IS NOT NULL)",
        name="ck_documents_role_supplier",
    ),
    UniqueConstraint("snapshot_id", "id", name="uq_documents_snapshot_id"),
    mysql_engine="InnoDB",
)
Index("ix_documents_parse_lease", document_versions.c.parse_status,
      document_versions.c.parse_lease_until)

evidence_blocks = Table(
    "evidence_blocks", metadata,
    Column("id", String(64), primary_key=True),
    Column("document_version_id", String(32), ForeignKey("document_versions.id"),
           nullable=False),
    Column("ordinal", Integer, nullable=False),
    Column("source_text", Text, nullable=False),
    Column("page_idx", Integer),
    Column("bbox_json", JSON),
    Column("sheet", String(255)),
    Column("cell", String(16)),
    Column("created_at", DateTime, nullable=False),
    CheckConstraint("ordinal >= 0", name="ck_evidence_ordinal"),
    UniqueConstraint("document_version_id", "ordinal", name="uq_evidence_document_ordinal"),
    mysql_engine="InnoDB",
)

conversations = Table(
    "conversations", metadata,
    Column("id", String(32), primary_key=True),
    Column("case_id", String(32), ForeignKey("cases.id"), nullable=False),
    Column("created_at", DateTime, nullable=False),
    UniqueConstraint("case_id", "id", name="uq_conversations_case_id"),
    mysql_engine="InnoDB",
)

messages = Table(
    "messages", metadata,
    Column("id", String(32), primary_key=True),
    Column("conversation_id", String(32), ForeignKey("conversations.id"), nullable=False),
    Column("role", String(20), nullable=False),
    Column("content", Text, nullable=False),
    Column("created_at", DateTime, nullable=False),
    CheckConstraint("role IN ('user', 'assistant')", name="ck_messages_role"),
    UniqueConstraint("conversation_id", "id", name="uq_messages_conversation_id"),
    mysql_engine="InnoDB",
)

agent_runs = Table(
    "agent_runs", metadata,
    Column("id", String(32), primary_key=True),
    Column("case_id", String(32), nullable=False),
    Column("conversation_id", String(32), nullable=False),
    Column("message_id", String(32), nullable=False),
    Column("snapshot_id", String(64), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("mode", String(20), nullable=False),
    Column("status", String(20), nullable=False),
    Column("active_attempt_id", String(32), nullable=False),
    Column("fence", Integer, nullable=False),
    Column("answer_text", Text),
    Column("error_code", String(64)),
    Column("memory_json", JSON),
    Column("created_at", DateTime, nullable=False),
    Column("updated_at", precise_datetime, nullable=False),
    ForeignKeyConstraint(["case_id", "conversation_id"],
                         ["conversations.case_id", "conversations.id"],
                         name="fk_runs_conversation"),
    ForeignKeyConstraint(["conversation_id", "message_id"],
                         ["messages.conversation_id", "messages.id"],
                         name="fk_runs_message"),
    ForeignKeyConstraint(["case_id", "snapshot_id", "revision"],
                         ["input_snapshots.case_id", "input_snapshots.id",
                          "input_snapshots.revision"],
                         name="fk_runs_snapshot_revision"),
    CheckConstraint("fence >= 0", name="ck_runs_fence"),
    CheckConstraint("mode IN ('standard', 'thorough')", name="ck_runs_mode"),
    UniqueConstraint("message_id", name="uq_runs_message"),
    mysql_engine="InnoDB",
)
Index("ix_runs_case_created", agent_runs.c.case_id, agent_runs.c.created_at)
Index("ix_runs_status_updated", agent_runs.c.status, agent_runs.c.updated_at)
Index("ix_runs_conversation_snapshot_status", agent_runs.c.conversation_id,
      agent_runs.c.snapshot_id, agent_runs.c.status)

run_attempts = Table(
    "run_attempts", metadata,
    Column("id", String(32), primary_key=True),
    Column("run_id", String(32), ForeignKey("agent_runs.id"), nullable=False),
    Column("attempt_no", Integer, nullable=False),
    Column("status", String(20), nullable=False),
    Column("outcome_unknown", Boolean, nullable=False),
    Column("model_id", String(100)),
    Column("usage_json", JSON),
    Column("resume_message_id", String(32), ForeignKey("messages.id")),
    Column("created_at", DateTime, nullable=False),
    Column("started_at", DateTime),
    Column("finished_at", DateTime),
    CheckConstraint("attempt_no >= 1", name="ck_attempts_number"),
    UniqueConstraint("run_id", "attempt_no", name="uq_attempts_run_number"),
    UniqueConstraint("run_id", "id", name="uq_attempts_run_id"),
    mysql_engine="InnoDB",
)

run_events = Table(
    "run_events", metadata,
    Column("id", String(32), primary_key=True),
    Column("run_id", String(32), nullable=False),
    Column("attempt_id", String(32), nullable=False),
    Column("seq", Integer, nullable=False),
    Column("kind", String(40), nullable=False),
    Column("status", String(20), nullable=False),
    Column("summary_json", JSON, nullable=False),
    Column("created_at", DateTime, nullable=False),
    ForeignKeyConstraint(["run_id", "attempt_id"],
                         ["run_attempts.run_id", "run_attempts.id"],
                         name="fk_events_attempt"),
    UniqueConstraint("run_id", "seq", name="uq_events_run_seq"),
    mysql_engine="InnoDB",
)

outbox = Table(
    "outbox", metadata,
    Column("id", String(32), primary_key=True),
    Column("event_key", String(128), nullable=False),
    Column("run_id", String(32), nullable=False),
    Column("attempt_id", String(32), nullable=False),
    Column("event_type", String(40), nullable=False),
    Column("payload_json", JSON, nullable=False),
    Column("status", String(20), nullable=False),
    Column("delivery_attempts", Integer, nullable=False),
    Column("available_at", precise_datetime, nullable=False),
    Column("delivered_at", precise_datetime),
    Column("created_at", DateTime, nullable=False),
    ForeignKeyConstraint(["run_id", "attempt_id"],
                         ["run_attempts.run_id", "run_attempts.id"],
                         name="fk_outbox_attempt"),
    UniqueConstraint("event_key", name="uq_outbox_event_key"),
    mysql_engine="InnoDB",
)
Index("ix_outbox_status_available", outbox.c.status, outbox.c.available_at)

idempotency_records = Table(
    "idempotency_records", metadata,
    Column("id", String(32), primary_key=True),
    Column("operation_scope", String(128), nullable=False),
    Column("key", String(255), nullable=False),
    Column("request_hash", String(64), nullable=False),
    Column("resource_type", String(32), nullable=False),
    Column("resource_id", String(64), nullable=False),
    Column("created_at", DateTime, nullable=False),
    UniqueConstraint("operation_scope", "key", name="uq_idempotency_scope_key"),
    mysql_engine="InnoDB",
)

corrections = Table(
    "corrections", metadata,
    Column("id", String(32), primary_key=True),
    Column("case_id", String(32), ForeignKey("cases.id"), nullable=False),
    Column("source_run_id", String(32), ForeignKey("agent_runs.id"), nullable=False),
    Column("from_snapshot_id", String(64), nullable=False),
    Column("to_snapshot_id", String(64), nullable=False),
    Column("fact_set_id", String(64), nullable=False),
    Column("fact_id", String(64), nullable=False),
    Column("field_key", String(32), nullable=False),
    Column("supplier", String(64), nullable=False),
    Column("old_fact_json", JSON, nullable=False),
    Column("corrected_candidate_json", JSON, nullable=False),
    Column("source_evidence_ids_json", JSON, nullable=False),
    Column("target_evidence_ids_json", JSON, nullable=False),
    Column("reason", String(2000), nullable=False),
    Column("created_at", DateTime, nullable=False),
    ForeignKeyConstraint(["case_id", "from_snapshot_id"],
                         ["input_snapshots.case_id", "input_snapshots.id"],
                         name="fk_corrections_from_snapshot"),
    ForeignKeyConstraint(["case_id", "to_snapshot_id"],
                         ["input_snapshots.case_id", "input_snapshots.id"],
                         name="fk_corrections_to_snapshot"),
    UniqueConstraint("to_snapshot_id", name="uq_corrections_to_snapshot"),
    mysql_engine="InnoDB",
)
Index("ix_corrections_case_created", corrections.c.case_id, corrections.c.created_at)

correction_reviews = Table(
    "correction_reviews", metadata,
    Column("correction_id", String(32), ForeignKey("corrections.id"),
           primary_key=True),
    Column("case_id", String(32), ForeignKey("cases.id"), nullable=False),
    Column("snapshot_id", String(64), nullable=False),
    Column("review_status", String(20), nullable=False),
    Column("repository_fingerprint", String(64), nullable=False),
    Column("corrected_fact_id", String(64), nullable=False),
    Column("fact_set_json", JSON, nullable=False),
    Column("check_result_json", JSON, nullable=False),
    Column("created_at", DateTime, nullable=False),
    ForeignKeyConstraint(["case_id", "snapshot_id"],
                         ["input_snapshots.case_id", "input_snapshots.id"],
                         name="fk_correction_reviews_snapshot"),
    CheckConstraint("review_status IN ('complete', 'needs_review')",
                    name="ck_correction_reviews_status"),
    mysql_engine="InnoDB",
)

case_cleanup_files = Table(
    "case_cleanup_files", metadata,
    Column("id", String(32), primary_key=True),
    Column("case_id", String(32), ForeignKey("cases.id"), nullable=False),
    Column("storage_key", String(512), nullable=False),
    Column("disposition", String(20)),
    Column("processed_at", precise_datetime),
    Column("created_at", DateTime, nullable=False),
    UniqueConstraint("case_id", "storage_key", name="uq_cleanup_case_storage"),
    mysql_engine="InnoDB",
)
