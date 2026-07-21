"""Enforce API business identifiers at the database boundary.

Revision ID: 0011_business_keys
Revises: 0010_publication_history
Create Date: 2026-07-22
"""

from alembic import op

revision = "0011_business_keys"
down_revision = "0010_publication_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_clients_external_id",
        "clients",
        ["external_id"],
    )
    op.create_unique_constraint(
        "uq_scenario_project_external_id",
        "scenarios",
        ["project_id", "external_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_scenario_project_external_id",
        "scenarios",
        type_="unique",
    )
    op.drop_constraint("uq_clients_external_id", "clients", type_="unique")
