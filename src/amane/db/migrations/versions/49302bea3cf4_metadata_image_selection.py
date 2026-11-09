"""metadata image selection

Revision ID: 49302bea3cf4
Revises: 798372bbfcab
Create Date: 2026-11-09 12:25:39.914408

只加两列: 候选池里当前选中的那一项. 其余改动来自 autogenerate 对既有 JSON 列与索引的
误判 (SQLite 方言差异), 与本次变更无关, 不采纳.
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '49302bea3cf4'
down_revision: str | None = '798372bbfcab'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('metadata', sa.Column('thumb_selected', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
    op.add_column('metadata', sa.Column('poster_selected', sqlmodel.sql.sqltypes.AutoString(), nullable=True))


def downgrade() -> None:
    op.drop_column('metadata', 'poster_selected')
    op.drop_column('metadata', 'thumb_selected')
