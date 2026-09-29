"""Preserve microseconds for Run leases and Outbox reconciliation.

Revision ID: 0003_recovery_precision
Revises: 0002_run_recovery
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0003_recovery_precision"
down_revision = "0002_run_recovery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "mysql":
        return
    op.alter_column("agent_runs", "updated_at",
                    existing_type=sa.DateTime(),
                    type_=mysql.DATETIME(fsp=6), existing_nullable=False)
    op.alter_column("outbox", "available_at",
                    existing_type=sa.DateTime(),
                    type_=mysql.DATETIME(fsp=6), existing_nullable=False)
    op.alter_column("outbox", "delivered_at",
                    existing_type=sa.DateTime(),
                    type_=mysql.DATETIME(fsp=6), existing_nullable=True)


def downgrade() -> None:
    if op.get_bind().dialect.name != "mysql":
        return
    op.alter_column("outbox", "delivered_at",
                    existing_type=mysql.DATETIME(fsp=6),
                    type_=sa.DateTime(), existing_nullable=True)
    op.alter_column("outbox", "available_at",
                    existing_type=mysql.DATETIME(fsp=6),
                    type_=sa.DateTime(), existing_nullable=False)
    op.alter_column("agent_runs", "updated_at",
                    existing_type=mysql.DATETIME(fsp=6),
                    type_=sa.DateTime(), existing_nullable=False)
