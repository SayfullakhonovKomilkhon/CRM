"""Ensure the protected Google Sheets row identity column is registered.

Revision ID: 0017_sheet_identity
Revises: 0016_sheet_bidirectional
Create Date: 2026-07-29
"""

import sqlalchemy as sa

from alembic import op

revision = "0017_sheet_identity"
down_revision = "0016_sheet_bidirectional"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("sheet_sources")
    }
    if "crm_row_id_column" not in columns:
        op.add_column(
            "sheet_sources",
            sa.Column(
                "crm_row_id_column",
                sa.String(length=3),
                server_default="A",
                nullable=False,
            ),
        )
        op.alter_column(
            "sheet_sources",
            "crm_row_id_column",
            server_default=None,
        )


def downgrade() -> None:
    columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("sheet_sources")
    }
    if "crm_row_id_column" in columns:
        op.drop_column("sheet_sources", "crm_row_id_column")
