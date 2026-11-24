"""add metadata runtime_seconds

Revision ID: 66e1b83d8ce7
Revises: fac1fdd5ecb3
Create Date: 2026-11-24 15:09:23.148760
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "66e1b83d8ce7"
down_revision: str | None = "fac1fdd5ecb3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 只加这一列. autogenerate 会顺带产出索引与枚举的「差异」, 那些是 SQLite 反射的假阳性,
    # 照抄会重建索引甚至改动约束类型, 与本次目的无关.
    op.add_column("metadata", sa.Column("runtime_seconds", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("metadata", "runtime_seconds")
