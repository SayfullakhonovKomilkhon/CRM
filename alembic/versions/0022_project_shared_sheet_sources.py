"""Allow one Google Sheets source to serve every scenarist in a project.

Revision ID: 0022_project_shared_sources
Revises: 0021_scope_scenario_id
Create Date: 2026-08-02
"""

import sqlalchemy as sa

from alembic import op

revision = "0022_project_shared_sources"
down_revision = "0021_scope_scenario_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "sheet_sources",
        "assigned_scenarist_id",
        existing_type=sa.Uuid(),
        nullable=True,
    )
    op.add_column(
        "sheet_sources",
        sa.Column(
            "is_project_template",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.create_index(
        "ix_sheet_sources_is_project_template",
        "sheet_sources",
        ["is_project_template"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_sheet_sources_is_project_template", table_name="sheet_sources")
    op.drop_column("sheet_sources", "is_project_template")
    op.alter_column(
        "sheet_sources",
        "assigned_scenarist_id",
        existing_type=sa.Uuid(),
        nullable=False,
    )
