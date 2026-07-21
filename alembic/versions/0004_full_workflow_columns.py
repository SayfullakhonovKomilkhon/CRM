"""Add full scenarist, montage, and publication workflow columns.

Revision ID: 0004_full_workflow
Revises: 0003_client_portal
Create Date: 2026-07-20
"""

import sqlalchemy as sa

from alembic import op

revision = "0004_full_workflow"
down_revision = "0003_client_portal"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    montage_columns = {column["name"] for column in inspector.get_columns("montage_tasks")}
    montage_additions = {
        "source_material_url": sa.Column("source_material_url", sa.Text(), nullable=True),
        "client_brand_style": sa.Column("client_brand_style", sa.Text(), nullable=True),
        "extra_brief": sa.Column("extra_brief", sa.Text(), nullable=True),
        "assigned_editor_id": sa.Column("assigned_editor_id", sa.Uuid(), nullable=True),
        "external_editor_name": sa.Column(
            "external_editor_name", sa.String(length=255), nullable=True
        ),
        "price": sa.Column("price", sa.Numeric(12, 2), nullable=True),
        "material_status": sa.Column("material_status", sa.String(length=100), nullable=True),
        "scenarist_material_comment": sa.Column(
            "scenarist_material_comment", sa.Text(), nullable=True
        ),
        "brief_compliance_status": sa.Column(
            "brief_compliance_status", sa.String(length=100), nullable=True
        ),
        "ready_at": sa.Column("ready_at", sa.Date(), nullable=True),
        "bot_visual_analysis": sa.Column("bot_visual_analysis", sa.Text(), nullable=True),
        "compliance_analysis": sa.Column("compliance_analysis", sa.Text(), nullable=True),
        "ai_analysis": sa.Column("ai_analysis", sa.Text(), nullable=True),
        "scenarist_revision_status": sa.Column(
            "scenarist_revision_status", sa.String(length=100), nullable=True
        ),
        "scenarist_revision_comment": sa.Column(
            "scenarist_revision_comment", sa.Text(), nullable=True
        ),
    }
    for name, column in montage_additions.items():
        if name not in montage_columns:
            op.add_column("montage_tasks", column)
    if "assigned_editor_id" not in montage_columns:
        op.create_foreign_key(
            "fk_montage_tasks_assigned_editor_id_users",
            "montage_tasks",
            "users",
            ["assigned_editor_id"],
            ["id"],
        )
        op.create_index(
            "ix_montage_tasks_assigned_editor_id", "montage_tasks", ["assigned_editor_id"]
        )
    if "ready_at" not in montage_columns:
        op.create_index("ix_montage_tasks_ready_at", "montage_tasks", ["ready_at"])

    publication_columns = {column["name"] for column in inspector.get_columns("publications")}
    publication_additions = {
        "publisher_brief": sa.Column("publisher_brief", sa.Text(), nullable=True),
        "instagram_url": sa.Column("instagram_url", sa.Text(), nullable=True),
        "engagement_metrics": sa.Column("engagement_metrics", sa.Text(), nullable=True),
        "publication_analysis": sa.Column("publication_analysis", sa.Text(), nullable=True),
        "ai_social_descriptions": sa.Column("ai_social_descriptions", sa.Text(), nullable=True),
        "leia_script": sa.Column("leia_script", sa.Text(), nullable=True),
    }
    for name, column in publication_additions.items():
        if name not in publication_columns:
            op.add_column("publications", column)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    publication_columns = {column["name"] for column in inspector.get_columns("publications")}
    for name in (
        "leia_script",
        "ai_social_descriptions",
        "publication_analysis",
        "engagement_metrics",
        "instagram_url",
        "publisher_brief",
    ):
        if name in publication_columns:
            op.drop_column("publications", name)

    montage_columns = {column["name"] for column in inspector.get_columns("montage_tasks")}
    if "ready_at" in montage_columns:
        op.drop_index("ix_montage_tasks_ready_at", table_name="montage_tasks")
    if "assigned_editor_id" in montage_columns:
        op.drop_index("ix_montage_tasks_assigned_editor_id", table_name="montage_tasks")
        op.drop_constraint(
            "fk_montage_tasks_assigned_editor_id_users", "montage_tasks", type_="foreignkey"
        )
    for name in (
        "scenarist_revision_comment",
        "scenarist_revision_status",
        "ai_analysis",
        "compliance_analysis",
        "bot_visual_analysis",
        "ready_at",
        "brief_compliance_status",
        "scenarist_material_comment",
        "material_status",
        "price",
        "external_editor_name",
        "assigned_editor_id",
        "extra_brief",
        "client_brand_style",
        "source_material_url",
    ):
        if name in montage_columns:
            op.drop_column("montage_tasks", name)
