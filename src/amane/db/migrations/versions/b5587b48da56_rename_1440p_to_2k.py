"""rename 1440p to 2k

Revision ID: b5587b48da56
Revises: 827e02c622c9
Create Date: 2026-11-12 23:58:07.105009
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b5587b48da56"
down_revision: str | None = "827e02c622c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """1440p 档位改名 2k.

    只在解析侧改 DEFINITION_VALUES 不够: 库里留着的旧值会让筛选与角标对不上,
    模板里 {def?} 的映射键也要同步改名, 否则那条规则永远命中不了.
    """
    conn = op.get_bind()
    conn.execute(sa.text("UPDATE media_files SET definition = '2k' WHERE definition = '1440p'"))
    # 模板里的映射键: {def?|1440p=...} 改成 {def?|2k=...}
    for column in ("video_template", "link_template", "strm_content_template"):
        conn.execute(
            sa.text(
                f"UPDATE libraries SET {column} = REPLACE({column}, '{{def?|1440p=', '{{def?|2k=') "
                f"WHERE {column} LIKE '%{{def?|1440p=%'"
            )
        )


def downgrade() -> None:
    """改回 1440p; 已合并的其它写法无法反推, 保持现值."""
    conn = op.get_bind()
    conn.execute(sa.text("UPDATE media_files SET definition = '1440p' WHERE definition = '2k'"))
