"""library extrafanart name template

Revision ID: 6836662720a6
Revises: 9d591b251161
Create Date: 2026-11-10 20:45:49.965493
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6836662720a6"
down_revision: str | None = "9d591b251161"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """剧照文件名模板; 空值走默认索引命名."""
    op.add_column(
        "libraries", sa.Column("extrafanart_name_template", sqlmodel.sql.sqltypes.AutoString(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("libraries", "extrafanart_name_template")
