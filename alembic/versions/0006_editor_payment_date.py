"""Add the editor payment due date.

Revision ID: 0006_editor_payment_date
Revises: 0005_manager_workflow
Create Date: 2026-07-20
"""

import sqlalchemy as sa

from alembic import op

revision = "0006_editor_payment_date"
down_revision = "0005_manager_workflow"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("montage_tasks")}
    if "payment_due_date" not in columns:
        op.add_column("montage_tasks", sa.Column("payment_due_date", sa.Date(), nullable=True))
        op.create_index("ix_montage_tasks_payment_due_date", "montage_tasks", ["payment_due_date"])


def downgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("montage_tasks")}
    if "payment_due_date" in columns:
        op.drop_index("ix_montage_tasks_payment_due_date", table_name="montage_tasks")
        op.drop_column("montage_tasks", "payment_due_date")
