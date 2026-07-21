"""Add a separate approval note.

Revision ID: 0008_approval_note
Revises: 0007_approval_rejected
Create Date: 2026-07-20
"""

import sqlalchemy as sa

from alembic import op

revision = "0008_approval_note"
down_revision = "0007_approval_rejected"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("scenario_approvals")
    }
    if "note" not in columns:
        op.add_column("scenario_approvals", sa.Column("note", sa.Text(), nullable=True))


def downgrade() -> None:
    columns = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("scenario_approvals")
    }
    if "note" in columns:
        op.drop_column("scenario_approvals", "note")
