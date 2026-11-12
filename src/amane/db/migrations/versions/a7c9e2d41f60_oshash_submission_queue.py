"""add ThePornDB OSHASH submission queue

Revision ID: a7c9e2d41f60
Revises: 4b8d2e7c1a90, f2a3b4c5d6e7
Create Date: 2026-11-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7c9e2d41f60"
down_revision: str | Sequence[str] | None = ("4b8d2e7c1a90", "f2a3b4c5d6e7")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "oshash_submissions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("theporndb_uuid", sa.String(), nullable=False),
        sa.Column("oshash", sa.String(), nullable=False),
        sa.Column("duration", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("path", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.UniqueConstraint("theporndb_uuid", "oshash", name="uq_oshash_submission_scene_hash"),
    )
    op.create_index("ix_oshash_submissions_theporndb_uuid", "oshash_submissions", ["theporndb_uuid"])


def downgrade() -> None:
    op.drop_index("ix_oshash_submissions_theporndb_uuid", table_name="oshash_submissions")
    op.drop_table("oshash_submissions")
