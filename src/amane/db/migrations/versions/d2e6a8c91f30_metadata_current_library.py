"""persist one current library assignment per metadata record

Revision ID: d2e6a8c91f30
Revises: c91f7a2e6d40
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d2e6a8c91f30"
down_revision: str | None = "c91f7a2e6d40"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "metadata_library",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("metadata_id", sa.Integer(), sa.ForeignKey("metadata.id"), nullable=False),
        sa.Column("library_id", sa.Integer(), sa.ForeignKey("libraries.id"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("metadata_id", name="uq_metadata_library_metadata"),
    )
    op.create_index("ix_metadata_library_metadata_id", "metadata_library", ["metadata_id"])
    op.create_index("ix_metadata_library_library_id", "metadata_library", ["library_id"])
    # 旧数据可能有多个文件指向同一 Metadata。以最晚创建的文件记录作为唯一回填归属；
    # 这是可重复、确定性的历史近似值，不把路径整理时间误当成归属变更时间。
    op.execute(
        sa.text(
            """INSERT INTO metadata_library (metadata_id, library_id, updated_at)
            SELECT mf.metadata_id,
                   (SELECT newer.library_id FROM media_files AS newer
                    WHERE newer.metadata_id = mf.metadata_id
                    ORDER BY newer.created_at DESC, newer.id DESC LIMIT 1),
                   MAX(mf.created_at)
            FROM media_files AS mf
            WHERE mf.metadata_id IS NOT NULL
            GROUP BY mf.metadata_id"""
        )
    )


def downgrade() -> None:
    op.drop_index("ix_metadata_library_library_id", table_name="metadata_library")
    op.drop_index("ix_metadata_library_metadata_id", table_name="metadata_library")
    op.drop_table("metadata_library")
