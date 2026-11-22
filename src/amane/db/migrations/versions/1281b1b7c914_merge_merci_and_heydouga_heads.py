"""合并メルシーボークー前缀与 heydouga 改型两个分支

两个迁移分别从 e06def65f72a 与 b5587b48da56 起头, 形成双 head. 它们操作的数据互不相干
(前者只改无码前缀的重投影, 后者只改 heydouga 条目的类型), 因此合并点不需要任何动作.

Revision ID: 1281b1b7c914
Revises: dc04a8ae624e, ec474ebeb46e
Create Date: 2026-11-22 20:03:58
"""

from collections.abc import Sequence

revision: str = "1281b1b7c914"
down_revision: str | tuple[str, str] | None = ("dc04a8ae624e", "ec474ebeb46e")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """两个分支互不相干, 无需额外动作."""


def downgrade() -> None:
    """回退同样无需动作: 拆开由各自迁移负责."""
