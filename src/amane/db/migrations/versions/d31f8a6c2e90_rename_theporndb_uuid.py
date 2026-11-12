"""rename WESTERN identity column to theporndb_uuid

Revision ID: d31f8a6c2e90
Revises: c7e2a9f14b60
Create Date: 2026-11-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d31f8a6c2e90"
down_revision: str | None = "c7e2a9f14b60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_metadata_western_scene_id", table_name="metadata")
    op.alter_column("metadata", "scene_id", new_column_name="theporndb_uuid")
    op.create_index(
        "ix_metadata_western_theporndb_uuid",
        "metadata",
        [sa.text("lower(theporndb_uuid)")],
        unique=True,
        sqlite_where=sa.text("content_type = 'WESTERN' AND theporndb_uuid IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_metadata_western_theporndb_uuid", table_name="metadata")
    op.alter_column("metadata", "theporndb_uuid", new_column_name="scene_id")
    op.create_index(
        "ix_metadata_western_scene_id",
        "metadata",
        ["lower(scene_id)"],
        unique=True,
        sqlite_where=sa.text("content_type = 'WESTERN' AND scene_id IS NOT NULL"),
    )
