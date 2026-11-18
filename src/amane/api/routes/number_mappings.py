from __future__ import annotations

import csv
import io
import re
import xml.etree.ElementTree as ET
import zipfile
from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ...mappings.store import NumberMappingStore, Source
from ..deps import RuntimeDep

router = APIRouter(prefix="/number-mappings", tags=["number-mappings"])


class EntryUpdate(BaseModel):
    key: str = Field(min_length=1, max_length=300)
    original_key: str | None = Field(default=None, max_length=300)
    id: str | int
    title: str | None = Field(default=None, max_length=2000)
    actor: str | None = Field(default=None, max_length=2000)
    # Legacy field retained solely for compatibility with existing scraper maps.
    override_title: str | None = Field(default=None, max_length=2000)


class ImportRequest(BaseModel):
    data: dict
    mode: Literal["merge", "replace"] = "merge"


class BatchDeleteRequest(BaseModel):
    keys: list[str]


class ExceptionUpdate(BaseModel):
    group_id: int
    side: Literal["old", "new"]
    entry: dict | None = None


def store(runtime) -> NumberMappingStore:
    return runtime.number_mappings


@router.get("")
async def list_number_mappings(runtime: RuntimeDep, source: Source, search: str = "", limit: int = 30, offset: int = 0):
    try:
        data = store(runtime).list(source, search)
    except ValueError as exc:
        raise HTTPException(500, str(exc)) from exc
    entries = list(data.items())
    page = entries[max(0, offset) : max(0, offset) + max(1, min(limit, 200))]
    return {"source": source, "items": [{"key": key, **value} for key, value in page], "total": len(entries)}


@router.put("")
async def save_number_mapping(req: EntryUpdate, runtime: RuntimeDep, source: Source):
    try:
        store(runtime).save(source, req.key, req.model_dump(exclude={"key", "original_key"}), req.original_key)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"key": req.key, **store(runtime).list(source)[req.key]}


@router.delete("/{key:path}", status_code=204)
async def delete_number_mapping(key: str, runtime: RuntimeDep, source: Source):
    if not store(runtime).delete(source, key):
        raise HTTPException(404, "映射不存在")


@router.get("/exceptions")
async def list_number_exceptions(runtime: RuntimeDep, source: Source):
    return store(runtime).list_conflicts(source)


@router.put("/exceptions")
async def update_number_exception(req: ExceptionUpdate, runtime: RuntimeDep, source: Source):
    try:
        if req.entry is not None:
            key = req.entry.get("key")
            if not isinstance(key, str) or not key:
                raise ValueError("番号不能为空")
            clean = store(runtime)._validate(source, key, {k: v for k, v in req.entry.items() if k != "key"})
            req.entry = {"key": key, **clean}
        return store(runtime).update_conflict(source, req.group_id, req.side, req.entry)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/exceptions/save")
async def save_number_exception(req: ExceptionUpdate, runtime: RuntimeDep, source: Source):
    if req.entry is None:
        raise HTTPException(400, "异常记录不能为空")
    try:
        key = req.entry.get("key")
        if not isinstance(key, str) or not key:
            raise ValueError("番号不能为空")
        value = {k: v for k, v in req.entry.items() if k != "key"}
        store(runtime).save(source, key, value)
        return store(runtime).update_conflict(source, req.group_id, req.side, None)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/exceptions/clear")
async def clear_number_exceptions(runtime: RuntimeDep, source: Source):
    store(runtime).clear_conflicts(source)
    return {"cleared": True}


@router.get("/export")
async def export_number_mappings(runtime: RuntimeDep, source: Source):
    return JSONResponse(
        store(runtime).export_data(source), headers={"Content-Disposition": f'attachment; filename="{source}.json"'}
    )


@router.post("/import")
async def import_number_mappings(req: ImportRequest, runtime: RuntimeDep, source: Source):
    try:
        result = (
            store(runtime).replace(source, req.data)
            if req.mode == "replace"
            else store(runtime).import_merge(source, req.data)
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return result if req.mode == "merge" else {"replaced": result}


@router.post("/batch-delete")
async def batch_delete_number_mappings(req: BatchDeleteRequest, runtime: RuntimeDep, source: Source):
    return {"deleted": store(runtime).delete_many(source, req.keys)}


def _read_xlsx_rows(raw: bytes) -> list[list[object | None]]:
    """Read the first worksheet of an XLSX file using the standard library."""
    ns = {
        "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
        "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
        "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
    }
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            shared.extend(
                "".join(node.text or "" for node in item.findall(".//m:t", ns)) for item in root.findall("m:si", ns)
            )
        # Preserve Excel custom zero-padding formats (for example, 0000 -> 0123).
        # The raw numeric value alone would otherwise lose leading zeroes.
        number_formats: dict[int, str] = {}
        if "xl/styles.xml" in archive.namelist():
            styles = ET.fromstring(archive.read("xl/styles.xml"))
            num_fmts = styles.find("m:numFmts", ns)
            custom_formats = {
                int(item.attrib["numFmtId"]): item.attrib.get("formatCode", "")
                for item in (num_fmts.findall("m:numFmt", ns) if num_fmts is not None else [])
            }
            cell_xfs = styles.find("m:cellXfs", ns)
            if cell_xfs is not None:
                for style_id, xf in enumerate(cell_xfs.findall("m:xf", ns)):
                    number_formats[style_id] = custom_formats.get(int(xf.attrib.get("numFmtId", "0")), "")
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        first = workbook.find("m:sheets/m:sheet", ns)
        if first is None:
            return []
        rel_id = first.attrib.get(f"{{{ns['r']}}}id")
        rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        target = next(
            (item.attrib["Target"] for item in rels.findall("rel:Relationship", ns) if item.attrib.get("Id") == rel_id),
            "worksheets/sheet1.xml",
        )
        sheet_path = target.lstrip("/") if target.startswith("/") else f"xl/{target}"
        sheet = ET.fromstring(archive.read(sheet_path))
        rows: list[list[object | None]] = []
        for row_node in sheet.findall(".//m:sheetData/m:row", ns):
            row: list[object | None] = []
            for cell in row_node.findall("m:c", ns):
                ref = cell.attrib.get("r", "A1")
                letters = "".join(char for char in ref if char.isalpha())
                column = 0
                for char in letters.upper():
                    column = column * 26 + ord(char) - 64
                while len(row) < column:
                    row.append(None)
                kind = cell.attrib.get("t")
                value_node = cell.find("m:v", ns)
                if kind == "inlineStr":
                    value: object | None = "".join(node.text or "" for node in cell.findall(".//m:t", ns))
                elif value_node is None or value_node.text is None:
                    value = None
                elif kind == "s":
                    value = shared[int(value_node.text)]
                elif kind == "b":
                    value = value_node.text == "1"
                else:
                    try:
                        number = float(value_node.text)
                        value = int(number) if number.is_integer() else number
                        style_id = int(cell.attrib.get("s", "0"))
                        number_format = number_formats.get(style_id, "")
                        # Handle simple zero masks used to display identifiers with
                        # leading zeroes. Do not attempt to interpret arbitrary Excel
                        # formatting expressions as identifier transformations.
                        positive_format = number_format.split(";", 1)[0].strip()
                        if isinstance(value, int) and re.fullmatch(r"0{2,}", positive_format):
                            value = str(value).zfill(len(positive_format))
                    except ValueError:
                        value = value_node.text
                row[column - 1] = value
            rows.append(row)
        return rows


@router.post("/import-table")
async def import_number_mapping_table(
    runtime: RuntimeDep,
    source: Source,
    file: Annotated[UploadFile, File()],
    start_column: Annotated[int, Form()] = 1,
    skip_header: Annotated[bool, Form()] = False,
):
    """Convert CSV/XLSX relative columns to mapping JSON, then run normal merge import."""
    filename = (file.filename or "").lower()
    try:
        raw = await file.read()
        if filename.endswith(".csv"):
            # Excel on Chinese Windows commonly exports CSV in a legacy
            # system encoding. Prefer UTF-8 (including BOM), then GB18030.
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
        offset = start_column - 1
        incoming_rows: list[tuple[str, dict[str, object]]] = []
        for row_number, row in enumerate(rows, start=2 if skip_header else 1):
            # Fixed table layout: number, ID, actor, title, detail page (ignored).
            # Actor and title are mapping-table metadata; title does not replace the
            # scraper's independent legacy override_title field.
            cells = row[offset : offset + 5]
            # CSV represents empty cells as empty strings, while XLSX parsing
            # represents them as None. Treat both forms as an incomplete row;
            # otherwise a visually blank CSV row (",,") becomes an invalid
            # empty mapping key and aborts the whole import.
            if len(cells) < 2 or cells[0] is None or cells[1] is None or str(cells[0]) == "" or str(cells[1]) == "":
                continue
            key_cell = cells[0]
            key = str(int(key_cell)) if isinstance(key_cell, float) and key_cell.is_integer() else str(key_cell)
            raw_id = cells[1]
            if source == "d2pass":
                if isinstance(raw_id, bool):
                    raise ValueError(f"第 {row_number} 行 ID 必须是整数")
                if isinstance(raw_id, float) and raw_id.is_integer():
                    raw_id = int(raw_id)
                if not str(raw_id).strip().isdigit():
                    raise ValueError(f"第 {row_number} 行 ID 必须是整数")
                identifier: str | int = int(str(raw_id).strip())
            else:
                identifier = str(int(raw_id)) if isinstance(raw_id, float) and raw_id.is_integer() else str(raw_id)
            value: dict[str, object] = {"id": identifier}
            if len(cells) >= 3 and cells[2] is not None and str(cells[2]).strip() != "":
                value["actor"] = str(cells[2]).strip()
            if len(cells) >= 4 and cells[3] is not None and str(cells[3]).strip() != "":
                value["title"] = str(cells[3]).strip()
            incoming_rows.append((key, value))
        result = store(runtime).import_merge_rows(source, incoming_rows)
        return {"converted": len(incoming_rows), **result}
    except (
        UnicodeDecodeError,
        ValueError,
        OSError,
        zipfile.BadZipFile,
        ET.ParseError,
        KeyError,
        IndexError,
    ) as exc:
        # Malformed or truncated workbooks must be reported as an import error,
        # not leak an internal 500 response. The formal mapping file is not
        # touched until parsing and validation have both completed.
        raise HTTPException(400, f"表格文件无法解析或内容无效：{exc}") from exc
    finally:
        await file.close()
