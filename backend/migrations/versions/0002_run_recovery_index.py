"""Index running Run heartbeat age for recovery sweeps.

Revision ID: 0002_run_recovery
Revises: 0001_core
"""

from alembic import op

revision = "0002_run_recovery"
down_revision = "0001_core"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_runs_status_updated", "agent_runs",
                    ["status", "updated_at"])


def downgrade() -> None:
    op.drop_index("ix_runs_status_updated", table_name="agent_runs")
