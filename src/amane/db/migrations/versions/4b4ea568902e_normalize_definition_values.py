"""normalize definition values

Revision ID: 4b4ea568902e
Revises: 59f649cb9e18
Create Date: 2026-11-12 22:25:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4b4ea568902e"
down_revision: str | None = "59f649cb9e18"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


#: 旧写法 → 现档位. 写入从此只落数字档位 (全小写), 同档多种写法归一.
_RENAMES: dict[str, str] = {
    "8K": "8k",
    "4K": "4k",
    "2160P": "4k",
    "4320P": "8k",
    "1080P": "1080p",
    "720P": "720p",
    "480P": "480p",
    "576P": "480p",
    "1440P": "1440p",
    "HD": "720p",
    "SD": "480p",
}


def upgrade() -> None:
    """存量档位改成新写法.

    只在解析侧改 DEFINITION_VALUES 不够: 库里留着的旧值会让筛选和角标对不上,
    整理产物里带档位的文件名也要跟着改, 否则同名判断会分裂成两份.
    """
    conn = op.get_bind()
    for old, new in _RENAMES.items():
        conn.execute(
            sa.text("UPDATE media_files SET definition = :new WHERE definition = :old"),
            {"new": new, "old": old},
        )


def downgrade() -> None:
    """回到数字与字母混写; 折叠过的同档无法还原, 保持现值."""
