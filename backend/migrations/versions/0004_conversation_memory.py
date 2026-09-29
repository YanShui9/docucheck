"""Persist bounded conversation memory and clarification replies.

Revision ID: 0004_conversation_memory
Revises: 0003_recovery_precision
"""

import sqlalchemy as sa
from alembic import op

revision = "0004_conversation_memory"
down_revision = "0003_recovery_precision"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_runs", sa.Column("memory_json", sa.JSON(), nullable=True))
    op.create_index("ix_runs_conversation_snapshot_status", "agent_runs",
                    ["conversation_id", "snapshot_id", "status"])
    with op.batch_alter_table("run_attempts") as batch:
        batch.add_column(sa.Column("resume_message_id", sa.String(32), nullable=True))
        batch.create_foreign_key("fk_attempt_resume_message", "messages",
                                 ["resume_message_id"], ["id"])


def downgrade() -> None:
    with op.batch_alter_table("run_attempts") as batch:
        batch.drop_constraint("fk_attempt_resume_message", type_="foreignkey")
        batch.drop_column("resume_message_id")
    op.drop_index("ix_runs_conversation_snapshot_status", table_name="agent_runs")
    op.drop_column("agent_runs", "memory_json")
