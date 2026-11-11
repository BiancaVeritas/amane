"""演员显示名映射.

只影响展示: 搜索、去重、刮削比对全部仍用原始名, 免得同一演员换个显示名就被当成另一个人.
"""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..deps import RuntimeDep

router = APIRouter(prefix="/actor-name-mappings", tags=["actor-name-mappings"])


class ActorNameUpdate(BaseModel):
    original: str = Field(min_length=1, max_length=300)
    translation: str | None = Field(default=None, max_length=300)


@router.get("")
async def list_actor_name_mappings(
    runtime: RuntimeDep,
    search: str = "",
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> dict:
    items = runtime.actor_names.list(search=search)
    return {
        "items": [item.__dict__ for item in items[offset : offset + limit]],
        "total": len(items),
        "stats": runtime.actor_names.counts(),
    }


@router.put("")
async def save_actor_name_mapping(req: ActorNameUpdate, runtime: RuntimeDep) -> dict:
    try:
        item = runtime.actor_names.upsert(req.original, req.translation)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return item.__dict__


@router.delete("/{original:path}", status_code=204)
async def delete_actor_name_mapping(original: str, runtime: RuntimeDep) -> None:
    if not runtime.actor_names.delete(original):
        raise HTTPException(404, "演员映射不存在")


@router.get("/export")
async def export_actor_name_mappings(runtime: RuntimeDep) -> JSONResponse:
    return JSONResponse(
        runtime.actor_names.export(),
        headers={"Content-Disposition": 'attachment; filename="actor_name_mappings.json"'},
    )


@router.post("/import")
async def import_actor_name_mappings(payload: dict, runtime: RuntimeDep) -> dict:
    try:
        count = runtime.actor_names.import_data(payload)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"imported": count}
