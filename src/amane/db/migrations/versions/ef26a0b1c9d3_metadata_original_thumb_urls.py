"""preserve original thumb urls separately from manually replaced cover resources

Revision ID: ef26a0b1c9d3
Revises: d2e6a8c91f30
Create Date: 2026-11-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "ef26a0b1c9d3"
down_revision: str | Sequence[str] | None = "d2e6a8c91f30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "metadata",
        sa.Column("original_thumb_urls", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
    )
    bind = op.get_bind()
    metadata = sa.table(
        "metadata",
        sa.column("id", sa.Integer()),
        sa.column("thumb_urls", sa.JSON()),
        sa.column("original_thumb_urls", sa.JSON()),
    )
    rows = bind.execute(sa.select(metadata.c.id, metadata.c.thumb_urls)).mappings()
    for row in rows:
        urls = row["thumb_urls"] if isinstance(row["thumb_urls"], list) else []
        if urls:
            bind.execute(metadata.update().where(metadata.c.id == row["id"]).values(original_thumb_urls=urls))


def downgrade() -> None:
    op.drop_column("metadata", "original_thumb_urls")
