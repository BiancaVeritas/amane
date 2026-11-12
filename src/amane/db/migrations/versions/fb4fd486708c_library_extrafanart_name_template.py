"""library extrafanart name template

Revision ID: fb4fd486708c
Revises: 56fe451426ef
Create Date: 2026-11-08 01:10:53.661774
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "fb4fd486708c"
down_revision: str | None = "56fe451426ef"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """剧照文件名模板; 空值走默认 ``{index}.jpg``.

    本站分支上已由 ``6836662720a6`` 加过同一列, 合并上游时这里必须幂等, 否则
    ALTER TABLE 撞上 duplicate column name.
    """
    conn = op.get_bind()
    columns = {row[1] for row in conn.execute(sa.text("PRAGMA table_info(libraries)"))}
    if "extrafanart_name_template" in columns:
        return
    op.add_column(
        "libraries", sa.Column("extrafanart_name_template", sqlmodel.sql.sqltypes.AutoString(), nullable=True)
    )


def downgrade() -> None:
    # 同一列由两个 revision 各删一次; 第二条再 drop 时列已不在, 必须跳过.
    conn = op.get_bind()
    columns = {row[1] for row in conn.execute(sa.text("PRAGMA table_info(libraries)"))}
    if "extrafanart_name_template" not in columns:
        return
    op.drop_column("libraries", "extrafanart_name_template")
