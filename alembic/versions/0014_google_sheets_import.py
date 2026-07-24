"""Add safe Google Sheets import metadata and sync log.

Revision ID: 0014_google_sheets_import
Revises: 0013_final_workflow
Create Date: 2026-07-24
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0014_google_sheets_import"
down_revision = "0013_final_workflow"
branch_labels = None
depends_on = None


def upgrade() -> None:
    sync_mode = postgresql.ENUM(
        "PREVIEW",
        "SYNC",
        name="google_sheets_sync_mode",
        create_type=False,
    )
    sync_status = postgresql.ENUM(
        "PREVIEW_READY",
        "COMPLETED",
        "VALIDATION_FAILED",
        "FAILED",
        name="google_sheets_sync_status",
        create_type=False,
    )
    sync_mode.create(op.get_bind(), checkfirst=True)
    sync_status.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "scenarios",
        sa.Column("source_checksum", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_scenarios_source_checksum",
        "scenarios",
        ["source_checksum"],
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM scenarios
                WHERE source_sheet_id IS NOT NULL
                  AND source_tab IS NOT NULL
                  AND source_row IS NOT NULL
                GROUP BY source_sheet_id, source_tab, source_row
                HAVING count(*) > 1
            ) THEN
                RAISE EXCEPTION
                    'Duplicate Google Sheets source rows must be resolved before migration';
            END IF;
        END
        $$;
        """
    )
    op.create_unique_constraint(
        "uq_scenario_source_row",
        "scenarios",
        ["source_sheet_id", "source_tab", "source_row"],
    )

    op.create_table(
        "google_sheets_sync_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("spreadsheet_id", sa.String(length=255), nullable=False),
        sa.Column("source_tab", sa.String(length=255), nullable=False),
        sa.Column("header_row", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("requested_by_id", sa.Uuid(), nullable=False),
        sa.Column("preview_id", sa.Uuid(), nullable=True),
        sa.Column("mode", sync_mode, nullable=False),
        sa.Column("status", sync_status, nullable=False),
        sa.Column("snapshot_checksum", sa.String(length=64), nullable=False),
        sa.Column("total_rows", sa.Integer(), nullable=False),
        sa.Column("created_count", sa.Integer(), nullable=False),
        sa.Column("updated_count", sa.Integer(), nullable=False),
        sa.Column("skipped_count", sa.Integer(), nullable=False),
        sa.Column("error_count", sa.Integer(), nullable=False),
        sa.Column("row_report", sa.JSON(), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["preview_id"],
            ["google_sheets_sync_runs.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["requested_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_google_sheets_sync_runs_spreadsheet_id",
        "google_sheets_sync_runs",
        ["spreadsheet_id"],
    )
    op.create_index(
        "ix_google_sheets_sync_runs_source_tab",
        "google_sheets_sync_runs",
        ["source_tab"],
    )
    op.create_index(
        "ix_google_sheets_sync_runs_project_id",
        "google_sheets_sync_runs",
        ["project_id"],
    )
    op.create_index(
        "ix_google_sheets_sync_runs_requested_by_id",
        "google_sheets_sync_runs",
        ["requested_by_id"],
    )
    op.create_index(
        "ix_google_sheets_sync_runs_mode",
        "google_sheets_sync_runs",
        ["mode"],
    )
    op.create_index(
        "ix_google_sheets_sync_runs_status",
        "google_sheets_sync_runs",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_google_sheets_sync_runs_status",
        table_name="google_sheets_sync_runs",
    )
    op.drop_index(
        "ix_google_sheets_sync_runs_mode",
        table_name="google_sheets_sync_runs",
    )
    op.drop_index(
        "ix_google_sheets_sync_runs_requested_by_id",
        table_name="google_sheets_sync_runs",
    )
    op.drop_index(
        "ix_google_sheets_sync_runs_project_id",
        table_name="google_sheets_sync_runs",
    )
    op.drop_index(
        "ix_google_sheets_sync_runs_source_tab",
        table_name="google_sheets_sync_runs",
    )
    op.drop_index(
        "ix_google_sheets_sync_runs_spreadsheet_id",
        table_name="google_sheets_sync_runs",
    )
    op.drop_table("google_sheets_sync_runs")
    op.drop_constraint("uq_scenario_source_row", "scenarios", type_="unique")
    op.drop_index("ix_scenarios_source_checksum", table_name="scenarios")
    op.drop_column("scenarios", "source_checksum")
    sa.Enum(name="google_sheets_sync_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="google_sheets_sync_mode").drop(op.get_bind(), checkfirst=True)
