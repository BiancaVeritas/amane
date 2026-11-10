"""metadata image candidates

Revision ID: 9d591b251161
Revises: 4a1fc57b42d7
Create Date: 2026-11-10 09:27:37.978643

封面 / 海报的候选池. 选过的图按首次出现的顺序留在池里, 换图不重排, 所以选图框里的编号
不会因为换一次图就全变; ``thumb_urls`` / ``poster_urls`` 仍只存当前那一张.

自动生成带出的索引与枚举差异是 SQLite 方言误判, 与本次变更无关, 不采纳.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '9d591b251161'
down_revision: str | None = '4a1fc57b42d7'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('metadata', sa.Column('thumb_candidates', sa.JSON(), nullable=True))
    op.add_column('metadata', sa.Column('poster_candidates', sa.JSON(), nullable=True))

    # 存量数据: 把当前封面 / 海报并进候选池; 否则老条目第一次换图时会丢掉原图.
    metadata = sa.table(
        'metadata',
        sa.column('id', sa.Integer()),
        sa.column('thumb_urls', sa.JSON()),
        sa.column('poster_urls', sa.JSON()),
        sa.column('thumb_candidates', sa.JSON()),
        sa.column('poster_candidates', sa.JSON()),
    )
    bind = op.get_bind()
    rows = bind.execute(sa.select(metadata.c.id, metadata.c.thumb_urls, metadata.c.poster_urls)).fetchall()
    for row_id, thumb_urls, poster_urls in rows:
        first_thumb = thumb_urls[0] if thumb_urls else None
        first_poster = poster_urls[0] if poster_urls else None
        if first_thumb is None and first_poster is None:
            continue
        bind.execute(
            sa.update(metadata)
            .where(metadata.c.id == row_id)
            .values(
                thumb_candidates=[first_thumb] if first_thumb else [],
                poster_candidates=[first_poster] if first_poster else [],
            )
        )


def downgrade() -> None:
    op.drop_column('metadata', 'poster_candidates')
    op.drop_column('metadata', 'thumb_candidates')
