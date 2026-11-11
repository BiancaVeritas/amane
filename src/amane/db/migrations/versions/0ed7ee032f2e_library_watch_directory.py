"""library watch directory

Revision ID: 0ed7ee032f2e
Revises: 6836662720a6
Create Date: 2026-11-11 14:08:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0ed7ee032f2e"
down_revision: str | None = "6836662720a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """独立下载目录与原地模式开关; 全部默认关闭, 老库升级后行为不变."""
    op.add_column(
        "libraries",
        sa.Column("watch_dir_enabled", sa.Boolean(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column("libraries", sa.Column("watch_path", sa.String(), nullable=True))
    op.add_column("libraries", sa.Column("watch_rename", sa.Boolean(), nullable=False, server_default=sa.text("0")))


def downgrade() -> None:
    op.drop_column("libraries", "watch_rename")
    op.drop_column("libraries", "watch_path")
    op.drop_column("libraries", "watch_dir_enabled")
