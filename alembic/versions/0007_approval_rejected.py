"""Add rejected approval decision.

Revision ID: 0007_approval_rejected
Revises: 0006_editor_payment_date
Create Date: 2026-07-20
"""

from alembic import op

revision = "0007_approval_rejected"
down_revision = "0006_editor_payment_date"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_enum
                JOIN pg_type ON pg_enum.enumtypid = pg_type.oid
                WHERE pg_type.typname = 'approval_decision'
                  AND pg_enum.enumlabel = 'REJECTED'
            ) THEN
                ALTER TYPE approval_decision ADD VALUE 'REJECTED';
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE scenario_approvals
        ALTER COLUMN decision TYPE VARCHAR
        USING decision::text;

        UPDATE scenario_approvals
        SET decision = 'REVISION'
        WHERE decision = 'REJECTED';

        DROP TYPE approval_decision;
        CREATE TYPE approval_decision AS ENUM ('PENDING', 'APPROVED', 'REVISION');

        ALTER TABLE scenario_approvals
        ALTER COLUMN decision TYPE approval_decision
        USING decision::approval_decision;
        """
    )
