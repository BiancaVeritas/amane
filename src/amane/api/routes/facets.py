from typing import Annotated

import structlog
from fastapi import APIRouter, HTTPException, Query, Response

from ...db.models import FAVORITE_FACET_KINDS, SCRAPE_FACET_KINDS, FacetKind, FacetSortField, SortOrder
from ...db.repo_types import FacetItem
from ...tags.mapping import TagMappingStore
from ...utils.model import to_resp
from ..deps import RepoDep, RuntimeDep
from ..models import (
    FacetBatchAction,
    FacetBatchRequest,
    FacetBatchResponse,
    FacetListResponse,
    FacetMergeRequest,
    FacetRenameRequest,
    FacetResponse,
    FacetRuleListResponse,
    FacetRuleResponse,
    UserTagResponse,
    UserTagsCreateRequest,
    UserTagsCreateResponse,
)

logger = structlog.get_logger()

router = APIRouter(prefix="/facets", tags=["facets"])


def _facet_response(item: FacetItem) -> FacetResponse:
    return FacetResponse(id=item.id, name=item.name, count=item.count, is_favorite=item.is_favorite)


@router.post("/user_tag")
async def create_user_tags(req: UserTagsCreateRequest, repo: RepoDep) -> UserTagsCreateResponse:
    """按名称取回或新建用户标签; 已存在的名称直接复用, 响应与入参同序."""
    tags, created = await repo.ensure_user_tags(req.names)
    logger.info("user tags ensured", requested=len(req.names), created=created)
    return UserTagsCreateResponse(items=[to_resp(UserTagResponse, tag) for tag in tags], created=created)


@router.get("/{kind}")
async def list_facets(
    kind: FacetKind,
    repo: RepoDep,
    runtime: RuntimeDep,
    search: Annotated[str | None, Query()] = None,
    favorite: Annotated[bool | None, Query(description="Filter by favorite marker")] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=1000)] = 50,
    sort_by: Annotated[FacetSortField, Query(description="Sort field")] = FacetSortField.NAME,
    order: Annotated[SortOrder, Query(description="Sort order")] = SortOrder.ASC,
) -> FacetListResponse:
    # 传了参数就要求该分类支持收藏, 与取值无关; 静默忽略会让调用方以为筛选生效.
    if favorite is not None and kind not in FAVORITE_FACET_KINDS:
        raise HTTPException(status_code=400, detail="该分类不支持收藏")
    # 列表显示的是译文, 库里存的是原名. 用户照着屏幕上的名字输入时必须能命中,
    # 所以译文命中的词条要把它们的原名也并进来.
    originals = runtime.tag_mappings.originals_matching(search) if (kind == FacetKind.TAG and search) else []
    items, total = await repo.list_facets(
        kind, search=search, favorite=favorite, offset=offset, limit=limit, sort_by=sort_by, order=order
    )
    if originals:
        # 译文命中时结果集要放宽到全量再看, 否则被过滤掉的那些抄不回来.
        items, total = await _wide_scan(repo, kind, favorite, sort_by, order, wanted=set(originals), items=items)
    responses = [_facet_response(i) for i in items]
    if kind == FacetKind.TAG:
        responses = _merge_by_display_name(responses, runtime.tag_mappings)
    return FacetListResponse(items=responses, total=total)


async def _wide_scan(
    repo: RepoDep,
    kind: FacetKind,
    favorite: bool | None,
    sort_by: FacetSortField,
    order: SortOrder,
    *,
    wanted: set[str],
    items: list[FacetItem],
) -> tuple[list[FacetItem], int]:
    """把 ``wanted`` 里那批原名对应的条目并进 ``items``."""
    extra, extra_total = await repo.list_facets(
        kind, search=None, favorite=favorite, offset=0, limit=1000, sort_by=sort_by, order=order
    )
    merged = {i.id: i for i in items}
    for item in extra:
        if item.name in wanted:
            merged.setdefault(item.id, item)
    return sorted(merged.values(), key=lambda i: (i.name.casefold(), i.id or 0)), max(len(merged), extra_total)


def _merge_by_display_name(responses: list[FacetResponse], tag_mappings: TagMappingStore) -> list[FacetResponse]:
    """译名相同的条目并成一条, count 相加, ``merged_ids`` 记下全部 id.

    不同站点对同一个概念会给出不同原名 (``完全無修正`` / ``無碼``), 译文都是「无修正」.
    不合并的话界面列出两行一模一样的名字, 而按其中任意一个 id 筛选都只命中一半.
    """
    out: dict[str, FacetResponse] = {}
    for response in responses:
        display = tag_mappings.map_tags([response.name])[0]
        existing = out.get(display)
        if existing is None:
            out[display] = response.model_copy(update={"name": display, "merged_ids": [response.id]})
            continue
        out[display] = existing.model_copy(
            update={
                "count": existing.count + response.count,
                "merged_ids": [*existing.merged_ids, response.id],
                "is_favorite": existing.is_favorite and response.is_favorite,
            }
        )
    return sorted(out.values(), key=lambda r: (r.name.casefold(), r.id))


@router.get("/{kind}/rules")
async def list_facet_rules(kind: FacetKind, repo: RepoDep) -> FacetRuleListResponse:
    if kind not in SCRAPE_FACET_KINDS:
        raise HTTPException(status_code=400, detail="该分类不支持规则")
    try:
        rules = await repo.list_facet_rules(kind)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return FacetRuleListResponse(
        items=[
            FacetRuleResponse(
                id=r.id or 0,
                kind=str(r.kind),
                source_name=r.source_name,
                action=str(r.action),
                target_name=r.target_name,
                created_at=r.created_at,
                updated_at=r.updated_at,
            )
            for r in rules
        ]
    )


@router.delete("/{kind}/rules/{rule_id}", status_code=204)
async def delete_facet_rule(kind: FacetKind, rule_id: int, repo: RepoDep) -> Response:
    """删除单条规则; 不回填历史 Metadata."""
    if kind not in SCRAPE_FACET_KINDS:
        raise HTTPException(status_code=400, detail="该分类不支持规则")
    try:
        ok = await repo.delete_facet_rule(kind, rule_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not ok:
        raise HTTPException(status_code=404, detail="规则不存在")
    logger.info("facet rule deleted", kind=kind, rule_id=rule_id)
    return Response(status_code=204)


@router.post("/{kind}/batch")
async def batch_facets(kind: FacetKind, req: FacetBatchRequest, repo: RepoDep) -> FacetBatchResponse:
    """对一批分类执行同一个动作, 单个事务; 分类不支持收藏返回 400.

    不存在的 id 计入 ``missing`` 而不报 404 — 批量请求里某个 id 已失效不该让整批无结果. 动作语义
    与扩展位见 ``FacetBatchAction``.
    """
    if kind not in FAVORITE_FACET_KINDS:
        raise HTTPException(status_code=400, detail="该分类不支持收藏")
    match req.action:
        case FacetBatchAction.FAVORITE:
            result = await repo.set_facets_favorite(kind, req.facet_ids, is_favorite=True)
        case FacetBatchAction.UNFAVORITE:
            result = await repo.set_facets_favorite(kind, req.facet_ids, is_favorite=False)
    logger.info(
        "facets batch",
        kind=kind,
        action=req.action,
        changed=result.changed,
        unchanged=result.unchanged,
        missing=result.missing,
    )
    return FacetBatchResponse(changed=result.changed, unchanged=result.unchanged, missing=result.missing)


@router.get("/{kind}/{facet_id}", response_model_exclude={"merged_ids"})
async def get_facet(kind: FacetKind, facet_id: int, repo: RepoDep, runtime: RuntimeDep) -> FacetResponse:
    item = await repo.get_facet(kind, facet_id)
    if item is None:
        raise HTTPException(status_code=404, detail="分类不存在")
    response = _facet_response(item)
    # 单条详情不参与「译名合并」, 因此不输出 merged_ids: 那个字段只对列表里的合并项有意义.
    return (
        response.model_copy(update={"name": runtime.tag_mappings.map_tags([response.name])[0]})
        if kind == FacetKind.TAG
        else response
    )


@router.post("/{kind}/merge")
async def merge_facets(kind: FacetKind, req: FacetMergeRequest, repo: RepoDep, runtime: RuntimeDep) -> FacetResponse:
    """将 source_ids 合并入 target_id: 关联迁移到 target, source 实体被删除."""
    try:
        item = await repo.merge_facets(kind, req.target_id, req.source_ids)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if item is None:
        raise HTTPException(status_code=404, detail="分类不存在")
    logger.info("facets merged", kind=kind, target_id=req.target_id, source_ids=req.source_ids)
    response = _facet_response(item)
    return (
        response.model_copy(update={"name": runtime.tag_mappings.map_tags([response.name])[0]})
        if kind == FacetKind.TAG
        else response
    )


@router.patch("/{kind}/{facet_id}")
async def rename_facet(
    kind: FacetKind, facet_id: int, req: FacetRenameRequest, repo: RepoDep, runtime: RuntimeDep
) -> FacetResponse:
    """新名称与另一实体冲突时返回 409 (应使用合并)."""
    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="名称不能为空")
    try:
        item = await repo.rename_facet(kind, facet_id, name)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    if item is None:
        raise HTTPException(status_code=404, detail="分类不存在")
    logger.info("facet renamed", kind=kind, facet_id=facet_id, name=name)
    return _facet_response(item)


@router.delete("/{kind}/{facet_id}", status_code=204)
async def delete_facet(kind: FacetKind, facet_id: int, repo: RepoDep) -> Response:
    """删除分类. 爬取侧写入剔除规则并从 Metadata 真值移除; user_tag 硬删."""
    try:
        ok = await repo.delete_facet(kind, facet_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not ok:
        raise HTTPException(status_code=404, detail="分类不存在")
    logger.info("facet deleted", kind=kind, facet_id=facet_id)
    return Response(status_code=204)
