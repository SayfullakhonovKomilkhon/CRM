"""Add dashboard fields and scenario comments.

Revision ID: 0002_dashboard
Revises: 0001_initial
Create Date: 2026-07-20
"""

import sqlalchemy as sa

from alembic import op

revision = "0002_dashboard"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    scenario_columns = {column["name"] for column in inspector.get_columns("scenarios")}
    if "deadline" not in scenario_columns:
        op.add_column("scenarios", sa.Column("deadline", sa.Date(), nullable=True))
        op.create_index("ix_scenarios_deadline", "scenarios", ["deadline"])
    if "score" not in scenario_columns:
        op.add_column("scenarios", sa.Column("score", sa.Integer(), nullable=True))

    if "scenario_comments" not in inspector.get_table_names():
        op.create_table(
            "scenario_comments",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("scenario_id", sa.Uuid(), nullable=False),
            sa.Column("author_id", sa.Uuid(), nullable=False),
            sa.Column("stage", sa.String(length=100), nullable=True),
            sa.Column("text", sa.Text(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(["author_id"], ["users.id"]),
            sa.ForeignKeyConstraint(["scenario_id"], ["scenarios.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_scenario_comments_author_id", "scenario_comments", ["author_id"])
        op.create_index("ix_scenario_comments_scenario_id", "scenario_comments", ["scenario_id"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "scenario_comments" in inspector.get_table_names():
        op.drop_table("scenario_comments")
    scenario_columns = {column["name"] for column in inspector.get_columns("scenarios")}
    if "score" in scenario_columns:
        op.drop_column("scenarios", "score")
    if "deadline" in scenario_columns:
        op.drop_index("ix_scenarios_deadline", table_name="scenarios")
        op.drop_column("scenarios", "deadline")
