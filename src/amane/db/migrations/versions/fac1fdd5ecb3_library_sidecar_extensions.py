"""library sidecar extensions

Revision ID: fac1fdd5ecb3
Revises: 225cd39aa776
Create Date: 2026-11-23 20:57:22.028930
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "fac1fdd5ecb3"
down_revision: str | None = "225cd39aa776"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 已有库补上默认值: 老条目按新默认的 .bif 生效, 而不是留空 (空等于关闭该功能).
    op.add_column(
        "libraries",
        sa.Column("sidecar_extensions", sa.JSON(), nullable=False, server_default=sa.text("'[\".bif\"]'")),
    )


def downgrade() -> None:
    op.drop_column("libraries", "sidecar_extensions")
