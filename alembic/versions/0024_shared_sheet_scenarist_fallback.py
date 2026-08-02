"""Repair shared sources whose template omitted the scenarist writeback field.

Revision ID: 0024_shared_sheet_scenarist_fallback
Revises: 0023_shared_sheet_scenarist
Create Date: 2026-08-02
"""

import sqlalchemy as sa

from alembic import op

revision = "0024_shared_scenarist_fallback"
down_revision = "0023_shared_sheet_scenarist"
branch_labels = None
depends_on = None


def upgrade() -> None:
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
        if inbound.get("scenarist.name") == "P":
            continue
        inbound["scenarist.name"] = "P"
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
        if inbound.get("scenarist.name") != "P":
            continue
        inbound.pop("scenarist.name", None)
        bind.execute(
            sa.update(sources)
            .where(sources.c.id == row["id"])
            .values(inbound_column_map=inbound)
        )
