"""演员血型

Revision ID: 225cd39aa776
Revises: 1281b1b7c914
Create Date: 2026-11-22 21:52:19.256419
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "225cd39aa776"
down_revision: str | None = "1281b1b7c914"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("actors", sa.Column("blood_type", sqlmodel.sql.sqltypes.AutoString(), nullable=True))


def downgrade() -> None:
    op.drop_column("actors", "blood_type")
