import asyncio
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from httpx2 import AsyncClient, RequestError, Timeout
from pydantic import BaseModel, Field

from ...db.models import OshashSubmissionStatus
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


async def _submit_one(
    runtime: RuntimeDep,
    repo: RepoDep,
    submission_id: int,
) -> tuple[bool, str | None, int]:
    row = await repo.get_oshash_submission(submission_id)
    if row is None:
        return False, "待提交记录不存在或已被移除", 404

    if not await repo.claim_oshash_submission(submission_id):
        current = await repo.get_oshash_submission(submission_id)
        if current is None:
            return False, "待提交记录不存在或已被移除", 404
        return False, "记录正在提交或等待人工确认，不能重复提交", 409

    cfg = runtime.config.hot.scraping.site_config.get(str(SiteName.THEPORNDB))
    token = cfg.api_token if cfg else None
    if not token:
        error = "未配置 ThePornDB API Token"
        await repo.finish_oshash_submission(submission_id, error=error)
        return False, error, 503

    try:
        async with AsyncClient(
            proxy=runtime.config.hot.network.proxy,
            timeout=Timeout(30),
            follow_redirects=True,
        ) as client:
            response = await client.post(
                f"https://api.theporndb.net/scenes/{row.theporndb_uuid}/hash",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "type": "OSHASH",
                    "hash": row.oshash,
                    "duration": row.duration,
                },
            )
    except RequestError as exc:
        error = f"提交请求结果不确定：{exc}"
        await repo.finish_oshash_submission(
            submission_id,
            error=error,
            uncertain=True,
        )
        return False, error, 502
    except Exception as exc:
        error = f"提交过程中发生异常，结果不确定：{exc}"
        await repo.finish_oshash_submission(
            submission_id,
            error=error,
            uncertain=True,
        )
        return False, error, 502

    if response.is_success:
        await repo.finish_oshash_submission(submission_id, error=None)
        return True, None, 200

    detail = response.text[:1000] or response.reason_phrase
    error = f"ThePornDB API HTTP {response.status_code}: {detail}"

    uncertain = response.status_code < 400 or response.status_code >= 500
    await repo.finish_oshash_submission(
        submission_id,
        error=error,
        uncertain=uncertain,
    )
    return False, error, 502


@router.post("/{submission_id}/submit")
async def submit_one(submission_id: int, runtime: RuntimeDep, repo: RepoDep):
    ok, error, status_code = await _submit_one(runtime, repo, submission_id)
    if not ok:
        raise HTTPException(status_code, detail=error)
    return {"submitted": True}


@router.post("/batch")
async def submit_batch(req: BatchSubmit, runtime: RuntimeDep, repo: RepoDep):
    succeeded = failed = 0
    errors: list[str] = []

    for index, submission_id in enumerate(dict.fromkeys(req.ids)):
        if index:
            await asyncio.sleep(0.5)

        ok, error, _ = await _submit_one(runtime, repo, submission_id)
        if ok:
            succeeded += 1
        else:
            failed += 1
            errors.append(f"#{submission_id}: {error}")

    return {"succeeded": succeeded, "failed": failed, "errors": errors}


@router.post("/{submission_id}/confirm-accepted")
async def confirm_accepted(submission_id: int, repo: RepoDep):
    resolved = await repo.confirm_oshash_submission_accepted(submission_id)
    if not resolved:
        row = await repo.get_oshash_submission(submission_id)
        if row is None:
            raise HTTPException(404, detail="待提交记录不存在或已被移除")
        raise HTTPException(409, detail="只有等待人工确认的记录才能执行此操作")

    return {"resolved": "accepted", "deleted": True}


@router.post("/{submission_id}/confirm-not-accepted")
async def confirm_not_accepted(submission_id: int, repo: RepoDep):
    resolved = await repo.retry_uncertain_oshash_submission(submission_id)
    if not resolved:
        row = await repo.get_oshash_submission(submission_id)
        if row is None:
            raise HTTPException(404, detail="待提交记录不存在或已被移除")
        raise HTTPException(409, detail="只有等待人工确认的记录才能执行此操作")

    return {"resolved": "not_accepted", "status": OshashSubmissionStatus.PENDING}


@router.delete("/{submission_id}")
async def delete_one(submission_id: int, repo: RepoDep):
    deleted = await repo.delete_oshash_submissions([submission_id])
    if not deleted:
        row = await repo.get_oshash_submission(submission_id)
        if row is None:
            raise HTTPException(404, detail="待提交记录不存在或已被移除")
        raise HTTPException(409, detail="只能删除待提交状态的记录")

    return {"deleted": 1}


@router.post("/delete")
async def delete_batch(req: BatchDelete, repo: RepoDep):
    deleted = await repo.delete_oshash_submissions(req.ids)
    return {"deleted": deleted}
