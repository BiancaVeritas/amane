"""scraped filename renamed column

Revision ID: 69a996587ddc
Revises: bea1ee8c801c
Create Date: 2026-11-12 21:25:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "69a996587ddc"
down_revision: str | None = "bea1ee8c801c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """记下整理后的文件名, 界面上把「原始名 → 新名」并排看.

    留空表示还没整理过, 与「整理后名字没变」区分开.
    """
    op.add_column(
        "scraped_filenames",
        sa.Column("renamed", sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("scraped_filenames", "renamed")
