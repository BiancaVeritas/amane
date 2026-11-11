"""merge upstream v0.27.1 heads

Revision ID: 827e02c622c9
Revises: 4b4ea568902e, e06def65f72a
Create Date: 2026-11-12 22:43:28.885704
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op


# revision identifiers, used by Alembic.
revision: str = '827e02c622c9'
down_revision: str | None = ('4b4ea568902e', 'e06def65f72a')
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
