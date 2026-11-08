"""media file ltx flag

Revision ID: 798372bbfcab
Revises: 6b1790d5e81a
Create Date: 2026-11-08 21:08:07.944318
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "798372bbfcab"
down_revision: str | None = "6b1790d5e81a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # autogenerate 同时在 metadata / schedules / tasks 上产出了索引与枚举变更, 那是 SQLite
    # 无法反射表达式索引与 Enum 造成的误报 (既有迁移已处理), 这里只保留本意.
    #
    # 存量行按文件名回填, 与 `_apply_path_phase` 同源; 默认 false 只作为列的起点.
    op.add_column("media_files", sa.Column("ltx", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_index(op.f("ix_media_files_ltx"), "media_files", ["ltx"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_media_files_ltx"), table_name="media_files")
    op.drop_column("media_files", "ltx")
