"""retype heydouga entries and files

Revision ID: ec474ebeb46e
Revises: b5587b48da56
Create Date: 2026-11-22 13:33:53.522055

heydouga 从「按站点前缀判无码」改为独立类型: 番号本身不带马赛克信息, 有码 / 无码由
文件名、目录与标签决定. 存量行原先被写成 UNCENSORED, 升级后要改成 HEYDOUGA, 否则
「无码」筛选与角标仍然按类型点亮.

类型只落在 metadata (见 9054ebacea29); media_files 的相位列是路径投影, 不参与.
谓词只认 heydouga 番号, 其它行保持原值.
"""

import re
from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision: str = "ec474ebeb46e"
down_revision: str | None = "b5587b48da56"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_HEYDOUGA = re.compile(r"^heydouga[-_]?\d{2,4}([-_]\d{1,4})?$", re.IGNORECASE)


def _is_heydouga(number: str | None) -> bool:
    """站内番号全文匹配; 只是名字里带 heydouga 的条目不算."""
    return number is not None and _HEYDOUGA.fullmatch(number) is not None


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(text("SELECT id, number FROM metadata")).all()
    for row_id, number in rows:
        if not _is_heydouga(number):
            continue
        conn.execute(
            text("UPDATE metadata SET content_type = 'HEYDOUGA' WHERE id = :id"),
            {"id": row_id},
        )


def downgrade() -> None:
    """改回无码: 升级前的口径就是「heydouga 一律判无码」."""
    conn = op.get_bind()
    conn.execute(text("UPDATE metadata SET content_type = 'UNCENSORED' WHERE content_type = 'HEYDOUGA'"))
