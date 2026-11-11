"""media file filename column

Revision ID: ab3a997abb20
Revises: 7320c746a589
Create Date: 2026-11-11 19:55:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ab3a997abb20"
down_revision: str | None = "7320c746a589"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """按文件名查重需要等值索引; 从现有 path 回填, 老库升级后即可直接命中."""
    op.add_column("media_files", sa.Column("filename", sa.String(), nullable=False, server_default=""))
    op.create_index("ix_media_files_filename", "media_files", ["filename"])

    # 回填: 取 path 最后一个分隔符之后的部分. 两种分隔符都要认, 库内路径来源不一.
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, path FROM media_files")).fetchall()
    for row_id, path in rows:
        name = str(path).replace("\\", "/").rsplit("/", 1)[-1]
        conn.execute(sa.text("UPDATE media_files SET filename = :name WHERE id = :id"), {"name": name, "id": row_id})


def downgrade() -> None:
    op.drop_index("ix_media_files_filename", table_name="media_files")
    op.drop_column("media_files", "filename")
