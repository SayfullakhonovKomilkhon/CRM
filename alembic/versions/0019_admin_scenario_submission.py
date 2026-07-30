"""Add administrator role and terminal rejected scenario status.

Revision ID: 0019_admin_submission
Revises: 0018_sheet_source_payload
"""

from alembic import op

revision = "0019_admin_submission"
down_revision = "0018_sheet_source_payload"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE role ADD VALUE IF NOT EXISTS 'ADMIN'")
        op.execute(
            "ALTER TYPE scenario_status ADD VALUE IF NOT EXISTS 'REJECTED'"
        )


def downgrade() -> None:
    # PostgreSQL enum values are intentionally retained: rebuilding live enum
    # types is destructive and unsafe when users or scenarios reference them.
    pass
