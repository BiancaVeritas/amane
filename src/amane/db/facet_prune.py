"""删除影片后清掉随之变空的分类实体.

判据是「作品数为 0 且没有人工痕迹」: 映射表里该名字有中文译名、或词条已归档的实体一律保留 ——
那是用户亲手留下的决定, 不该因为某部片被删而消失.

映射表按 :class:`MappingGroup` 分组, 名字落在哪一组由分类种类决定, 与
``api/routes/facets.py::_mapping_group`` 同一口径.

只在删除入口调用 (单条与批量), 不做定时扫描: 计数归零只可能由删除造成, 在那里处理最及时.
"""

from __future__ import annotations

import structlog
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from ..tags.mapping import MappingGroup, MappingRegistry
from .models import (
    Actor,
    Director,
    FacetKind,
    Metadata,
    MetadataActor,
    MetadataDirector,
    MetadataSeries,
    MetadataTag,
    Publisher,
    Series,
    Studio,
    Tag,
)

logger = structlog.get_logger()

#: 关联型分类: (实体, 关联表的外键列, 映射组).
_LINK_FACETS: dict[FacetKind, tuple[type, object, MappingGroup | None]] = {
    FacetKind.ACTOR: (Actor, MetadataActor.actor_id, MappingGroup.ACTOR),
    FacetKind.DIRECTOR: (Director, MetadataDirector.director_id, None),
    FacetKind.TAG: (Tag, MetadataTag.tag_id, MappingGroup.TAG),
    FacetKind.SERIES: (Series, MetadataSeries.series_id, MappingGroup.VENDOR),
}

#: 标量分类: 名字存在 Metadata 的列里, 没有关联表.
_SCALAR_FACETS: dict[FacetKind, tuple[type, str, MappingGroup]] = {
    FacetKind.STUDIO: (Studio, "studio", MappingGroup.VENDOR),
    FacetKind.PUBLISHER: (Publisher, "publisher", MappingGroup.VENDOR),
}


async def prune_empty_facets(
    session: AsyncSession, registry: MappingRegistry | None, *, kind: FacetKind | None = None
) -> dict[str, int]:
    """删掉作品数为 0 的分类实体; 有译名或已归档的保留.

    ``kind`` 限定只处理一种分类 (手动清理用); 缺省处理全部.
    ``registry`` 缺省时直接返回: 没有映射表就判不出人工痕迹, 宁可不删.
    调用方负责提交事务. 返回各分类被删的条数, 供日志与测试断言.
    """
    if registry is None:
        return {}

    link_facets = _LINK_FACETS if kind is None else {kind: _LINK_FACETS[kind]} if kind in _LINK_FACETS else {}
    scalar_facets = _SCALAR_FACETS if kind is None else {kind: _SCALAR_FACETS[kind]} if kind in _SCALAR_FACETS else {}

    removed: dict[str, int] = {}

    for facet_kind, (entity, fk_column, group) in link_facets.items():
        used = {int(row) for row in (await session.exec(select(col(fk_column)).distinct())).all() if row is not None}
        doomed = [
            row
            for row in (await session.exec(select(entity))).all()
            if row.id is not None and int(row.id) not in used and not _retained(registry, group, row.name)
        ]
        for row in doomed:
            await session.delete(row)
        if doomed:
            removed[str(facet_kind)] = len(doomed)

    for facet_kind, (entity, column_name, group) in scalar_facets.items():
        column = getattr(Metadata, column_name)
        used = {str(name) for name in (await session.exec(select(col(column)))).all() if name}
        doomed = [
            row
            for row in (await session.exec(select(entity))).all()
            if row.name not in used and not _retained(registry, group, row.name)
        ]
        for row in doomed:
            await session.delete(row)
        if doomed:
            removed[str(facet_kind)] = len(doomed)

    if removed:
        logger.info("empty facets pruned", counts=removed)
    return removed


def _retained(registry: MappingRegistry, group: MappingGroup | None, name: str) -> bool:
    """有中文译名或已归档的条目保留.

    没有映射组的分类 (导演) 一律保留: 判不出人工痕迹时不该删.
    """
    if group is None:
        return True
    entry = registry.store(group).get(name)
    if entry is None:
        return False
    return bool(entry.translation) or bool(entry.archived)
