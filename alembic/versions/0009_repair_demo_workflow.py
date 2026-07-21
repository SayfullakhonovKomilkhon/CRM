"""Repair the inconsistent manager demo workflow.

Revision ID: 0009_repair_demo_workflow
Revises: 0008_approval_note
Create Date: 2026-07-21
"""

from alembic import op

revision = "0009_repair_demo_workflow"
down_revision = "0008_approval_note"
branch_labels = None
depends_on = None


def upgrade() -> None:
    statements = (
        """
        UPDATE scenario_content AS content
        SET script_text = COALESCE(
                NULLIF(content.script_text, ''),
                NULLIF(content.cover_text, ''),
                scenario.external_id
            ),
            updated_at = now()
        FROM scenarios AS scenario
        WHERE content.scenario_id = scenario.id
          AND scenario.external_id = 'manager-3f4151fe'
        """,
        """
        INSERT INTO scenario_approvals (
            id,
            scenario_id,
            stage,
            decision,
            comment,
            decided_by_id,
            decided_at,
            created_at,
            updated_at
        )
        SELECT
            gen_random_uuid(),
            scenario.id,
            'RESPONSIBLE_REVIEW',
            'APPROVED',
            'Внутренняя проверка демо-сценария восстановлена',
            manager.id,
            now(),
            now(),
            now()
        FROM scenarios AS scenario
        LEFT JOIN users AS manager ON manager.email = 'manager@crm.local'
        WHERE scenario.external_id = 'manager-3f4151fe'
        ON CONFLICT (scenario_id, stage) DO UPDATE
        SET decision = 'APPROVED',
            decided_by_id = EXCLUDED.decided_by_id,
            decided_at = EXCLUDED.decided_at,
            updated_at = now()
        """,
        """
        INSERT INTO scenario_approvals (
            id,
            scenario_id,
            stage,
            decision,
            created_at,
            updated_at
        )
        SELECT
            gen_random_uuid(),
            scenario.id,
            'PRE_GENERATION_CLIENT',
            'PENDING',
            now(),
            now()
        FROM scenarios AS scenario
        WHERE scenario.external_id = 'manager-3f4151fe'
        ON CONFLICT (scenario_id, stage) DO UPDATE
        SET decision = 'PENDING',
            decided_by_id = NULL,
            decided_at = NULL,
            updated_at = now()
        """,
        """
        UPDATE scenario_approvals AS approval
        SET decision = 'PENDING',
            decided_by_id = NULL,
            decided_at = NULL,
            updated_at = now()
        FROM scenarios AS scenario
        WHERE approval.scenario_id = scenario.id
          AND scenario.external_id = 'manager-3f4151fe'
          AND approval.stage IN ('SOURCE_MATERIAL', 'MONTAGE_COMPLIANCE', 'FINAL_CLIENT')
        """,
        """
        UPDATE scenarios
        SET status = 'CLIENT_REVIEW',
            updated_at = now()
        WHERE external_id = 'manager-3f4151fe'
        """,
    )
    for statement in statements:
        op.execute(statement)


def downgrade() -> None:
    # This migration repairs inconsistent demo data. Reintroducing the invalid
    # late-stage approvals during downgrade would be unsafe.
    pass
