"""Add explicit scenarist publication preparation status.

Revision ID: 0020_publication_preparation
Revises: 0019_admin_submission
"""

import sqlalchemy as sa

from alembic import op

revision = "0020_publication_preparation"
down_revision = "0019_admin_submission"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "publications",
        sa.Column(
            "preparation_status",
            sa.String(length=32),
            server_default="draft",
            nullable=False,
        ),
    )
    op.create_index(
        "ix_publications_preparation_status",
        "publications",
        ["preparation_status"],
    )


def downgrade() -> None:
    op.drop_index("ix_publications_preparation_status", table_name="publications")
    op.drop_column("publications", "preparation_status")
