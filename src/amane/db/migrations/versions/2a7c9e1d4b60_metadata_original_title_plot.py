"""add original title and plot to metadata

Revision ID: 2a7c9e1d4b60
Revises: 9054ebacea29
Create Date: 2026-11-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "2a7c9e1d4b60"
down_revision: str | None = "9054ebacea29"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("metadata", sa.Column("original_title", sa.String(), nullable=True))
    op.add_column("metadata", sa.Column("original_plot", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("metadata", "original_plot")
    op.drop_column("metadata", "original_title")
