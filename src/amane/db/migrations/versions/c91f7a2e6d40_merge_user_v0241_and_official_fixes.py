"""merge user customization and selected official fixes"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "c91f7a2e6d40"
down_revision: str | Sequence[str] | None = ("b8c4d2e19a70", "56fe451426ef")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
