"""Add bidirectional Google Sheets synchronization foundation.

Revision ID: 0016_sheet_bidirectional
Revises: 0015_management_roles
Create Date: 2026-07-29
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0016_sheet_bidirectional"
down_revision = "0015_management_roles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    event_status = postgresql.ENUM(
        "RECEIVED",
        "PROCESSING",
        "COMPLETED",
        "SKIPPED",
        "FAILED",
        name="sheet_event_status",
        create_type=False,
    )
    writeback_status = postgresql.ENUM(
        "PENDING",
        "PROCESSING",
        "COMPLETED",
        "FAILED",
        name="sheet_writeback_status",
        create_type=False,
    )
    event_status.create(op.get_bind(), checkfirst=True)
    writeback_status.create(op.get_bind(), checkfirst=True)
    inspector = sa.inspect(op.get_bind())

    if not inspector.has_table("sheet_sources"):
        op.create_table(
            "sheet_sources",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("spreadsheet_id", sa.String(255), nullable=False),
            sa.Column("source_tab", sa.String(255), nullable=False),
            sa.Column("project_id", sa.Uuid(), nullable=False),
            sa.Column("assigned_scenarist_id", sa.Uuid(), nullable=False),
            sa.Column("header_row", sa.Integer(), nullable=False),
            sa.Column("inbound_column_map", sa.JSON(), nullable=False),
            sa.Column("writeback_column_map", sa.JSON(), nullable=False),
            sa.Column("crm_row_id_column", sa.String(3), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False),
            sa.Column("webhook_secret_version", sa.Integer(), nullable=False),
            sa.Column("last_status", sa.String(50), nullable=True),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("last_event_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
            sa.ForeignKeyConstraint(["assigned_scenarist_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "spreadsheet_id", "source_tab", name="uq_sheet_source_location"
            ),
        )
        for name, columns in (
            ("ix_sheet_sources_spreadsheet_id", ["spreadsheet_id"]),
            ("ix_sheet_sources_project_id", ["project_id"]),
            ("ix_sheet_sources_assigned_scenarist_id", ["assigned_scenarist_id"]),
            ("ix_sheet_sources_enabled", ["enabled"]),
        ):
            op.create_index(name, "sheet_sources", columns)

    scenario_columns = {
        column["name"] for column in inspector.get_columns("scenarios")
    }
    if "sheet_source_id" not in scenario_columns:
        op.add_column("scenarios", sa.Column("sheet_source_id", sa.Uuid(), nullable=True))
        op.create_foreign_key(
            "fk_scenarios_sheet_source",
            "scenarios",
            "sheet_sources",
            ["sheet_source_id"],
            ["id"],
            ondelete="SET NULL",
        )
        op.create_index("ix_scenarios_sheet_source_id", "scenarios", ["sheet_source_id"])
    if "crm_row_id" not in scenario_columns:
        op.add_column("scenarios", sa.Column("crm_row_id", sa.Uuid(), nullable=True))
    constraints = {
        constraint["name"] for constraint in inspector.get_unique_constraints("scenarios")
    }
    if "uq_scenario_sheet_source_crm_row" not in constraints:
        op.create_unique_constraint(
            "uq_scenario_sheet_source_crm_row",
            "scenarios",
            ["sheet_source_id", "crm_row_id"],
        )

    if not inspector.has_table("sheet_inbound_events"):
        op.create_table(
            "sheet_inbound_events",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("event_id", sa.String(255), nullable=False),
            sa.Column("schema_version", sa.Integer(), nullable=False),
            sa.Column("source_id", sa.Uuid(), nullable=False),
            sa.Column("crm_row_id", sa.Uuid(), nullable=False),
            sa.Column("row_number", sa.Integer(), nullable=False),
            sa.Column("changed_fields", sa.JSON(), nullable=False),
            sa.Column("raw", sa.JSON(), nullable=False),
            sa.Column("checksum", sa.String(64), nullable=False),
            sa.Column("origin", sa.String(50), nullable=False),
            sa.Column("correlation_id", sa.String(255), nullable=True),
            sa.Column("status", event_status, nullable=False),
            sa.Column("attempts", sa.Integer(), nullable=False),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["source_id"], ["sheet_sources.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("event_id", name="uq_sheet_inbound_event_id"),
        )
        for name, columns in (
            ("ix_sheet_inbound_events_event_id", ["event_id"]),
            ("ix_sheet_inbound_events_source_id", ["source_id"]),
            ("ix_sheet_inbound_events_crm_row_id", ["crm_row_id"]),
            ("ix_sheet_inbound_events_checksum", ["checksum"]),
            ("ix_sheet_inbound_events_correlation_id", ["correlation_id"]),
            ("ix_sheet_inbound_events_status", ["status"]),
        ):
            op.create_index(name, "sheet_inbound_events", columns)

    if not inspector.has_table("sheet_writeback_events"):
        op.create_table(
            "sheet_writeback_events",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("source_id", sa.Uuid(), nullable=False),
            sa.Column("scenario_id", sa.Uuid(), nullable=False),
            sa.Column("crm_row_id", sa.Uuid(), nullable=False),
            sa.Column("changed_fields", sa.JSON(), nullable=False),
            sa.Column("checksum", sa.String(64), nullable=False),
            sa.Column("origin", sa.String(50), nullable=False),
            sa.Column("correlation_id", sa.String(255), nullable=False),
            sa.Column("status", writeback_status, nullable=False),
            sa.Column("attempts", sa.Integer(), nullable=False),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["source_id"], ["sheet_sources.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["scenario_id"], ["scenarios.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("correlation_id", name="uq_sheet_writeback_correlation_id"),
        )
        for name, columns in (
            ("ix_sheet_writeback_events_source_id", ["source_id"]),
            ("ix_sheet_writeback_events_scenario_id", ["scenario_id"]),
            ("ix_sheet_writeback_events_crm_row_id", ["crm_row_id"]),
            ("ix_sheet_writeback_events_checksum", ["checksum"]),
            ("ix_sheet_writeback_events_correlation_id", ["correlation_id"]),
            ("ix_sheet_writeback_events_status", ["status"]),
            ("ix_sheet_writeback_events_next_attempt_at", ["next_attempt_at"]),
        ):
            op.create_index(name, "sheet_writeback_events", columns)


def downgrade() -> None:
    op.drop_table("sheet_writeback_events")
    op.drop_table("sheet_inbound_events")
    op.drop_constraint("uq_scenario_sheet_source_crm_row", "scenarios", type_="unique")
    op.drop_index("ix_scenarios_sheet_source_id", table_name="scenarios")
    op.drop_constraint("fk_scenarios_sheet_source", "scenarios", type_="foreignkey")
    op.drop_column("scenarios", "crm_row_id")
    op.drop_column("scenarios", "sheet_source_id")
    op.drop_table("sheet_sources")
    writeback = sa.Enum(name="sheet_writeback_status")
    event = sa.Enum(name="sheet_event_status")
    writeback.drop(op.get_bind(), checkfirst=True)
    event.drop(op.get_bind(), checkfirst=True)
