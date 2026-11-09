"""drop image selection columns

Revision ID: 4a1fc57b42d7
Revises: 49302bea3cf4
Create Date: 2026-11-09 15:06:37.283340

移除 ``thumb_selected`` / ``poster_selected``, 回到上游语义: ``thumb_urls`` / ``poster_urls``
只存正在用的那一张, 换图即替换. 其余改动是 autogenerate 对既有 JSON 列与索引的误判
(SQLite 方言差异), 与本次变更无关, 不采纳.
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4a1fc57b42d7"
down_revision: str | None = "49302bea3cf4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("metadata", "thumb_selected")
    op.drop_column("metadata", "poster_selected")


def downgrade() -> None:
    op.add_column("metadata", sa.Column("poster_selected", sqlmodel.sql.sqltypes.AutoString(), nullable=True))
    op.add_column("metadata", sa.Column("thumb_selected", sqlmodel.sql.sqltypes.AutoString(), nullable=True))
