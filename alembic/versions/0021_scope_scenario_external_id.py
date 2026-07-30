"""Treat Google Sheet scenario IDs as non-unique display identifiers.

Revision ID: 0021_scope_scenario_id
Revises: 0020_publication_preparation
Create Date: 2026-07-30
"""

import sqlalchemy as sa

from alembic import op

revision = "0021_scope_scenario_id"
down_revision = "0020_publication_preparation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    constraint_names = {
        constraint["name"] for constraint in inspector.get_unique_constraints("scenarios")
    }
    if "uq_scenarios_external_id" in constraint_names:
        op.drop_constraint(
            "uq_scenarios_external_id",
            "scenarios",
            type_="unique",
        )
    if "uq_scenario_source" in constraint_names:
        op.drop_constraint(
            "uq_scenario_source",
            "scenarios",
            type_="unique",
        )


def downgrade() -> None:
    op.create_unique_constraint(
        "uq_scenario_source",
        "scenarios",
        ["source_sheet_id", "source_tab", "external_id"],
    )
    op.create_unique_constraint(
        "uq_scenarios_external_id",
        "scenarios",
        ["external_id"],
    )
