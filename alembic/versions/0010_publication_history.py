"""Remember whether a publication was previously enabled.

Revision ID: 0010_publication_history
Revises: 0009_repair_demo_workflow
Create Date: 2026-07-21
"""

import sqlalchemy as sa

from alembic import op

revision = "0010_publication_history"
down_revision = "0009_repair_demo_workflow"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("publications")}
    if "first_published_at" not in columns:
        op.add_column(
            "publications",
            sa.Column("first_published_at", sa.DateTime(timezone=True), nullable=True),
        )
    op.execute(
        """
        UPDATE publications AS publication
        SET first_published_at = COALESCE(publication.updated_at, now())
        FROM scenarios AS scenario
        WHERE publication.scenario_id = scenario.id
          AND publication.first_published_at IS NULL
          AND (publication.is_published = true OR scenario.status = 'PUBLISHED')
        """
    )


def downgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("publications")}
    if "first_published_at" in columns:
        op.drop_column("publications", "first_published_at")
