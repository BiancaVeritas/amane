"""library jinja video template

Revision ID: 7320c746a589
Revises: 0ed7ee032f2e
Create Date: 2026-11-11 18:10:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7320c746a589"
down_revision: str | None = "0ed7ee032f2e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Jinja2 命名模板开关与对应模板; 默认关闭, 老库升级后仍走原有占位符."""
    op.add_column(
        "libraries", sa.Column("use_jinja_template", sa.Boolean(), nullable=False, server_default=sa.text("0"))
    )
    op.add_column("libraries", sa.Column("jinja_video_template", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("libraries", "jinja_video_template")
    op.drop_column("libraries", "use_jinja_template")
