"""library custom template tokens

Revision ID: d6658e3fea45
Revises: 4b8d2e7c1a90
Create Date: 2026-11-08

库级自定义占位符: ``{LTX}`` 这类占位符的匹配串存放在 libraries.custom_placeholders.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d6658e3fea45"
down_revision: str | None = "4b8d2e7c1a90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # SQLite DDL 失败会留下半成品列, 用 inspect 保持幂等以便重跑.
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("libraries")}
    if "custom_placeholders" in columns:
        return
    op.add_column(
        "libraries",
        sa.Column("custom_placeholders", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )


def downgrade() -> None:
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("libraries")}
    if "custom_placeholders" not in columns:
        return
    op.drop_column("libraries", "custom_placeholders")
