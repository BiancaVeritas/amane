"""add oshash submission status

Revision ID: d4e8f1a2b6c0
Revises: ef26a0b1c9d3
Create Date: 2026-11-17 02:21:11.306683
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4e8f1a2b6c0"
down_revision: str | None = "ef26a0b1c9d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "oshash_submissions",
        sa.Column(
            "status",
            sa.String(),
            server_default="PENDING",
            nullable=False,
        ),
    )
    op.create_index(
        "ix_oshash_submissions_status",
        "oshash_submissions",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_oshash_submissions_status",
        table_name="oshash_submissions",
    )
    with op.batch_alter_table("oshash_submissions") as batch_op:
        batch_op.drop_column("status")
