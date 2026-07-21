"""Generate stable sequential scenario external IDs.

Revision ID: 0012_scenario_id_seq
Revises: 0011_business_keys
Create Date: 2026-07-22
"""

import sqlalchemy as sa

from alembic import op

revision = "0012_scenario_id_seq"
down_revision = "0011_business_keys"
branch_labels = None
depends_on = None

SEQUENCE_NAME = "scenario_external_id_seq"


def upgrade() -> None:
    op.drop_constraint(
        "uq_scenario_project_external_id",
        "scenarios",
        type_="unique",
    )
    op.execute(f"CREATE SEQUENCE {SEQUENCE_NAME} START WITH 1 INCREMENT BY 1 NO CYCLE")
    op.execute("UPDATE scenarios SET external_id = NULL")
    op.execute(
        """
        WITH numbered AS (
            SELECT id, row_number() OVER (ORDER BY created_at, id) AS number
            FROM scenarios
        )
        UPDATE scenarios AS scenario
        SET external_id = numbered.number::text
        FROM numbered
        WHERE scenario.id = numbered.id
        """
    )
    op.execute(
        f"""
        SELECT setval(
            '{SEQUENCE_NAME}',
            COALESCE((SELECT max(external_id::bigint) FROM scenarios), 1),
            EXISTS(SELECT 1 FROM scenarios)
        )
        """
    )
    op.alter_column(
        "scenarios",
        "external_id",
        existing_type=sa.String(length=100),
        nullable=False,
        server_default=sa.text(f"nextval('{SEQUENCE_NAME}'::regclass)::text"),
    )
    op.create_unique_constraint(
        "uq_scenarios_external_id",
        "scenarios",
        ["external_id"],
    )
    op.execute(f"ALTER SEQUENCE {SEQUENCE_NAME} OWNED BY scenarios.external_id")


def downgrade() -> None:
    op.drop_constraint("uq_scenarios_external_id", "scenarios", type_="unique")
    op.alter_column(
        "scenarios",
        "external_id",
        existing_type=sa.String(length=100),
        nullable=True,
        server_default=None,
    )
    op.create_unique_constraint(
        "uq_scenario_project_external_id",
        "scenarios",
        ["project_id", "external_id"],
    )
    op.execute(f"DROP SEQUENCE {SEQUENCE_NAME}")
