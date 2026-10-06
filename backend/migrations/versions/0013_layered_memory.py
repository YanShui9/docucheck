"""Layered conversation and explicitly confirmed personal memory."""

import sqlalchemy as sa
from alembic import op

from backend.app.db.memory_schema_v1 import define_memory_tables

revision = "0013_layered_memory"
down_revision = "0012_durable_indexes"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    # MySQL DDL commits each operation. Resume this migration safely if a later
    # CREATE TABLE failed, preserving all existing messages and Run records.
    inspector = sa.inspect(conn)
    if "sequence" not in {c["name"] for c in inspector.get_columns("messages")}:
        op.add_column("messages", sa.Column("sequence", sa.Integer()))
    message_table = sa.table(
        "messages",
        sa.column("id", sa.String),
        sa.column("conversation_id", sa.String),
        sa.column("created_at", sa.DateTime),
        sa.column("sequence", sa.Integer),
    )
    cursor, last_conversation, sequence = None, None, 0
    while True:
        query = sa.select(
            message_table.c.id,
            message_table.c.conversation_id,
            message_table.c.created_at,
        )
        if cursor is not None:
            conv, created, message_id = cursor
            query = query.where(
                sa.or_(
                    message_table.c.conversation_id > conv,
                    sa.and_(
                        message_table.c.conversation_id == conv,
                        message_table.c.created_at > created,
                    ),
                    sa.and_(
                        message_table.c.conversation_id == conv,
                        message_table.c.created_at == created,
                        message_table.c.id > message_id,
                    ),
                )
            )
        rows = conn.execute(
            query.order_by(
                message_table.c.conversation_id,
                message_table.c.created_at,
                message_table.c.id,
            ).limit(500)
        ).all()
        if not rows:
            break
        for row in rows:
            sequence = sequence + 1 if row.conversation_id == last_conversation else 1
            last_conversation = row.conversation_id
            conn.execute(
                sa.update(message_table)
                .where(message_table.c.id == row.id)
                .values(sequence=sequence)
            )
        last = rows[-1]
        cursor = (last.conversation_id, last.created_at, last.id)
    with op.batch_alter_table("messages") as batch:
        if "uq_message_sequence" not in {
            c["name"] for c in inspector.get_unique_constraints("messages")
        }:
            batch.create_unique_constraint(
                "uq_message_sequence", ["conversation_id", "sequence"]
            )
        if "ck_message_sequence" not in {
            c["name"] for c in inspector.get_check_constraints("messages")
        }:
            batch.create_check_constraint(
                "ck_message_sequence", "sequence IS NULL OR sequence >= 1"
            )
    if "context_manifest_json" not in {
        c["name"] for c in inspector.get_columns("agent_runs")
    }:
        op.add_column("agent_runs", sa.Column("context_manifest_json", sa.JSON()))
    with op.batch_alter_table("agent_runs") as batch:
        if "uq_run_memory_scope" not in {
            c["name"] for c in inspector.get_unique_constraints("agent_runs")
        }:
            batch.create_unique_constraint(
                "uq_run_memory_scope",
                ["case_id", "conversation_id", "snapshot_id", "id"],
            )
    meta = sa.MetaData()
    for name, columns in (
        ("conversations", ["case_id", "id"]),
        ("input_snapshots", ["case_id", "id"]),
        ("agent_runs", ["case_id", "conversation_id", "snapshot_id", "id"]),
    ):
        sa.Table(name, meta, *(sa.Column(name, sa.String(64)) for name in columns))
    for table in define_memory_tables(meta):
        table.create(conn, checkfirst=True)


def downgrade():
    for name in (
        "conversation_summaries",
        "task_memory",
        "memory_records",
        "memory_profiles",
    ):
        op.drop_table(name)
    with op.batch_alter_table("agent_runs") as batch:
        batch.drop_constraint("uq_run_memory_scope", type_="unique")
    op.drop_column("agent_runs", "context_manifest_json")
    with op.batch_alter_table("messages") as batch:
        batch.drop_constraint("uq_message_sequence", type_="unique")
        batch.drop_constraint("ck_message_sequence", type_="check")
        batch.drop_column("sequence")
