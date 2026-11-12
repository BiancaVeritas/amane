"""use Scene ID as WESTERN metadata identity

Revision ID: 4b8d2e7c1a90
Revises: 2a7c9e1d4b60
Create Date: 2026-11-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4b8d2e7c1a90"
down_revision: str | None = "2a7c9e1d4b60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("metadata", sa.Column("scene_id", sa.String(), nullable=True))
    op.create_index("ix_media_files_oshash", "media_files", ["oshash"], unique=False)
    op.drop_index("ix_metadata_number", table_name="metadata")
    op.create_index(
        "ix_metadata_number_lookup",
        "metadata",
        [sa.text("number COLLATE NOCASE")],
        unique=False,
    )
    op.create_index(
        "ix_metadata_number_non_western",
        "metadata",
        [sa.text("number COLLATE NOCASE")],
        unique=True,
        sqlite_where=sa.text("content_type != 'WESTERN'"),
    )
    op.create_index(
        "ix_metadata_western_scene_id",
        "metadata",
        [sa.text("lower(scene_id)")],
        unique=True,
        sqlite_where=sa.text("content_type = 'WESTERN' AND scene_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_metadata_western_scene_id", table_name="metadata")
    op.drop_index("ix_metadata_number_non_western", table_name="metadata")
    op.drop_index("ix_metadata_number_lookup", table_name="metadata")
    op.drop_index("ix_media_files_oshash", table_name="media_files")
    op.drop_column("metadata", "scene_id")
    # 若升级后已经产生重复番号的 WESTERN Metadata, 恢复旧全局唯一索引将失败;
    # 这是预期保护, 不允许 downgrade 静默丢弃或合并作品.
    op.create_index(
        "ix_metadata_number",
        "metadata",
        [sa.text("number COLLATE NOCASE")],
        unique=True,
    )
