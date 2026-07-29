"""preserve complete Google Sheets source rows

Revision ID: 0018_sheet_source_payload
Revises: 0017_sheet_identity
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018_sheet_source_payload"
down_revision: str | None = "0017_sheet_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("scenarios", sa.Column("source_payload", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("scenarios", "source_payload")
