"""Add client portal montage and publication data.

Revision ID: 0003_client_portal
Revises: 0002_dashboard
Create Date: 2026-07-20
"""

import sqlalchemy as sa

from alembic import op

revision = "0003_client_portal"
down_revision = "0002_dashboard"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "montage_tasks" not in tables:
        op.create_table(
            "montage_tasks",
            sa.Column("scenario_id", sa.Uuid(), nullable=False),
            sa.Column("ready_material_url", sa.Text(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(["scenario_id"], ["scenarios.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("scenario_id"),
        )
    if "publications" not in tables:
        op.create_table(
            "publications",
            sa.Column("scenario_id", sa.Uuid(), nullable=False),
            sa.Column("description_dzen", sa.Text(), nullable=True),
            sa.Column("description_youtube", sa.Text(), nullable=True),
            sa.Column("description_tiktok", sa.Text(), nullable=True),
            sa.Column("description_instagram", sa.Text(), nullable=True),
            sa.Column("publication_date", sa.Date(), nullable=True),
            sa.Column("is_published", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(["scenario_id"], ["scenarios.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("scenario_id"),
        )
        op.create_index("ix_publications_publication_date", "publications", ["publication_date"])
        op.create_index("ix_publications_is_published", "publications", ["is_published"])


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "publications" in tables:
        op.drop_table("publications")
    if "montage_tasks" in tables:
        op.drop_table("montage_tasks")
