"""分类名称的中文映射.

页面上的三个滑动选项各是一组, 组之间完全独立: 端点、标题、统计与可用动作都跟着 ``group`` 走.
路径里是**组**而不是分类 —— 片商那一组的制作商 / 发行商 / 系列共用同一张表, 同一个名字只占一行.

映射只影响展示: 搜索、去重与刮削比对仍用原始名.

每组的列表在读取前先按分类表对账: 分类表里已经没有 (或引用数归零) 的自动条目会连同译名
一起删掉, 手动条目保留.
"""

import json
from typing import Annotated

from aiolimiter import AsyncLimiter
from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.responses import JSONResponse
from httpx2 import AsyncClient, Timeout
from pydantic import BaseModel, Field
from pydantic_ai.direct import model_request
from pydantic_ai.messages import ModelRequest, SystemPromptPart, UserPromptPart

from ...db.models import FacetKind, FacetSortField, SortOrder
from ...llm.model import build_model
from ...tags import MAPPING_FILES, MAPPING_GROUPS, MappingGroup, VendorRole
from ..deps import RepoDep, RuntimeDep

router = APIRouter(prefix="/mappings", tags=["mappings"])

#: 每组管的分类; 组内顺序即页面上的展示顺序.
GROUP_FACETS: dict[MappingGroup, tuple[FacetKind, ...]] = {
    MappingGroup.TAG: (FacetKind.TAG,),
    MappingGroup.ACTOR: (FacetKind.ACTOR,),
    MappingGroup.VENDOR: (FacetKind.STUDIO, FacetKind.PUBLISHER, FacetKind.SERIES),
}

#: 三组都有归档区; 删除一律是归档, 真删只由对账做.
ARCHIVABLE: frozenset[MappingGroup] = frozenset(MAPPING_GROUPS)

StatusFilter = Annotated[str, Query(pattern="^(all|pending|mapped)$")]
LanguageFilter = Annotated[str, Query(pattern="^(all|ja|en|zh|other)$")]
FrequencyFilter = Annotated[str, Query(pattern="^(all|high)$")]


class MappingUpdate(BaseModel):
    original: str = Field(min_length=1, max_length=300)
    translation: str | None = Field(default=None, max_length=300)


class ManualMappingUpdate(MappingUpdate):
    """手动新增时可以指定它属于片商组的哪几个分类."""

    roles: list[VendorRole] = Field(default_factory=list)


async def _sync_group(runtime: RuntimeDep, repo: RepoDep, group: MappingGroup) -> None:
    """按分类表对账: 分类表里已经不存在 (或引用数归零) 的自动条目连同译名一起删掉.

    片商三分类并成一张表, 所以同一批数据里同一个名字会出现多次 (它同时是制作商与发行商);
    表那边按名字合成一行, 用 ``roles`` 记住归属. 来源取自各条影片的 ``field_sources``.
    """
    if not runtime.mappings.needs_sync(group):
        return
    facet_values: list[tuple[str, str, int]] = []
    sources: dict[str, set[str]] = {}
    for facet_kind in GROUP_FACETS[group]:
        offset = 0
        while True:
            facets, total = await repo.list_facets(
                facet_kind, offset=offset, limit=1000, sort_by=FacetSortField.NAME, order=SortOrder.ASC
            )
            facet_values.extend((str(facet_kind), item.name, item.count) for item in facets)
            offset += len(facets)
            if not facets or offset >= total:
                break
        # 同一个名字可能跨分类出现 (制作商与发行商同名), 来源取并集.
        for name, values in (await repo.facet_sources(facet_kind)).items():
            sources.setdefault(name, set()).update(values)
    merged = {name: sorted(values) for name, values in sources.items()}
    if group is MappingGroup.VENDOR:
        runtime.mappings.sync_vendor(facet_values, sources=merged)
    else:
        runtime.mappings.sync(group, [(name, count) for _, name, count in facet_values], sources=merged)


async def _role_of(runtime: RuntimeDep, repo: RepoDep, group: MappingGroup, value: str) -> str | None:
    """手动新增片商映射时推断它属于哪个分类: 先找现存分类, 找不到就按第一个分类落.

    名字在多个分类里都出现时它本就该身兼数职, 表里也只占一行, 因此取第一个命中的就够.
    """
    if group is not MappingGroup.VENDOR:
        return None
    for facet_kind in GROUP_FACETS[group]:
        items, _ = await repo.list_facets(
            facet_kind, search=value, offset=0, limit=50, sort_by=FacetSortField.NAME, order=SortOrder.ASC
        )
        if any(item.name == value for item in items):
            return str(facet_kind)
    return str(FacetKind.STUDIO)


@router.get("/groups")
async def list_mapping_groups() -> dict:
    """可维护的映射组; 前端据此渲染滑动选项与各组的分类范围, 不在前端硬编码名单."""
    return {
        "items": [
            {
                "group": str(group),
                "archive": group in ARCHIVABLE,
                "kinds": [str(kind) for kind in GROUP_FACETS[group]],
            }
            for group in MAPPING_GROUPS
        ]
    }


@router.get("/{group}")
async def list_mappings(
    group: MappingGroup,
    runtime: RuntimeDep,
    repo: RepoDep,
    status: StatusFilter = "pending",
    language: LanguageFilter = "all",
    frequency: FrequencyFilter = "all",
    search: str = "",
    archived: bool = False,
    kind: Annotated[str | None, Query(description="片商组内按分类过滤: studio / publisher / series")] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
):
    await _sync_group(runtime, repo, group)
    store = runtime.mappings.store(group)
    role = None
    if group is MappingGroup.VENDOR and kind in {str(value) for value in VendorRole}:
        role = kind
    items, total = store.list(
        status=status,
        language=language,
        frequency=frequency,
        search=search,
        archived=archived,
        # 「全部词条」要把归档区也算进来: 那一档的字面意思就是全部, 少一块会让人以为
        # 词条凭空消失了.
        include_archived=status == "all" and not archived,
        role=role,
        offset=offset,
        limit=limit,
    )
    return {
        "items": [item.__dict__ for item in items],
        "total": total,
        "stats": store.counts(role),
        "groups": [{"kind": str(facet_kind), "label": str(facet_kind)} for facet_kind in GROUP_FACETS[group]],
        "archive": group in ARCHIVABLE,
    }


@router.put("/{group}")
async def save_mapping(group: MappingGroup, req: MappingUpdate, runtime: RuntimeDep, repo: RepoDep):
    try:
        store = runtime.mappings.store(group)
        roles: list[str] | None = None
        if group is MappingGroup.VENDOR:
            # 手动新增的片商名要落一个分类归属; 已存在的条目沿用原归属.
            existing = store.get(req.original.strip())
            if existing is not None and existing.roles:
                roles = list(existing.roles)
            else:
                role = await _role_of(runtime, repo, group, req.original.strip())
                roles = [role] if role else None
        item = store.upsert(req.original, req.translation, roles)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return item.__dict__


@router.delete("/{group}/{original:path}", status_code=204)
async def delete_mapping(group: MappingGroup, original: str, runtime: RuntimeDep) -> Response:
    """三组统一: 删除就是归档, 词条不真删.

    真删只交给对账 —— 分类表里已经没有人用的自动条目才连同译名一起消失, 手动条目与导入的
    词典永远不走这条路. 这里若真删, 词条会在下次对账时按分类表重建出来, 用户看到的就是
    「删了又回来」; 而手动 / 导入的词条一旦真删就再也找不回来了.
    """
    store = runtime.mappings.store(group)
    if not store.delete(original):
        entry = store.get(original)
        if entry is not None and entry.translation:
            raise HTTPException(409, "已映射的词条不能归档, 请先清空中文译名")
        raise HTTPException(404, "词条不存在或已归档")
    return Response(status_code=204)


@router.post("/{group}/restore/{original:path}")
async def restore_mapping(group: MappingGroup, original: str, runtime: RuntimeDep):
    """把归档区的词条放回主列表."""
    if not runtime.mappings.store(group).restore(original):
        raise HTTPException(404, "词条不存在或未归档")
    return {"restored": original}


class MappingCandidate(BaseModel):
    """补全框的一项: ``original`` 是要写库的原始名, ``label`` 是同一条的译名."""

    original: str
    label: str


class MappingCandidatesResponse(BaseModel):
    items: list[MappingCandidate]


@router.get("/{group}/candidates")
async def list_mapping_candidates(
    group: MappingGroup,
    runtime: RuntimeDep,
    repo: RepoDep,
    search: Annotated[str, Query(description="按原名或译名过滤")] = "",
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    kind: Annotated[str | None, Query(description="片商组内按分类过滤: studio / publisher / series")] = None,
) -> MappingCandidatesResponse:
    """给选择框用的候选: ``(原始名, 展示名)`` 成对返回.

    与 ``GET /{group}`` 的区别: 那边是给人看的管理表 (默认只看待映射, 且要翻译), 这里是给
    录入框的补全 —— 要的是**原始名**, 因为写进条目的是原始名, 展示名只是同一件事的译文.
    两个原始名映射到同一译文时也要各出一条, 不能按译文去重.

    ``kind`` 只对片商组有意义: 制作商与发行商共用一张表, 各自的候选要分开取.
    """
    await _sync_group(runtime, repo, group)
    role = kind if group is MappingGroup.VENDOR and kind in {str(value) for value in VendorRole} else None
    needle = search.strip().casefold()
    items: list[MappingCandidate] = []
    entries, _ = runtime.mappings.store(group).list(
        status="all", include_archived=True, role=role, limit=1000
    )
    for item in entries:
        shown = item.translation or item.original
        if needle and needle not in item.original.casefold() and needle not in shown.casefold():
            continue
        items.append(MappingCandidate(original=item.original, label=shown))
        if len(items) >= limit:
            break
    return MappingCandidatesResponse(items=items)


@router.get("/{group}/export")
async def export_mappings(group: MappingGroup, runtime: RuntimeDep):
    return JSONResponse(
        runtime.mappings.store(group).export(),
        headers={"Content-Disposition": f'attachment; filename="{MAPPING_FILES[group]}"'},
    )


@router.post("/{group}/import")
async def import_mappings(group: MappingGroup, payload: dict, runtime: RuntimeDep):
    try:
        count = runtime.mappings.store(group).import_data(payload)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"imported": count}


class SuggestRequest(BaseModel):
    originals: list[str] = Field(min_length=1, max_length=30)


_SUGGEST_PROMPTS: dict[MappingGroup, str] = {
    MappingGroup.TAG: "将以下影片标签逐条翻译为简体中文。保留标签含义，不要合并，不要解释。只返回 JSON 对象，键为原文，值为中文译名。\n",
    MappingGroup.ACTOR: "将以下日本演员名逐条转写为简体中文常用译名。不确定时按读音给出最通行的中文写法，不要解释。只返回 JSON 对象，键为原文，值为中文译名。\n",
    MappingGroup.VENDOR: "将以下日本影片厂商与系列名逐条翻译为简体中文。保留厂牌与系列识别度，不要解释。只返回 JSON 对象，键为原文，值为中文译名。\n",
}


@router.post("/{group}/suggest")
async def suggest_mappings(group: MappingGroup, req: SuggestRequest, runtime: RuntimeDep):
    """只返回候选译名，不写入词典；人工确认仍由 PUT 接口完成。"""
    llm = runtime.config.hot.llm
    if not llm.api_key or not llm.model:
        raise HTTPException(503, "请先在现有 LLM 设置中配置 API Key 和模型")
    originals = list(dict.fromkeys(value.strip() for value in req.originals if value.strip()))
    if not originals:
        raise HTTPException(400, "没有有效的原始名")
    prompt = _SUGGEST_PROMPTS[group] + json.dumps(originals, ensure_ascii=False)
    try:
        # 复用现有 LLM 的 API 类型、地址、密钥、模型、代理和速率配置；不依赖标题/剧情翻译开关。
        async with AsyncClient(
            proxy=runtime.config.hot.network.proxy, timeout=Timeout(45), follow_redirects=True
        ) as http_client:
            model = build_model(
                llm.api_type, base_url=llm.base_url, api_key=llm.api_key, model=llm.model, http_client=http_client
            )
            async with AsyncLimiter(llm.rate_limit, time_period=1):
                result = await model_request(
                    model,
                    [
                        ModelRequest(
                            parts=[
                                SystemPromptPart(content="你是影视元数据翻译助手。输出必须是合法 JSON 对象。"),
                                UserPromptPart(content=prompt),
                            ]
                        )
                    ],
                )
        data = json.loads(result.text or "{}")
        if not isinstance(data, dict):
            raise ValueError("模型返回格式不是 JSON 对象")
    except Exception as exc:
        raise HTTPException(502, f"LLM 建议生成失败：{exc}") from exc
    # 返回建议供 UI 展示，绝不自动入库。
    return {
        "suggestions": {
            key: str(value).strip() for key, value in data.items() if key in originals and str(value).strip()
        }
    }
