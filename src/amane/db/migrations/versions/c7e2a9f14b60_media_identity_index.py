"""add independent WESTERN media identity index

Revision ID: c7e2a9f14b60
Revises: a91c7e4d2f60
Create Date: 2026-11-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c7e2a9f14b60"
down_revision: str | None = "a91c7e4d2f60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "media_identities",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "metadata_id",
            sa.Integer(),
            sa.ForeignKey("metadata.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("identity_type", sa.String(), nullable=False),
        sa.Column("identity_value", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_media_identities_type_value",
        "media_identities",
        ["identity_type", sa.text("lower(identity_value)")],
        unique=True,
    )
    op.create_index("ix_media_identities_metadata_id", "media_identities", ["metadata_id"])


def downgrade() -> None:
    op.drop_index("ix_media_identities_metadata_id", table_name="media_identities")
    op.drop_index("ix_media_identities_type_value", table_name="media_identities")
    op.drop_table("media_identities")
