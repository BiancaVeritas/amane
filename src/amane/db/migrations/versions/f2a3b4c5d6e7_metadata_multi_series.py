"""add ordered metadata-to-series relationships

Revision ID: f2a3b4c5d6e7
Revises: d31f8a6c2e90
Create Date: 2026-11-07
"""

from collections.abc import Sequence
import json

import sqlalchemy as sa
from alembic import op

revision: str = "f2a3b4c5d6e7"
down_revision: str | None = "d31f8a6c2e90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "metadata",
        sa.Column("series_names", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
    )
    op.create_table(
        "metadata_series",
        sa.Column("metadata_id", sa.Integer(), sa.ForeignKey("metadata.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("series_id", sa.Integer(), sa.ForeignKey("series.id"), primary_key=True),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_metadata_series_series_id", "metadata_series", ["series_id"], unique=False)
    # Existing scalar series becomes the first entry and receives a normalized relation.
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, series FROM metadata WHERE series IS NOT NULL AND series != ''")).all()
    for metadata_id, name in rows:
        result = bind.execute(sa.text("SELECT id FROM series WHERE name = :name"), {"name": name}).first()
        if result is None:
            inserted = bind.execute(
                sa.text(
                    "INSERT INTO series (name, is_favorite, created_at, updated_at) "
                    "VALUES (:name, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {"name": name},
            )
            series_id = inserted.lastrowid
        else:
            series_id = result[0]
        bind.execute(
            sa.text("UPDATE metadata SET series_names = :names WHERE id = :metadata_id"),
            {"names": json.dumps([name], ensure_ascii=False), "metadata_id": metadata_id},
        )
        bind.execute(
            sa.text(
                "INSERT INTO metadata_series (metadata_id, series_id, position) "
                "VALUES (:metadata_id, :series_id, 0)"
            ),
            {"metadata_id": metadata_id, "series_id": series_id},
        )


def downgrade() -> None:
    op.drop_table("metadata_series")
    op.drop_column("metadata", "series_names")
