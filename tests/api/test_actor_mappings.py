import csv
import io
import zipfile
from html import escape
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from fastapi import FastAPI
    from httpx2 import AsyncClient


def _make_test_xlsx(rows: list[list[object | None]]) -> bytes:
    """Create a minimal XLSX archive without third-party dependencies."""

    def column_name(index: int) -> str:
        result = ""
        while index:
            index, remainder = divmod(index - 1, 26)
            result = chr(65 + remainder) + result
        return result

    sheet_rows: list[str] = []

    for row_number, values in enumerate(rows, start=1):
        cells: list[str] = []

        for column_number, value in enumerate(values, start=1):
            if value is None:
                continue

            reference = f"{column_name(column_number)}{row_number}"

            if isinstance(value, (int, float)):
                cells.append(f'<c r="{reference}" s="1"><v>{value}</v></c>')
            else:
                cells.append(
                    f'<c r="{reference}" t="inlineStr"><is><t xml:space="preserve">{escape(str(value))}</t></is></c>'
                )

        sheet_rows.append(f'<row r="{row_number}">{"".join(cells)}</row>')

    worksheet = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<sheetData>{''.join(sheet_rows)}</sheetData>"
        "</worksheet>"
    )

    workbook = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<workbook "
        'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets>'
        "</workbook>"
    )

    relationships = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet1.xml"/>'
        "</Relationships>"
    )

    styles = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<numFmts count="1">'
        '<numFmt numFmtId="164" formatCode="000000000000"/>'
        "</numFmts>"
        '<cellXfs count="2">'
        '<xf numFmtId="0" applyNumberFormat="0"/>'
        '<xf numFmtId="164" applyNumberFormat="1"/>'
        "</cellXfs>"
        "</styleSheet>"
    )

    buffer = io.BytesIO()

    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", relationships)
        archive.writestr("xl/worksheets/sheet1.xml", worksheet)
        archive.writestr("xl/styles.xml", styles)

    return buffer.getvalue()


class TestActorMappingExceptions:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_save_exception_with_missing_group_does_not_save_mapping(
        self, client: AsyncClient, app: FastAPI
    ) -> None:
        mappings = app.state.runtime.actor_mappings

        identifier = "999999999999"
        while identifier in mappings.list():
            identifier = str(int(identifier) - 1)

        mappings.add_conflicts(
            [
                {
                    "reason": "id",
                    "old": {"id": "100000000001", "name": "旧演员", "aliases": []},
                    "new": {"id": "100000000002", "name": "新演员", "aliases": []},
                }
            ]
        )

        response = await client.post(
            "actor-mappings/exceptions/save",
            json={
                "group_id": 999,
                "side": "old",
                "entry": {
                    "id": identifier,
                    "name": "API异常保存副作用测试演员",
                    "aliases": [],
                },
            },
        )

        assert response.status_code == 400
        assert identifier not in mappings.list()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_save_exception_clears_only_selected_side(self, client: AsyncClient, app: FastAPI) -> None:
        mappings = app.state.runtime.actor_mappings

        identifier = "999999999998"
        while identifier in mappings.list():
            identifier = str(int(identifier) - 1)

        mappings.add_conflicts(
            [
                {
                    "reason": "id",
                    "old": {
                        "id": "100000000001",
                        "name": "旧演员",
                        "aliases": [],
                    },
                    "new": {
                        "id": "100000000002",
                        "name": "新演员",
                        "aliases": [],
                    },
                }
            ]
        )

        response = await client.post(
            "actor-mappings/exceptions/save",
            json={
                "group_id": 1,
                "side": "old",
                "entry": {
                    "id": identifier,
                    "name": "正常保存测试演员",
                    "aliases": [],
                },
            },
        )

        assert response.status_code == 200
        assert mappings.list()[identifier] == {
            "name": "正常保存测试演员",
            "aliases": [],
        }

        conflicts = mappings.list_conflicts()
        assert len(conflicts) == 1
        assert "old" not in conflicts[0]
        assert conflicts[0]["new"] == {
            "id": "100000000002",
            "name": "新演员",
            "aliases": [],
        }

    @pytest.mark.asyncio(loop_scope="function")
    async def test_save_exception_clears_only_new_side(self, client: AsyncClient, app: FastAPI) -> None:
        mappings = app.state.runtime.actor_mappings

        identifier = "999999999996"
        while identifier in mappings.list():
            identifier = str(int(identifier) - 1)

        mappings.add_conflicts(
            [
                {
                    "reason": "id",
                    "old": {
                        "id": "100000000001",
                        "name": "旧演员",
                        "aliases": [],
                    },
                    "new": {
                        "id": "100000000002",
                        "name": "新演员",
                        "aliases": [],
                    },
                }
            ]
        )

        response = await client.post(
            "actor-mappings/exceptions/save",
            json={
                "group_id": 1,
                "side": "new",
                "entry": {
                    "id": identifier,
                    "name": "正常保存新侧测试演员",
                    "aliases": [],
                },
            },
        )

        assert response.status_code == 200
        assert mappings.list()[identifier] == {
            "name": "正常保存新侧测试演员",
            "aliases": [],
        }

        conflicts = mappings.list_conflicts()
        assert len(conflicts) == 1
        assert conflicts[0]["old"] == {
            "id": "100000000001",
            "name": "旧演员",
            "aliases": [],
        }
        assert "new" not in conflicts[0]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_save_exception_removes_empty_conflict_group(self, client: AsyncClient, app: FastAPI) -> None:
        mappings = app.state.runtime.actor_mappings

        identifier = "999999999995"
        while identifier in mappings.list():
            identifier = str(int(identifier) - 1)

        mappings.add_conflicts(
            [
                {
                    "reason": "id",
                    "old": {
                        "id": "100000000001",
                        "name": "旧演员",
                        "aliases": [],
                    },
                }
            ]
        )

        response = await client.post(
            "actor-mappings/exceptions/save",
            json={
                "group_id": 1,
                "side": "old",
                "entry": {
                    "id": identifier,
                    "name": "清除空异常组测试演员",
                    "aliases": [],
                },
            },
        )

        assert response.status_code == 200
        assert identifier in mappings.list()
        assert mappings.list_conflicts() == []

    @pytest.mark.asyncio(loop_scope="function")
    async def test_save_exception_with_existing_id_does_not_modify_data(
        self, client: AsyncClient, app: FastAPI
    ) -> None:
        mappings = app.state.runtime.actor_mappings

        identifier = "100000000010"
        mappings.save(
            identifier,
            {"name": "已有演员", "aliases": []},
        )

        mappings.add_conflicts(
            [
                {
                    "reason": "id",
                    "old": {
                        "id": "100000000001",
                        "name": "旧演员",
                        "aliases": [],
                    },
                    "new": {
                        "id": "100000000002",
                        "name": "新演员",
                        "aliases": [],
                    },
                }
            ]
        )

        conflicts_before = mappings.list_conflicts()

        response = await client.post(
            "actor-mappings/exceptions/save",
            json={
                "group_id": 1,
                "side": "old",
                "entry": {
                    "id": identifier,
                    "name": "试图覆盖已有演员",
                    "aliases": [],
                },
            },
        )

        assert response.status_code == 400
        assert mappings.list()[identifier] == {
            "name": "已有演员",
            "aliases": [],
        }
        assert mappings.list_conflicts() == conflicts_before

    @pytest.mark.asyncio(loop_scope="function")
    async def test_save_exception_with_existing_name_does_not_modify_data(
        self, client: AsyncClient, app: FastAPI
    ) -> None:
        mappings = app.state.runtime.actor_mappings

        existing_id = "100000000010"
        identifier = "999999999994"

        mappings.save(
            existing_id,
            {"name": "同名演员", "aliases": []},
        )

        mappings.add_conflicts(
            [
                {
                    "reason": "id",
                    "old": {
                        "id": "100000000001",
                        "name": "旧演员",
                        "aliases": [],
                    },
                    "new": {
                        "id": "100000000002",
                        "name": "新演员",
                        "aliases": [],
                    },
                }
            ]
        )

        conflicts_before = mappings.list_conflicts()

        response = await client.post(
            "actor-mappings/exceptions/save",
            json={
                "group_id": 1,
                "side": "old",
                "entry": {
                    "id": identifier,
                    "name": "同名演员",
                    "aliases": [],
                },
            },
        )

        assert response.status_code == 400
        assert identifier not in mappings.list()
        assert mappings.list()[existing_id] == {
            "name": "同名演员",
            "aliases": [],
        }
        assert mappings.list_conflicts() == conflicts_before

    @pytest.mark.asyncio(loop_scope="function")
    async def test_save_exception_with_invalid_side_does_not_modify_data(
        self, client: AsyncClient, app: FastAPI
    ) -> None:
        mappings = app.state.runtime.actor_mappings

        identifier = "999999999997"
        while identifier in mappings.list():
            identifier = str(int(identifier) - 1)

        mappings.add_conflicts(
            [
                {
                    "reason": "id",
                    "old": {
                        "id": "100000000001",
                        "name": "旧演员",
                        "aliases": [],
                    },
                    "new": {
                        "id": "100000000002",
                        "name": "新演员",
                        "aliases": [],
                    },
                }
            ]
        )

        conflicts_before = mappings.list_conflicts()

        response = await client.post(
            "actor-mappings/exceptions/save",
            json={
                "group_id": 1,
                "side": "invalid",
                "entry": {
                    "id": identifier,
                    "name": "无效侧别测试演员",
                    "aliases": [],
                },
            },
        )

        assert response.status_code == 422
        assert identifier not in mappings.list()
        assert mappings.list_conflicts() == conflicts_before

    @pytest.mark.asyncio(loop_scope="function")
    async def test_save_exception_with_empty_entry_does_not_modify_data(
        self, client: AsyncClient, app: FastAPI
    ) -> None:
        mappings = app.state.runtime.actor_mappings

        mappings.add_conflicts(
            [
                {
                    "reason": "id",
                    "old": {
                        "id": "100000000001",
                        "name": "旧演员",
                        "aliases": [],
                    },
                    "new": {
                        "id": "100000000002",
                        "name": "新演员",
                        "aliases": [],
                    },
                }
            ]
        )

        conflicts_before = mappings.list_conflicts()

        response = await client.post(
            "actor-mappings/exceptions/save",
            json={
                "group_id": 1,
                "side": "old",
                "entry": None,
            },
        )

        assert response.status_code == 400
        assert mappings.list_conflicts() == conflicts_before

    @pytest.mark.asyncio(loop_scope="function")
    async def test_update_exception_updates_only_selected_side(self, client: AsyncClient, app: FastAPI) -> None:
        mappings = app.state.runtime.actor_mappings

        mappings.add_conflicts(
            [
                {
                    "reason": "id",
                    "old": {
                        "id": "100000000001",
                        "name": "旧演员",
                        "aliases": [],
                    },
                    "new": {
                        "id": "100000000002",
                        "name": "新演员",
                        "aliases": [],
                    },
                }
            ]
        )

        response = await client.put(
            "actor-mappings/exceptions",
            json={
                "group_id": 1,
                "side": "old",
                "entry": {
                    "id": "100000000003",
                    "name": "修改后的旧演员",
                    "aliases": ["旧演员别名"],
                },
            },
        )

        assert response.status_code == 200

        conflicts = mappings.list_conflicts()
        assert len(conflicts) == 1
        assert conflicts[0]["old"] == {
            "id": "100000000003",
            "name": "修改后的旧演员",
            "aliases": ["旧演员别名"],
        }
        assert conflicts[0]["new"] == {
            "id": "100000000002",
            "name": "新演员",
            "aliases": [],
        }

    @pytest.mark.asyncio(loop_scope="function")
    async def test_update_exception_with_missing_group_does_not_modify_data(
        self, client: AsyncClient, app: FastAPI
    ) -> None:
        mappings = app.state.runtime.actor_mappings

        mappings.add_conflicts(
            [
                {
                    "reason": "id",
                    "old": {
                        "id": "100000000001",
                        "name": "旧演员",
                        "aliases": [],
                    },
                    "new": {
                        "id": "100000000002",
                        "name": "新演员",
                        "aliases": [],
                    },
                }
            ]
        )

        conflicts_before = mappings.list_conflicts()

        response = await client.put(
            "actor-mappings/exceptions",
            json={
                "group_id": 999,
                "side": "old",
                "entry": {
                    "id": "100000000003",
                    "name": "修改后的旧演员",
                    "aliases": [],
                },
            },
        )

        assert response.status_code == 400
        assert mappings.list_conflicts() == conflicts_before

    @pytest.mark.asyncio(loop_scope="function")
    async def test_update_exception_with_invalid_side_does_not_modify_data(
        self, client: AsyncClient, app: FastAPI
    ) -> None:
        mappings = app.state.runtime.actor_mappings

        mappings.add_conflicts(
            [
                {
                    "reason": "id",
                    "old": {
                        "id": "100000000001",
                        "name": "旧演员",
                        "aliases": [],
                    },
                    "new": {
                        "id": "100000000002",
                        "name": "新演员",
                        "aliases": [],
                    },
                }
            ]
        )

        conflicts_before = mappings.list_conflicts()

        response = await client.put(
            "actor-mappings/exceptions",
            json={
                "group_id": 1,
                "side": "invalid",
                "entry": {
                    "id": "100000000003",
                    "name": "修改后的旧演员",
                    "aliases": [],
                },
            },
        )

        assert response.status_code == 422
        assert mappings.list_conflicts() == conflicts_before

    @pytest.mark.asyncio(loop_scope="function")
    async def test_update_exception_with_invalid_id_does_not_modify_data(
        self, client: AsyncClient, app: FastAPI
    ) -> None:
        mappings = app.state.runtime.actor_mappings

        mappings.add_conflicts(
            [
                {
                    "reason": "id",
                    "old": {
                        "id": "100000000001",
                        "name": "旧演员",
                        "aliases": [],
                    },
                    "new": {
                        "id": "100000000002",
                        "name": "新演员",
                        "aliases": [],
                    },
                }
            ]
        )

        conflicts_before = mappings.list_conflicts()

        response = await client.put(
            "actor-mappings/exceptions",
            json={
                "group_id": 1,
                "side": "old",
                "entry": {
                    "id": "invalid-id",
                    "name": "修改后的旧演员",
                    "aliases": [],
                },
            },
        )

        assert response.status_code == 400
        assert mappings.list_conflicts() == conflicts_before

    @pytest.mark.asyncio(loop_scope="function")
    async def test_update_exception_with_empty_entry_clears_only_selected_side(
        self, client: AsyncClient, app: FastAPI
    ) -> None:
        mappings = app.state.runtime.actor_mappings

        mappings.add_conflicts(
            [
                {
                    "reason": "id",
                    "old": {
                        "id": "100000000001",
                        "name": "旧演员",
                        "aliases": [],
                    },
                    "new": {
                        "id": "100000000002",
                        "name": "新演员",
                        "aliases": [],
                    },
                }
            ]
        )

        response = await client.put(
            "actor-mappings/exceptions",
            json={
                "group_id": 1,
                "side": "old",
                "entry": None,
            },
        )

        assert response.status_code == 200

        conflicts = mappings.list_conflicts()
        assert len(conflicts) == 1
        assert "old" not in conflicts[0]
        assert conflicts[0]["new"] == {
            "id": "100000000002",
            "name": "新演员",
            "aliases": [],
        }

    @pytest.mark.asyncio(loop_scope="function")
    async def test_update_exception_updates_only_new_side(self, client: AsyncClient, app: FastAPI) -> None:
        mappings = app.state.runtime.actor_mappings

        mappings.add_conflicts(
            [
                {
                    "reason": "id",
                    "old": {"id": "100000000001", "name": "旧演员", "aliases": []},
                    "new": {"id": "100000000002", "name": "新演员", "aliases": []},
                }
            ]
        )

        response = await client.put(
            "actor-mappings/exceptions",
            json={
                "group_id": 1,
                "side": "new",
                "entry": {
                    "id": "100000000003",
                    "name": "修改后的新演员",
                    "aliases": ["新演员别名"],
                },
            },
        )

        assert response.status_code == 200
        conflicts = mappings.list_conflicts()
        assert conflicts[0]["old"] == {
            "id": "100000000001",
            "name": "旧演员",
            "aliases": [],
        }
        assert conflicts[0]["new"] == {
            "id": "100000000003",
            "name": "修改后的新演员",
            "aliases": ["新演员别名"],
        }

    @pytest.mark.asyncio(loop_scope="function")
    async def test_update_exception_removes_empty_conflict_group(self, client: AsyncClient, app: FastAPI) -> None:
        mappings = app.state.runtime.actor_mappings

        mappings.add_conflicts(
            [
                {
                    "reason": "id",
                    "old": {
                        "id": "100000000001",
                        "name": "旧演员",
                        "aliases": [],
                    },
                }
            ]
        )

        response = await client.put(
            "actor-mappings/exceptions",
            json={
                "group_id": 1,
                "side": "old",
                "entry": None,
            },
        )

        assert response.status_code == 200
        assert mappings.list_conflicts() == []

    @pytest.mark.asyncio(loop_scope="function")
    async def test_update_exception_with_invalid_entry_does_not_modify_data(
        self, client: AsyncClient, app: FastAPI
    ) -> None:
        mappings = app.state.runtime.actor_mappings

        mappings.add_conflicts(
            [
                {
                    "reason": "id",
                    "old": {
                        "id": "100000000001",
                        "name": "旧演员",
                        "aliases": [],
                    },
                    "new": {
                        "id": "100000000002",
                        "name": "新演员",
                        "aliases": [],
                    },
                }
            ]
        )

        conflicts_before = mappings.list_conflicts()

        response = await client.put(
            "actor-mappings/exceptions",
            json={
                "group_id": 1,
                "side": "old",
                "entry": {
                    "id": "100000000003",
                    "name": "",
                    "aliases": [],
                },
            },
        )

        assert response.status_code == 400
        assert mappings.list_conflicts() == conflicts_before

    @pytest.mark.asyncio(loop_scope="function")
    @pytest.mark.parametrize(
        "aliases",
        [
            "不是列表",
            ["正常别名", 123],
        ],
    )
    async def test_update_exception_with_invalid_aliases_does_not_modify_data(
        self, client: AsyncClient, app: FastAPI, aliases: object
    ) -> None:
        mappings = app.state.runtime.actor_mappings

        mappings.add_conflicts(
            [
                {
                    "reason": "id",
                    "old": {
                        "id": "100000000001",
                        "name": "旧演员",
                        "aliases": [],
                    },
                    "new": {
                        "id": "100000000002",
                        "name": "新演员",
                        "aliases": [],
                    },
                }
            ]
        )

        conflicts_before = mappings.list_conflicts()

        response = await client.put(
            "actor-mappings/exceptions",
            json={
                "group_id": 1,
                "side": "old",
                "entry": {
                    "id": "100000000003",
                    "name": "修改后的旧演员",
                    "aliases": aliases,
                },
            },
        )

        assert response.status_code == 400
        assert mappings.list_conflicts() == conflicts_before

    @pytest.mark.asyncio(loop_scope="function")
    async def test_update_exception_with_zero_group_does_not_modify_data(
        self, client: AsyncClient, app: FastAPI
    ) -> None:
        mappings = app.state.runtime.actor_mappings

        mappings.add_conflicts(
            [
                {
                    "reason": "id",
                    "old": {
                        "id": "100000000001",
                        "name": "旧演员",
                        "aliases": [],
                    },
                }
            ]
        )

        conflicts_before = mappings.list_conflicts()

        response = await client.put(
            "actor-mappings/exceptions",
            json={
                "group_id": 0,
                "side": "old",
                "entry": None,
            },
        )

        assert response.status_code == 400
        assert mappings.list_conflicts() == conflicts_before

    @pytest.mark.asyncio(loop_scope="function")
    async def test_list_exceptions_returns_all_conflicts(self, client: AsyncClient, app: FastAPI) -> None:
        mappings = app.state.runtime.actor_mappings

        mappings.add_conflicts(
            [
                {
                    "reason": "id",
                    "old": {
                        "id": "100000000001",
                        "name": "第一组旧演员",
                        "aliases": [],
                    },
                    "new": {
                        "id": "100000000002",
                        "name": "第一组新演员",
                        "aliases": [],
                    },
                },
                {
                    "reason": "name",
                    "old": {
                        "id": "100000000003",
                        "name": "第二组旧演员",
                        "aliases": [],
                    },
                    "new": {
                        "id": "100000000004",
                        "name": "第二组新演员",
                        "aliases": [],
                    },
                },
            ]
        )

        expected = mappings.list_conflicts()

        response = await client.get("actor-mappings/exceptions")

        assert response.status_code == 200
        assert response.json() == expected
        assert len(response.json()) == 2

    @pytest.mark.asyncio(loop_scope="function")
    async def test_list_exceptions_returns_empty_list_when_no_conflicts(self, client: AsyncClient) -> None:
        response = await client.get("actor-mappings/exceptions")

        assert response.status_code == 200
        assert response.json() == []

    @pytest.mark.asyncio(loop_scope="function")
    async def test_clear_exceptions_removes_all_conflicts_without_affecting_mappings(
        self, client: AsyncClient, app: FastAPI
    ) -> None:
        mappings = app.state.runtime.actor_mappings

        identifier = "100000000010"
        mappings.save(
            identifier,
            {"name": "清空异常时保留的演员", "aliases": ["保留别名"]},
        )

        mappings.add_conflicts(
            [
                {
                    "reason": "id",
                    "old": {
                        "id": "100000000001",
                        "name": "第一组旧演员",
                        "aliases": [],
                    },
                    "new": {
                        "id": "100000000002",
                        "name": "第一组新演员",
                        "aliases": [],
                    },
                },
                {
                    "reason": "name",
                    "old": {
                        "id": "100000000003",
                        "name": "第二组旧演员",
                        "aliases": [],
                    },
                    "new": {
                        "id": "100000000004",
                        "name": "第二组新演员",
                        "aliases": [],
                    },
                },
            ]
        )

        assert len(mappings.list_conflicts()) == 2

        response = await client.post("actor-mappings/exceptions/clear")

        assert response.status_code == 200
        assert response.json() == {"cleared": True}
        assert mappings.list_conflicts() == []
        assert mappings.list()[identifier] == {
            "name": "清空异常时保留的演员",
            "aliases": ["保留别名"],
        }

    @pytest.mark.asyncio(loop_scope="function")
    async def test_clear_exceptions_when_already_empty(self, client: AsyncClient, app: FastAPI) -> None:
        mappings = app.state.runtime.actor_mappings

        assert mappings.list_conflicts() == []

        response = await client.post("actor-mappings/exceptions/clear")

        assert response.status_code == 200
        assert response.json() == {"cleared": True}
        assert mappings.list_conflicts() == []


class TestActorMappingImportExport:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_export_json_excludes_exception_records(self, client: AsyncClient, app: FastAPI) -> None:
        mappings = app.state.runtime.actor_mappings

        identifier = "999999999990"
        while identifier in mappings.list():
            identifier = str(int(identifier) - 1)

        mappings.save(
            identifier,
            {"name": "JSON导出测试演员", "aliases": ["JSON导出别名"]},
        )
        mappings.add_conflicts(
            [
                {
                    "reason": "id",
                    "old": {
                        "id": "100000000001",
                        "name": "JSON导出异常旧演员",
                        "aliases": [],
                    },
                    "new": {
                        "id": "100000000002",
                        "name": "JSON导出异常新演员",
                        "aliases": [],
                    },
                }
            ]
        )

        response = await client.get("actor-mappings/export?format=json")

        assert response.status_code == 200
        assert response.json() == mappings.list()
        assert identifier in response.json()
        assert "JSON导出异常旧演员" not in str(response.json())
        assert "JSON导出异常新演员" not in str(response.json())
        assert "attachment; filename=" in response.headers["content-disposition"]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_export_csv_excludes_exception_records(self, client: AsyncClient, app: FastAPI) -> None:
        mappings = app.state.runtime.actor_mappings

        identifier = "999999999989"
        while identifier in mappings.list():
            identifier = str(int(identifier) - 1)

        mappings.save(
            identifier,
            {"name": "CSV导出测试演员", "aliases": ["别名一", "别名二"]},
        )
        mappings.add_conflicts(
            [
                {
                    "reason": "id",
                    "old": {
                        "id": "100000000001",
                        "name": "CSV导出异常旧演员",
                        "aliases": [],
                    },
                    "new": {
                        "id": "100000000002",
                        "name": "CSV导出异常新演员",
                        "aliases": [],
                    },
                }
            ]
        )

        response = await client.get("actor-mappings/export?format=csv")

        assert response.status_code == 200
        assert "text/csv" in response.headers["content-type"]
        assert "attachment; filename=" in response.headers["content-disposition"]

        rows = list(csv.reader(io.StringIO(response.text)))
        assert rows[0] == ["ID", "标准姓名", "别名"]
        assert [
            identifier,
            "CSV导出测试演员",
            "别名一\n别名二",
        ] in rows
        assert "CSV导出异常旧演员" not in response.text
        assert "CSV导出异常新演员" not in response.text

    @pytest.mark.asyncio(loop_scope="function")
    async def test_import_merge_adds_new_records_and_quarantines_collisions(
        self, client: AsyncClient, app: FastAPI
    ) -> None:
        mappings = app.state.runtime.actor_mappings

        def unused_id(start: int) -> str:
            identifier = str(start)
            while identifier in mappings.list():
                identifier = str(int(identifier) - 1)
            return identifier

        existing_id = unused_id(999999999980)
        name_collision_id = unused_id(int(existing_id) - 1)
        new_id = unused_id(int(name_collision_id) - 1)

        mappings.save(
            existing_id,
            {"name": "导入ID冲突旧演员", "aliases": ["旧别名"]},
        )
        mappings.save(
            name_collision_id,
            {"name": "导入姓名冲突演员", "aliases": []},
        )

        response = await client.post(
            "actor-mappings/import",
            json={
                "mode": "merge",
                "data": {
                    existing_id: {
                        "name": "导入ID冲突新演员",
                        "aliases": [],
                    },
                    new_id: {
                        "name": "导入新增演员",
                        "aliases": ["新增别名"],
                    },
                    unused_id(int(new_id) - 1): {
                        "name": "导入姓名冲突演员",
                        "aliases": [],
                    },
                },
            },
        )

        assert response.status_code == 200
        result = response.json()

        assert result["added"] == [new_id]
        assert len(result["conflicts"]) == 2
        assert [item["reason"] for item in result["conflicts"]] == [
            "id",
            "name",
        ]

        current = mappings.list()
        assert existing_id not in current
        assert name_collision_id not in current
        assert current[new_id] == {
            "name": "导入新增演员",
            "aliases": ["新增别名"],
        }

        conflicts = mappings.list_conflicts()
        assert len(conflicts) >= 2
        assert conflicts[-2]["old"]["name"] == "导入ID冲突旧演员"
        assert conflicts[-2]["new"]["name"] == "导入ID冲突新演员"
        assert conflicts[-1]["old"]["name"] == "导入姓名冲突演员"
        assert conflicts[-1]["new"]["name"] == "导入姓名冲突演员"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_import_merge_with_invalid_data_does_not_modify_existing_data(
        self, client: AsyncClient, app: FastAPI
    ) -> None:
        mappings = app.state.runtime.actor_mappings

        map_before = mappings.list()
        conflicts_before = mappings.list_conflicts()

        response = await client.post(
            "actor-mappings/import",
            json={
                "mode": "merge",
                "data": {
                    "999999999970": {
                        "name": "不应写入的演员",
                        "aliases": [],
                    },
                    "invalid-id": {
                        "name": "无效ID演员",
                        "aliases": [],
                    },
                },
            },
        )

        assert response.status_code == 400
        assert mappings.list() == map_before
        assert mappings.list_conflicts() == conflicts_before

    @pytest.mark.asyncio(loop_scope="function")
    async def test_import_replace_replaces_mappings_without_clearing_exceptions(
        self, client: AsyncClient, app: FastAPI
    ) -> None:
        mappings = app.state.runtime.actor_mappings

        mappings.save(
            "999999999960",
            {"name": "替换前演员", "aliases": []},
        )
        mappings.add_conflicts(
            [
                {
                    "reason": "id",
                    "old": {
                        "id": "100000000001",
                        "name": "替换操作保留的旧演员",
                        "aliases": [],
                    },
                    "new": {
                        "id": "100000000002",
                        "name": "替换操作保留的新演员",
                        "aliases": [],
                    },
                }
            ]
        )

        conflicts_before = mappings.list_conflicts()

        response = await client.post(
            "actor-mappings/import",
            json={
                "mode": "replace",
                "data": {
                    "999999999959": {
                        "name": "替换后的演员",
                        "aliases": ["替换后的别名"],
                    }
                },
            },
        )

        assert response.status_code == 200
        assert response.json() == {"replaced": 1}
        assert mappings.list() == {
            "999999999959": {
                "name": "替换后的演员",
                "aliases": ["替换后的别名"],
            }
        }
        assert mappings.list_conflicts() == conflicts_before

    @pytest.mark.asyncio(loop_scope="function")
    async def test_import_replace_with_duplicate_names_does_not_modify_data(
        self, client: AsyncClient, app: FastAPI
    ) -> None:
        mappings = app.state.runtime.actor_mappings

        map_before = mappings.list()
        conflicts_before = mappings.list_conflicts()

        response = await client.post(
            "actor-mappings/import",
            json={
                "mode": "replace",
                "data": {
                    "999999999950": {
                        "name": "重复姓名演员",
                        "aliases": [],
                    },
                    "999999999949": {
                        "name": "重复姓名演员",
                        "aliases": [],
                    },
                },
            },
        )

        assert response.status_code == 400
        assert mappings.list() == map_before
        assert mappings.list_conflicts() == conflicts_before

    @pytest.mark.asyncio(loop_scope="function")
    async def test_import_table_respects_start_column_header_and_aliases(
        self, client: AsyncClient, app: FastAPI
    ) -> None:
        mappings = app.state.runtime.actor_mappings

        identifier = "999999999940"
        while identifier in mappings.list():
            identifier = str(int(identifier) - 1)

        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(["来源", "演员ID", "标准姓名", "别名"])
        writer.writerow(["测试来源", identifier, "表格导入测试演员", "表格别名一\n表格别名二"])

        response = await client.post(
            "actor-mappings/import-table",
            files={
                "file": (
                    "actors.csv",
                    output.getvalue().encode("utf-8-sig"),
                    "text/csv",
                )
            },
            data={
                "start_column": "2",
                "skip_header": "true",
            },
        )

        assert response.status_code == 200
        assert response.json()["converted"] == 1
        assert response.json()["added"] == [identifier]
        assert mappings.list()[identifier] == {
            "name": "表格导入测试演员",
            "aliases": ["表格别名一", "表格别名二"],
        }

    @pytest.mark.asyncio(loop_scope="function")
    async def test_import_table_supports_gb18030_csv(self, client: AsyncClient, app: FastAPI) -> None:
        mappings = app.state.runtime.actor_mappings

        identifier = "999999999930"
        while identifier in mappings.list():
            identifier = str(int(identifier) - 1)

        content = (f"{identifier},GB18030编码测试演员,GB18030别名\n").encode("gb18030")

        response = await client.post(
            "actor-mappings/import-table",
            files={
                "file": (
                    "actors.csv",
                    content,
                    "text/csv",
                )
            },
        )

        assert response.status_code == 200
        assert response.json()["converted"] == 1
        assert mappings.list()[identifier] == {
            "name": "GB18030编码测试演员",
            "aliases": ["GB18030别名"],
        }

    @pytest.mark.asyncio(loop_scope="function")
    async def test_import_table_rejects_unsupported_file_type(self, client: AsyncClient, app: FastAPI) -> None:
        mappings = app.state.runtime.actor_mappings

        map_before = mappings.list()
        conflicts_before = mappings.list_conflicts()

        response = await client.post(
            "actor-mappings/import-table",
            files={
                "file": (
                    "actors.txt",
                    "100000000001,TXT格式测试演员".encode("gb18030"),
                    "text/plain",
                )
            },
        )

        assert response.status_code == 400
        assert mappings.list() == map_before
        assert mappings.list_conflicts() == conflicts_before

    @pytest.mark.asyncio(loop_scope="function")
    async def test_import_table_rejects_invalid_start_column(self, client: AsyncClient, app: FastAPI) -> None:
        mappings = app.state.runtime.actor_mappings

        map_before = mappings.list()
        conflicts_before = mappings.list_conflicts()

        response = await client.post(
            "actor-mappings/import-table",
            files={
                "file": (
                    "actors.csv",
                    "100000000001,CSV起始列测试演员".encode(),
                    "text/csv",
                )
            },
            data={"start_column": "0"},
        )

        assert response.status_code == 400
        assert mappings.list() == map_before
        assert mappings.list_conflicts() == conflicts_before


class TestActorMappingExcelImport:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_import_table_supports_xlsx_with_start_column_header_and_aliases(
        self, client: AsyncClient, app: FastAPI
    ) -> None:
        mappings = app.state.runtime.actor_mappings

        identifier = "100000000101"
        while identifier in mappings.list():
            identifier = str(int(identifier) - 1)

        content = _make_test_xlsx(
            [
                ["说明", "ID", "标准姓名", "别名"],
                ["数据", identifier, "XLSX导入演员", "别名一\n别名二"],
            ]
        )

        response = await client.post(
            "actor-mappings/import-table",
            files={
                "file": (
                    "actors.xlsx",
                    content,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
            data={
                "start_column": "2",
                "skip_header": "true",
            },
        )

        assert response.status_code == 200
        assert response.json()["converted"] == 1
        assert response.json()["added"] == [identifier]
        assert mappings.list()[identifier] == {
            "name": "XLSX导入演员",
            "aliases": ["别名一", "别名二"],
        }

    @pytest.mark.asyncio(loop_scope="function")
    async def test_import_table_supports_xlsm_extension(self, client: AsyncClient, app: FastAPI) -> None:
        mappings = app.state.runtime.actor_mappings

        identifier = "100000000102"
        while identifier in mappings.list():
            identifier = str(int(identifier) - 1)

        content = _make_test_xlsx([[identifier, "XLSM导入演员", "XLSM别名"]])

        response = await client.post(
            "actor-mappings/import-table",
            files={
                "file": (
                    "actors.xlsm",
                    content,
                    "application/vnd.ms-excel.sheet.macroEnabled.12",
                )
            },
        )

        assert response.status_code == 200
        assert response.json()["converted"] == 1
        assert mappings.list()[identifier] == {
            "name": "XLSM导入演员",
            "aliases": ["XLSM别名"],
        }

    @pytest.mark.asyncio(loop_scope="function")
    async def test_import_table_preserves_numeric_identifier_leading_zeroes(
        self, client: AsyncClient, app: FastAPI
    ) -> None:
        mappings = app.state.runtime.actor_mappings

        identifier = "000000000123"

        content = _make_test_xlsx([[123, "数字格式导入演员", "数字格式别名"]])

        response = await client.post(
            "actor-mappings/import-table",
            files={
                "file": (
                    "actors.xlsx",
                    content,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        )

        assert response.status_code == 200
        assert response.json()["converted"] == 1
        assert mappings.list()[identifier] == {
            "name": "数字格式导入演员",
            "aliases": ["数字格式别名"],
        }

    @pytest.mark.asyncio(loop_scope="function")
    async def test_import_table_rejects_corrupted_xlsx_without_modifying_data(
        self, client: AsyncClient, app: FastAPI
    ) -> None:
        mappings = app.state.runtime.actor_mappings

        map_before = mappings.list()
        conflicts_before = mappings.list_conflicts()

        response = await client.post(
            "actor-mappings/import-table",
            files={
                "file": (
                    "broken.xlsx",
                    b"this is not a valid xlsx file",
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        )

        assert response.status_code == 400
        assert mappings.list() == map_before
        assert mappings.list_conflicts() == conflicts_before
