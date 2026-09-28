"""Create the first case, snapshot and run envelope tables.

Revision ID: 0001_core
Revises:
"""

import sqlalchemy as sa
from alembic import op

revision = "0001_core"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cases",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("revision >= 0", name="ck_cases_revision"),
        mysql_engine="InnoDB",
    )
    op.create_table(
        "suppliers",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("case_id", sa.String(32), sa.ForeignKey("cases.id"), nullable=False),
        sa.Column("handle", sa.String(64), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("case_id", "handle", name="uq_suppliers_case_handle"),
        sa.UniqueConstraint("case_id", "id", name="uq_suppliers_case_id"),
        mysql_engine="InnoDB",
    )
    op.create_table(
        "input_snapshots",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("case_id", sa.String(32), sa.ForeignKey("cases.id"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("revision >= 1", name="ck_snapshots_revision"),
        sa.UniqueConstraint("case_id", "revision", name="uq_snapshots_case_revision"),
        sa.UniqueConstraint("case_id", "id", name="uq_snapshots_case_id"),
        sa.UniqueConstraint("case_id", "id", "revision",
                            name="uq_snapshots_case_id_revision"),
        mysql_engine="InnoDB",
    )
    op.create_table(
        "document_versions",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("case_id", sa.String(32), nullable=False),
        sa.Column("snapshot_id", sa.String(64), nullable=False),
        sa.Column("supplier_id", sa.String(32)),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("original_name", sa.String(255), nullable=False),
        sa.Column("storage_key", sa.String(512), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("media_type", sa.String(100), nullable=False),
        sa.Column("parser_version", sa.String(64)),
        sa.Column("parse_status", sa.String(20), nullable=False),
        sa.Column("coverage_json", sa.JSON()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["case_id", "snapshot_id"],
                                ["input_snapshots.case_id", "input_snapshots.id"],
                                name="fk_documents_snapshot"),
        sa.ForeignKeyConstraint(["case_id", "supplier_id"],
                                ["suppliers.case_id", "suppliers.id"],
                                name="fk_documents_supplier"),
        sa.CheckConstraint(
            "(role = 'requirement' AND supplier_id IS NULL) OR "
            "(role IN ('quote', 'spec') AND supplier_id IS NOT NULL)",
            name="ck_documents_role_supplier",
        ),
        sa.UniqueConstraint("snapshot_id", "id", name="uq_documents_snapshot_id"),
        mysql_engine="InnoDB",
    )
    op.create_table(
        "evidence_blocks",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("document_version_id", sa.String(32),
                  sa.ForeignKey("document_versions.id"), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("source_text", sa.Text(), nullable=False),
        sa.Column("page_idx", sa.Integer()),
        sa.Column("bbox_json", sa.JSON()),
        sa.Column("sheet", sa.String(255)),
        sa.Column("cell", sa.String(16)),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("ordinal >= 0", name="ck_evidence_ordinal"),
        sa.UniqueConstraint("document_version_id", "ordinal",
                            name="uq_evidence_document_ordinal"),
        mysql_engine="InnoDB",
    )
    op.create_table(
        "conversations",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("case_id", sa.String(32), sa.ForeignKey("cases.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("case_id", "id", name="uq_conversations_case_id"),
        mysql_engine="InnoDB",
    )
    op.create_table(
        "messages",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("conversation_id", sa.String(32), sa.ForeignKey("conversations.id"),
                  nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("role IN ('user', 'assistant')", name="ck_messages_role"),
        sa.UniqueConstraint("conversation_id", "id", name="uq_messages_conversation_id"),
        mysql_engine="InnoDB",
    )
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("case_id", sa.String(32), nullable=False),
        sa.Column("conversation_id", sa.String(32), nullable=False),
        sa.Column("message_id", sa.String(32), nullable=False),
        sa.Column("snapshot_id", sa.String(64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("active_attempt_id", sa.String(32), nullable=False),
        sa.Column("fence", sa.Integer(), nullable=False),
        sa.Column("answer_text", sa.Text()),
        sa.Column("error_code", sa.String(64)),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["case_id", "conversation_id"],
                                ["conversations.case_id", "conversations.id"],
                                name="fk_runs_conversation"),
        sa.ForeignKeyConstraint(["conversation_id", "message_id"],
                                ["messages.conversation_id", "messages.id"],
                                name="fk_runs_message"),
        sa.ForeignKeyConstraint(["case_id", "snapshot_id", "revision"],
                                ["input_snapshots.case_id", "input_snapshots.id",
                                 "input_snapshots.revision"],
                                name="fk_runs_snapshot_revision"),
        sa.CheckConstraint("fence >= 0", name="ck_runs_fence"),
        sa.CheckConstraint("mode IN ('standard', 'thorough')", name="ck_runs_mode"),
        sa.UniqueConstraint("message_id", name="uq_runs_message"),
        mysql_engine="InnoDB",
    )
    op.create_index("ix_runs_case_created", "agent_runs", ["case_id", "created_at"])
    op.create_table(
        "run_attempts",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("run_id", sa.String(32), sa.ForeignKey("agent_runs.id"), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("outcome_unknown", sa.Boolean(), nullable=False),
        sa.Column("model_id", sa.String(100)),
        sa.Column("usage_json", sa.JSON()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime()),
        sa.Column("finished_at", sa.DateTime()),
        sa.CheckConstraint("attempt_no >= 1", name="ck_attempts_number"),
        sa.UniqueConstraint("run_id", "attempt_no", name="uq_attempts_run_number"),
        sa.UniqueConstraint("run_id", "id", name="uq_attempts_run_id"),
        mysql_engine="InnoDB",
    )
    op.create_table(
        "run_events",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("run_id", sa.String(32), nullable=False),
        sa.Column("attempt_id", sa.String(32), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("summary_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["run_id", "attempt_id"],
                                ["run_attempts.run_id", "run_attempts.id"],
                                name="fk_events_attempt"),
        sa.UniqueConstraint("run_id", "seq", name="uq_events_run_seq"),
        mysql_engine="InnoDB",
    )
    op.create_table(
        "outbox",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("event_key", sa.String(128), nullable=False),
        sa.Column("run_id", sa.String(32), nullable=False),
        sa.Column("attempt_id", sa.String(32), nullable=False),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("delivery_attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(), nullable=False),
        sa.Column("delivered_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["run_id", "attempt_id"],
                                ["run_attempts.run_id", "run_attempts.id"],
                                name="fk_outbox_attempt"),
        sa.UniqueConstraint("event_key", name="uq_outbox_event_key"),
        mysql_engine="InnoDB",
    )
    op.create_index("ix_outbox_status_available", "outbox",
                    ["status", "available_at"])
    op.create_table(
        "idempotency_records",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("operation_scope", sa.String(128), nullable=False),
        sa.Column("key", sa.String(255), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("resource_type", sa.String(32), nullable=False),
        sa.Column("resource_id", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("operation_scope", "key", name="uq_idempotency_scope_key"),
        mysql_engine="InnoDB",
    )


def downgrade() -> None:
    op.drop_table("idempotency_records")
    op.drop_index("ix_outbox_status_available", table_name="outbox")
    op.drop_table("outbox")
    op.drop_table("run_events")
    op.drop_table("run_attempts")
    op.drop_index("ix_runs_case_created", table_name="agent_runs")
    op.drop_table("agent_runs")
    op.drop_table("messages")
    op.drop_table("conversations")
    op.drop_table("evidence_blocks")
    op.drop_table("document_versions")
    op.drop_table("input_snapshots")
    op.drop_table("suppliers")
    op.drop_table("cases")
