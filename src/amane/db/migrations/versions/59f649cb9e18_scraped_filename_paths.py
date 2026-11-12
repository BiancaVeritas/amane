"""scraped filename paths

Revision ID: 59f649cb9e18
Revises: 69a996587ddc
Create Date: 2026-11-12 21:38:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "59f649cb9e18"
down_revision: str | None = "69a996587ddc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """记下来源与目标路径, 界面按库内相对路径显示."""
    op.add_column(
        "scraped_filenames",
        sa.Column("source_path", sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default=""),
    )
    op.add_column(
        "scraped_filenames",
        sa.Column("target_path", sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("scraped_filenames", "target_path")
    op.drop_column("scraped_filenames", "source_path")
