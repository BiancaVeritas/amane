"""scraped filename registry

Revision ID: bea1ee8c801c
Revises: ab3a997abb20
Create Date: 2026-11-12 21:12:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "bea1ee8c801c"
down_revision: str | None = "ab3a997abb20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """同名不重刮的登记表, 与 media_files 解耦.

    靠 media_files 判断「这个名字刮过没有」会随整理/清理失效: 那张表本来就会增删,
    文件挪动两轮之后同名记录不再命中, 于是又重刮一遍. 这里单独记一条, 要重刮得显式清.
    """
    op.create_table(
        "scraped_filenames",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("library_id", sa.Integer(), nullable=False),
        sa.Column("filename", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(["library_id"], ["libraries.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("library_id", "filename", name="uq_scraped_filenames_library_name"),
    )
    op.create_index(op.f("ix_scraped_filenames_filename"), "scraped_filenames", ["filename"], unique=False)
    op.create_index(op.f("ix_scraped_filenames_library_id"), "scraped_filenames", ["library_id"], unique=False)

    # 回填: 现有 SCRAPED 记录代表已经刮过了, 升级后不该全部重刮.
    # 只存主名 (去掉扩展名): 判重不比扩展名, 同一份片子换容器格式仍算同一份.
    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT DISTINCT library_id, filename, updated_at FROM media_files WHERE status = 'SCRAPED'")
    ).fetchall()
    seen: set[tuple[int, str]] = set()
    for library_id, filename, updated_at in rows:
        stem = str(filename).strip()
        dot = stem.rfind(".")
        if dot > 0:
            stem = stem[:dot]
        key = (int(library_id), stem)
        if not stem or key in seen:
            continue
        seen.add(key)
        conn.execute(
            sa.text(
                "INSERT INTO scraped_filenames (library_id, filename, created_at) "
                "VALUES (:library_id, :filename, :created_at)"
            ),
            {"library_id": library_id, "filename": stem, "created_at": updated_at},
        )


def downgrade() -> None:
    op.drop_index(op.f("ix_scraped_filenames_library_id"), table_name="scraped_filenames")
    op.drop_index(op.f("ix_scraped_filenames_filename"), table_name="scraped_filenames")
    op.drop_table("scraped_filenames")
