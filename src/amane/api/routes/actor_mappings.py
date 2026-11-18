from __future__ import annotations

import csv
import io
import zipfile
from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from ...mappings.actor_store import ActorMappingStore
from ..deps import RuntimeDep
from .number_mappings import _read_xlsx_rows

router = APIRouter(prefix="/actor-mappings", tags=["actor-mappings"])


class EntryUpdate(BaseModel):
    id: str = Field(min_length=1, max_length=12)
    name: str = Field(min_length=1, max_length=200)
    aliases: list[str] = Field(default_factory=list)
    original_id: str | None = None


class ImportRequest(BaseModel):
    data: dict
    mode: Literal["merge", "replace"] = "merge"


class BatchDeleteRequest(BaseModel):
    ids: list[str]


class ExceptionUpdate(BaseModel):
    group_id: int
    side: Literal["old", "new"]
    entry: dict | None = None


def store(runtime) -> ActorMappingStore:
    return runtime.actor_mappings


@router.get("")
async def list_actor_mappings(runtime: RuntimeDep, search: str = "", limit: int = 30, offset: int = 0):
    data = store(runtime).list(search)
    entries = list(data.items())
    page = entries[max(0, offset) : max(0, offset) + max(1, min(limit, 200))]
    return {"items": [{"id": identifier, **value} for identifier, value in page], "total": len(entries)}


@router.put("")
async def save_actor_mapping(req: EntryUpdate, runtime: RuntimeDep):
    try:
        store(runtime).save(
            req.id,
            {"name": req.name, "aliases": req.aliases},
            req.original_id,
            check_names=True,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"id": req.id, **store(runtime).list()[req.id]}


@router.delete("/{identifier}", status_code=204)
async def delete_actor_mapping(identifier: str, runtime: RuntimeDep):
    if not store(runtime).delete(identifier):
        raise HTTPException(404, "演员映射不存在")


@router.post("/batch-delete")
async def batch_delete_actor_mappings(req: BatchDeleteRequest, runtime: RuntimeDep):
    return {"deleted": store(runtime).delete_many(req.ids)}


@router.get("/exceptions")
async def list_actor_exceptions(runtime: RuntimeDep):
    return store(runtime).list_conflicts()


@router.put("/exceptions")
async def update_actor_exception(req: ExceptionUpdate, runtime: RuntimeDep):
    try:
        entry = None
        if req.entry is not None:
            identifier = str(req.entry.get("id", ""))
            entry = {
                "id": identifier,
                **store(runtime)._validate(identifier, {k: v for k, v in req.entry.items() if k != "id"}),
            }
        return store(runtime).update_conflict(req.group_id, req.side, entry)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/exceptions/save")
async def save_actor_exception(req: ExceptionUpdate, runtime: RuntimeDep):
    if req.entry is None:
        raise HTTPException(400, "异常记录不能为空")
    try:
        identifier = str(req.entry.get("id", ""))
        value = {k: v for k, v in req.entry.items() if k != "id"}
        return store(runtime).save_conflict_entry(req.group_id, req.side, identifier, value)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/exceptions/clear")
async def clear_actor_exceptions(runtime: RuntimeDep):
    store(runtime).clear_conflicts()
    return {"cleared": True}


@router.get("/export")
async def export_actor_mappings(runtime: RuntimeDep, format: Literal["json", "csv"] = "json"):
    if format == "csv":
        return Response(
            store(runtime).export_csv(),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="minnano_actress_map.csv"'},
        )
    return JSONResponse(
        store(runtime).export_data(), headers={"Content-Disposition": 'attachment; filename="minnano_actress_map.json"'}
    )


@router.post("/import")
async def import_actor_mappings(req: ImportRequest, runtime: RuntimeDep):
    try:
        if req.mode == "replace":
            return {"replaced": store(runtime).replace(req.data)}
        return store(runtime).import_merge(req.data)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/import-table")
async def import_actor_mapping_table(
    runtime: RuntimeDep,
    file: Annotated[UploadFile, File()],
    start_column: Annotated[int, Form()] = 1,
    skip_header: Annotated[bool, Form()] = False,
):
    try:
        raw = await file.read()
        filename = (file.filename or "").lower()
        if filename.endswith(".csv"):
            try:
                text = raw.decode("utf-8-sig")
            except UnicodeDecodeError:
                text = raw.decode("gb18030")
            rows = list(csv.reader(io.StringIO(text, newline="")))
        elif filename.endswith((".xlsx", ".xlsm")):
            rows = _read_xlsx_rows(raw)
        else:
            raise ValueError("仅支持 CSV、XLSX 和 XLSM 文件")
        if start_column < 1:
            raise ValueError("数据区域起始列必须大于等于 1")
        if skip_header and rows:
            rows = rows[1:]
        incoming_rows: list[tuple[str, dict]] = []
        for row in rows:
            cells = row[start_column - 1 : start_column + 2]
            if (
                len(cells) < 2
                or cells[0] is None
                or cells[1] is None
                or not str(cells[0]).strip()
                or not str(cells[1]).strip()
            ):
                continue
            identifier = str(cells[0]).strip()
            name = str(cells[1]).strip()
            aliases_raw = str(cells[2]).strip() if len(cells) > 2 and cells[2] is not None else ""
            aliases = [part.strip() for part in aliases_raw.replace("\r", "").split("\n") if part.strip()]
            incoming_rows.append((identifier, {"name": name, "aliases": aliases}))
        result = store(runtime).import_merge_rows(incoming_rows)
        return {"converted": len(incoming_rows), **result}
    except (UnicodeDecodeError, ValueError, OSError, zipfile.BadZipFile) as exc:
        raise HTTPException(400, f"表格文件无法解析或内容无效：{exc}") from exc
    finally:
        await file.close()
