"""Add scenarist names to shared project Sheet inbound maps.

Revision ID: 0023_shared_sheet_scenarist
Revises: 0022_project_shared_sources
Create Date: 2026-08-02
"""

import sqlalchemy as sa

from alembic import op

revision = "0023_shared_sheet_scenarist"
down_revision = "0022_project_shared_sources"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    sources = sa.table(
        "sheet_sources",
        sa.column("id", sa.Uuid()),
        sa.column("assigned_scenarist_id", sa.Uuid()),
        sa.column("inbound_column_map", sa.JSON()),
        sa.column("writeback_column_map", sa.JSON()),
    )
    rows = bind.execute(
        sa.select(
            sources.c.id,
            sources.c.inbound_column_map,
            sources.c.writeback_column_map,
        ).where(sources.c.assigned_scenarist_id.is_(None))
    ).mappings()
    for row in rows:
        inbound = dict(row["inbound_column_map"] or {})
        writeback = dict(row["writeback_column_map"] or {})
        scenarist_column = writeback.get("scenarist.name")
        if scenarist_column is None or inbound.get("scenarist.name") == scenarist_column:
            continue
        inbound["scenarist.name"] = scenarist_column
        bind.execute(
            sa.update(sources)
            .where(sources.c.id == row["id"])
            .values(inbound_column_map=inbound)
        )


def downgrade() -> None:
    bind = op.get_bind()
    sources = sa.table(
        "sheet_sources",
        sa.column("id", sa.Uuid()),
        sa.column("assigned_scenarist_id", sa.Uuid()),
        sa.column("inbound_column_map", sa.JSON()),
    )
    rows = bind.execute(
        sa.select(sources.c.id, sources.c.inbound_column_map).where(
            sources.c.assigned_scenarist_id.is_(None)
        )
    ).mappings()
    for row in rows:
        inbound = dict(row["inbound_column_map"] or {})
        filtered = dict(inbound)
        filtered.pop("scenarist.name", None)
        if filtered == inbound:
            continue
        bind.execute(
            sa.update(sources)
            .where(sources.c.id == row["id"])
            .values(inbound_column_map=filtered)
        )
