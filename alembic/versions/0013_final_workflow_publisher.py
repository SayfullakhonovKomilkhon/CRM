"""Add final revision gate and publisher workflow.

Revision ID: 0013_final_workflow
Revises: 0012_scenario_id_seq
Create Date: 2026-07-23
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0013_final_workflow"
down_revision = "0012_scenario_id_seq"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE role ADD VALUE IF NOT EXISTS 'PUBLISHER'")
        op.execute(
            "ALTER TYPE scenario_status ADD VALUE IF NOT EXISTS 'MANAGER_REVISION_REVIEW'"
        )
        op.execute("ALTER TYPE scenario_status ADD VALUE IF NOT EXISTS 'READY_TO_PUBLISH'")

    gate_decision = postgresql.ENUM(
        "PENDING", "APPROVED", "REJECTED", name="gate_decision", create_type=False
    )
    publication_review = postgresql.ENUM(
        "PENDING",
        "APPROVED",
        "REVISION",
        name="publication_review_decision",
        create_type=False,
    )
    publisher_status = postgresql.ENUM(
        "PENDING",
        "ASSIGNED",
        "IN_PROGRESS",
        "PUBLISHED",
        name="publisher_status",
        create_type=False,
    )
    gate_decision.create(op.get_bind(), checkfirst=True)
    publication_review.create(op.get_bind(), checkfirst=True)
    publisher_status.create(op.get_bind(), checkfirst=True)

    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("final_client_revision_gates"):
        op.create_table(
            "final_client_revision_gates",
            sa.Column("scenario_id", sa.Uuid(), nullable=False),
            sa.Column(
                "decision",
                gate_decision,
                server_default=sa.text("'PENDING'"),
                nullable=False,
            ),
            sa.Column("request_comment", sa.Text(), nullable=False),
            sa.Column("manager_comment", sa.Text(), nullable=True),
            sa.Column("decided_by_id", sa.Uuid(), nullable=True),
            sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
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
            sa.ForeignKeyConstraint(["decided_by_id"], ["users.id"]),
            sa.ForeignKeyConstraint(["scenario_id"], ["scenarios.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("scenario_id"),
        )
        op.create_index(
            "ix_final_client_revision_gates_decision",
            "final_client_revision_gates",
            ["decision"],
        )

    publication_columns = {
        column["name"] for column in inspector.get_columns("publications")
    }
    publication_column_definitions = (
        sa.Column("assigned_publisher_id", sa.Uuid(), nullable=True),
        sa.Column(
            "manager_review_decision",
            publication_review,
            server_default=sa.text("'PENDING'"),
            nullable=False,
        ),
        sa.Column("manager_review_comment", sa.Text(), nullable=True),
        sa.Column("manager_reviewed_by_id", sa.Uuid(), nullable=True),
        sa.Column("manager_reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "publisher_status",
            publisher_status,
            server_default=sa.text("'PENDING'"),
            nullable=False,
        ),
        sa.Column("publisher_comment", sa.Text(), nullable=True),
        sa.Column("dzen_url", sa.Text(), nullable=True),
        sa.Column("youtube_url", sa.Text(), nullable=True),
        sa.Column("tiktok_url", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    )
    for column in publication_column_definitions:
        if column.name not in publication_columns:
            op.add_column("publications", column)

    foreign_key_columns = {
        tuple(constraint["constrained_columns"])
        for constraint in inspector.get_foreign_keys("publications")
    }
    if ("assigned_publisher_id",) not in foreign_key_columns:
        op.create_foreign_key(
            "fk_publications_assigned_publisher",
            "publications",
            "users",
            ["assigned_publisher_id"],
            ["id"],
        )
    if ("manager_reviewed_by_id",) not in foreign_key_columns:
        op.create_foreign_key(
            "fk_publications_manager_reviewer",
            "publications",
            "users",
            ["manager_reviewed_by_id"],
            ["id"],
        )

    index_names = {index["name"] for index in inspector.get_indexes("publications")}
    for name, columns in (
        ("ix_publications_assigned_publisher_id", ["assigned_publisher_id"]),
        ("ix_publications_manager_review_decision", ["manager_review_decision"]),
        ("ix_publications_publisher_status", ["publisher_status"]),
    ):
        if name not in index_names:
            op.create_index(name, "publications", columns)

    op.execute(
        """
        UPDATE publications
        SET manager_review_decision = 'APPROVED',
            publisher_status = 'PUBLISHED',
            published_at = COALESCE(first_published_at, updated_at)
        WHERE is_published IS TRUE
        """
    )
    op.execute(
        """
        INSERT INTO final_client_revision_gates (
            scenario_id, decision, request_comment, created_at, updated_at
        )
        SELECT scenario_id, 'PENDING', COALESCE(comment, ''), now(), now()
        FROM scenario_approvals
        WHERE stage = 'FINAL_CLIENT'
          AND decision IN ('REVISION', 'REJECTED')
        ON CONFLICT (scenario_id) DO NOTHING
        """
    )
    op.execute(
        """
        UPDATE scenarios
        SET status = 'MANAGER_REVISION_REVIEW'
        WHERE id IN (SELECT scenario_id FROM final_client_revision_gates)
        """
    )


def downgrade() -> None:
    op.drop_index("ix_publications_publisher_status", table_name="publications")
    op.drop_index("ix_publications_manager_review_decision", table_name="publications")
    op.drop_index("ix_publications_assigned_publisher_id", table_name="publications")
    op.drop_constraint("fk_publications_manager_reviewer", "publications", type_="foreignkey")
    op.drop_constraint("fk_publications_assigned_publisher", "publications", type_="foreignkey")
    for column in (
        "published_at",
        "tiktok_url",
        "youtube_url",
        "dzen_url",
        "publisher_comment",
        "publisher_status",
        "manager_reviewed_at",
        "manager_reviewed_by_id",
        "manager_review_comment",
        "manager_review_decision",
        "assigned_publisher_id",
    ):
        op.drop_column("publications", column)
    op.drop_index(
        "ix_final_client_revision_gates_decision",
        table_name="final_client_revision_gates",
    )
    op.drop_table("final_client_revision_gates")
    sa.Enum(name="publisher_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="publication_review_decision").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="gate_decision").drop(op.get_bind(), checkfirst=True)
    # PostgreSQL enum values PUBLISHER and the two scenario statuses are intentionally retained.
