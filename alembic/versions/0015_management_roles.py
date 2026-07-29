"""Add editor and publisher manager roles.

Revision ID: 0015_management_roles
Revises: 0014_google_sheets_import
Create Date: 2026-07-29
"""

from alembic import op

revision = "0015_management_roles"
down_revision = "0014_google_sheets_import"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE role ADD VALUE IF NOT EXISTS 'EDITOR_MANAGER'")
        op.execute("ALTER TYPE role ADD VALUE IF NOT EXISTS 'PUBLISHER_MANAGER'")


def downgrade() -> None:
    # Removing PostgreSQL enum values would require rebuilding the type and risks
    # invalidating users. Keeping the values is the safe downgrade behavior.
    pass
