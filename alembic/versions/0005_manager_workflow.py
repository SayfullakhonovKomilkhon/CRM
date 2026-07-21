"""Add editor-owned status and comment fields.

Revision ID: 0005_manager_workflow
Revises: 0004_full_workflow
Create Date: 2026-07-20
"""

import sqlalchemy as sa

from alembic import op

revision = "0005_manager_workflow"
down_revision = "0004_full_workflow"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("montage_tasks")}
    if "editor_status" not in columns:
        op.add_column(
            "montage_tasks", sa.Column("editor_status", sa.String(length=100), nullable=True)
        )
    if "editor_comment" not in columns:
        op.add_column("montage_tasks", sa.Column("editor_comment", sa.Text(), nullable=True))


def downgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("montage_tasks")}
    if "editor_comment" in columns:
        op.drop_column("montage_tasks", "editor_comment")
    if "editor_status" in columns:
        op.drop_column("montage_tasks", "editor_status")
