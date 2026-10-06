"""Add project membership as the Case authorization boundary.

Revision ID: 0010_project_access
Revises: 0009_case_cleanup
"""

import sqlalchemy as sa
from alembic import op

revision = "0010_project_access"
down_revision = "0009_case_cleanup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        mysql_engine="InnoDB",
    )
    op.create_table(
        "project_members",
        sa.Column("project_id", sa.String(32),
                  sa.ForeignKey("projects.id"), primary_key=True),
        sa.Column("principal_key", sa.String(64), primary_key=True),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("role IN ('owner', 'editor', 'viewer')",
                           name="ck_project_members_role"),
        mysql_engine="InnoDB",
    )
    op.create_index("ix_project_members_principal", "project_members",
                    ["principal_key"])
    with op.batch_alter_table("cases") as batch:
        batch.add_column(sa.Column("project_id", sa.String(32),
                                   sa.ForeignKey("projects.id",
                                                 name="fk_cases_project")))
    op.create_index("ix_cases_project_created", "cases",
                    ["project_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_cases_project_created", table_name="cases")
    with op.batch_alter_table("cases") as batch:
        batch.drop_column("project_id")
    op.drop_index("ix_project_members_principal",
                  table_name="project_members")
    op.drop_table("project_members")
    op.drop_table("projects")
