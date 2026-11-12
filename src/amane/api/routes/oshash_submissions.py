import asyncio
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from httpx2 import AsyncClient, Timeout
from pydantic import BaseModel, Field

from ...enums import SiteName
from ..deps import RepoDep, RuntimeDep

router = APIRouter(prefix="/oshash-submissions", tags=["oshash-submissions"])


class BatchSubmit(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=500)


class BatchDelete(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=500)


@router.get("")
async def list_submissions(
    repo: RepoDep,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
):
    rows, total = await repo.list_oshash_submissions(offset=offset, limit=limit)
    return {"items": [row.model_dump(mode="json") for row in rows], "total": total}


async def _submit_one(runtime: RuntimeDep, repo: RepoDep, submission_id: int) -> tuple[bool, str | None]:
    row = await repo.get_oshash_submission(submission_id)
    if row is None:
        return False, "待提交记录不存在或已被移除"
    cfg = runtime.config.hot.scraping.site_config.get(str(SiteName.THEPORNDB))
    token = cfg.api_token if cfg else None
    if not token:
        error = "未配置 ThePornDB API Token"
        await repo.finish_oshash_submission(submission_id, error=error)
        return False, error
    try:
        async with AsyncClient(
            proxy=runtime.config.hot.network.proxy, timeout=Timeout(30), follow_redirects=True
        ) as client:
            response = await client.post(
                f"https://api.theporndb.net/scenes/{row.theporndb_uuid}/hash",
                headers={"Authorization": f"Bearer {token}"},
                json={"type": "OSHASH", "hash": row.oshash, "duration": row.duration},
            )
        if response.is_success:
            await repo.finish_oshash_submission(submission_id, error=None)
            return True, None
        detail = response.text[:1000] or response.reason_phrase
        error = f"ThePornDB API HTTP {response.status_code}: {detail}"
    except Exception as exc:
        error = f"提交请求失败：{exc}"
    await repo.finish_oshash_submission(submission_id, error=error)
    return False, error


@router.post("/{submission_id}/submit")
async def submit_one(submission_id: int, runtime: RuntimeDep, repo: RepoDep):
    ok, error = await _submit_one(runtime, repo, submission_id)
    if not ok:
        raise HTTPException(502, detail=error)
    return {"submitted": True}


@router.post("/batch")
async def submit_batch(req: BatchSubmit, runtime: RuntimeDep, repo: RepoDep):
    succeeded = failed = 0
    errors: list[str] = []
    for index, submission_id in enumerate(dict.fromkeys(req.ids)):
        if index:
            await asyncio.sleep(0.5)
        ok, error = await _submit_one(runtime, repo, submission_id)
        if ok:
            succeeded += 1
        else:
            failed += 1
            errors.append(f"#{submission_id}: {error}")
    return {"succeeded": succeeded, "failed": failed, "errors": errors}


@router.delete("/{submission_id}")
async def delete_one(submission_id: int, repo: RepoDep):
    deleted = await repo.delete_oshash_submissions([submission_id])
    if not deleted:
        raise HTTPException(404, detail="待提交记录不存在或已被移除")
    return {"deleted": 1}


@router.post("/delete")
async def delete_batch(req: BatchDelete, repo: RepoDep):
    deleted = await repo.delete_oshash_submissions(req.ids)
    return {"deleted": deleted}
