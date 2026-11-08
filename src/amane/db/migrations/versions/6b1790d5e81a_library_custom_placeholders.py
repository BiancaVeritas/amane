"""library custom placeholders

Revision ID: 6b1790d5e81a
Revises: f2a3b4c5d6e7
Create Date: 2026-11-08 20:11:44.737923
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6b1790d5e81a"
down_revision: str | None = "f2a3b4c5d6e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # autogenerate 同时在 metadata / schedules / tasks 上产出了一批索引与枚举类型变更,
    # 那是 SQLite 无法反射表达式索引与 Enum 造成的误报 (既有迁移已处理), 这里只保留本意.
    op.add_column(
        "libraries",
        sa.Column("custom_placeholders", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("libraries", "custom_placeholders")
