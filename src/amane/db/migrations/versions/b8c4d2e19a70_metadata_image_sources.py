"""persist source for each aggregated image URL

Revision ID: b8c4d2e19a70
Revises: a7c9e2d41f60
Create Date: 2026-11-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b8c4d2e19a70"
down_revision: str | None = "a7c9e2d41f60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("metadata", sa.Column("image_sources", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))


def downgrade() -> None:
    op.drop_column("metadata", "image_sources")
