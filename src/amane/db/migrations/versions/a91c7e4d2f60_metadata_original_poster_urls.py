"""preserve original poster urls separately from materialized poster urls

Revision ID: a91c7e4d2f60
Revises: 4b8d2e7c1a90, 5abbb79b1ae6
Create Date: 2026-11-03

原始海报地址与经过下载/裁剪后可能替换的 poster_urls 分离保存。
从 raw 站点快照恢复旧记录; 不从 poster_urls 推断, 避免把内部资源地址误作原始地址。
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "a91c7e4d2f60"
down_revision: str | Sequence[str] | None = ("4b8d2e7c1a90", "5abbb79b1ae6")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _raw_poster_urls(raw: Any) -> list[str]:
    if not isinstance(raw, dict):
        return []
    urls: list[str] = []
    for site_name, site in raw.items():
        if not isinstance(site, dict):
            continue
        # 新版快照明确记录原海报字段, 包括空列表; 不能再回退到展示用 poster_urls.
        if "original_poster_urls" in site:
            value = site["original_poster_urls"]
        else:
            # 旧版 DMM / AVBase 在缺少独立海报时会从封面派生竖版展示图,
            # 无法仅凭旧快照区分, 按“不可恢复则留空”原则跳过, 避免封面冒充原海报.
            if site_name.casefold() in {"dmm", "avbase"}:
                continue
            value = site.get("poster_urls", site.get("poster_url"))
        if isinstance(value, str):
            candidates = [value]
        elif isinstance(value, list):
            candidates = value
        else:
            continue
        for url in candidates:
            if isinstance(url, str) and url and url not in urls:
                urls.append(url)
    return urls


def upgrade() -> None:
    op.add_column(
        "metadata",
        sa.Column("original_poster_urls", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
    )
    metadata = sa.table(
        "metadata",
        sa.column("id", sa.Integer()),
        sa.column("raw", sa.JSON()),
        sa.column("original_poster_urls", sa.JSON()),
    )
    bind = op.get_bind()
    rows = bind.execute(sa.select(metadata.c.id, metadata.c.raw)).mappings()
    for row in rows:
        urls = _raw_poster_urls(row["raw"])
        if urls:
            bind.execute(metadata.update().where(metadata.c.id == row["id"]).values(original_poster_urls=urls))


def downgrade() -> None:
    op.drop_index(
        "ix_metadata_western_scene_id",
        table_name="metadata",
    )
    op.drop_column("metadata", "original_poster_urls")
    op.create_index(
        "ix_metadata_western_scene_id",
        "metadata",
        [sa.text("lower(scene_id)")],
        unique=True,
        sqlite_where=sa.text("content_type = 'WESTERN' AND scene_id IS NOT NULL"),
    )
