"""测试 NFO 附属文件生成"""

import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING

import pytest

from amane.db.models import Metadata
from amane.media import write_nfo
from amane.parsing.file_info import ContentType

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def metadata() -> Metadata:
    return Metadata(
        number="MIDV-123",
        title="Test Title",
        original_title="Original Test Title",
        original_plot="Original plot",
        actors=["Actor A", "Actor B"],
        studio="Studio X",
        release="2026-01-15",
        runtime=120,
        tags=["Drama", "Romance"],
        series="Series Y",
        scores={"javdb": 85.0},
        directors=["Director Z"],
    )


@pytest.mark.asyncio(loop_scope="function")
async def test_write_nfo_has_required_fields(tmp_path: Path, metadata: Metadata):
    nfo_path = tmp_path / "MIDV-123.nfo"
    ok = await write_nfo(metadata, nfo_path)
    assert ok is True
    assert nfo_path.exists()
    content = nfo_path.read_text(encoding="utf-8")
    assert "<title>MIDV-123 Test Title</title>" in content
    assert "<originaltitle>MIDV-123 Original Test Title</originaltitle>" in content
    assert "<sorttitle>MIDV-123 Original Test Title</sorttitle>" in content
    assert "<originalplot>Original plot</originalplot>" in content
    assert "<tagline>发行日期 2026-01-15</tagline>" in content
    assert "<premiered>2026-01-15</premiered>" in content
    assert "<releasedate>2026-01-15</releasedate>" in content
    assert "<release>2026-01-15</release>" in content
    assert "<num>MIDV-123</num>" in content
    assert "<actor>" in content
    assert "<name>Actor A</name>" in content
    assert "<studio>Studio X</studio>" in content
    assert "<genre>Drama</genre>" in content
    assert "<director>Director Z</director>" not in content
    assert "<outline>" not in content
    assert "<javbusid>" not in content
    assert "<dmmid>" not in content
    assert content.index("<genre>Drama</genre>") < content.index("<tag>Drama</tag>")
    assert content.index("<genre>Romance</genre>") < content.index("<tag>Drama</tag>")
    assert "<rating>85.0</rating>" in content
    # 系列使用规范嵌套: <set><name>…</name></set>
    assert "<series>Series Y</series>" in content
    assert "<set>" in content
    assert "<name>Series Y</name>" in content


@pytest.mark.asyncio(loop_scope="function")
async def test_write_nfo_plot_is_plain_text(tmp_path: Path):
    """长文本按纯文本写出: 无 CDATA, HTML 转义, 非法字符剥离, 换行原样保留."""
    plot = "结尾 ]]> 之后 & <标签>\n\n第二段\x0b"
    nfo_path = tmp_path / "PLOT-1.nfo"

    assert await write_nfo(Metadata(number="PLOT-1", title="T", plot=plot), nfo_path) is True

    content = nfo_path.read_text(encoding="utf-8")
    assert "<![CDATA[" not in content
    assert "]]>" not in content
    assert "\x0b" not in content

    root = ET.fromstring(content)
    assert root.findtext("plot") == "结尾 ]]> 之后 & <标签>\n\n第二段"
    assert root.find("outline") is None


@pytest.mark.asyncio(loop_scope="function")
async def test_write_nfo_uses_original_poster_and_keeps_cover(tmp_path: Path):
    """NFO poster 使用刮削原始地址; cover 继续使用封面地址, 不输出内部裁剪海报 URL."""
    metadata = Metadata(
        number="POSTER-1",
        original_poster_urls=[
            "https://source.example/original-poster.jpg",
            "https://backup.example/original-poster.jpg",
        ],
        poster_urls=["/api/resources/internal-cropped-poster"],
        cover_urls=["/api/resources/manual-cover"],
        original_cover_urls=["https://source.example/cover.jpg"],
    )
    nfo_path = tmp_path / "POSTER-1.nfo"

    assert await write_nfo(metadata, nfo_path) is True

    root = ET.parse(nfo_path).getroot()
    assert root.findtext("poster") == "https://source.example/original-poster.jpg"
    assert root.findtext("cover") == "https://source.example/cover.jpg"
    assert "/api/resources/internal-cropped-poster" not in nfo_path.read_text(encoding="utf-8")
    assert "/api/resources/manual-cover" not in nfo_path.read_text(encoding="utf-8")


@pytest.mark.asyncio(loop_scope="function")
async def test_write_nfo_omits_poster_when_original_url_missing(tmp_path: Path):
    """没有原始海报地址时不以封面或内部裁剪 URL 冒充 poster."""
    metadata = Metadata(
        number="POSTER-2",
        poster_urls=["/api/resources/internal-cropped-poster"],
        cover_urls=["https://source.example/cover.jpg"],
        original_cover_urls=["https://source.example/cover.jpg"],
    )
    nfo_path = tmp_path / "POSTER-2.nfo"

    assert await write_nfo(metadata, nfo_path) is True

    root = ET.parse(nfo_path).getroot()
    assert root.find("poster") is None
    assert root.findtext("cover") == "https://source.example/cover.jpg"


@pytest.mark.asyncio(loop_scope="function")
async def test_write_nfo_exports_western_theporndbid_uuid_at_the_end(tmp_path: Path):
    metadata = Metadata(
        number="WESTERN-001",
        content_type=ContentType.WESTERN,
        theporndb_uuid="96842b9f-0405-481b-a44a-1c7fb5b0370c",
        original_poster_urls=["https://source.example/poster.jpg"],
        cover_urls=["https://source.example/cover.jpg"],
        original_cover_urls=["https://source.example/cover.jpg"],
        trailer_urls=["https://source.example/trailer.mp4"],
        external_ids={"other_source": "external-123", "theporndb": "duplicate-uuid"},
    )
    nfo_path = tmp_path / "WESTERN-001.nfo"

    assert await write_nfo(metadata, nfo_path) is True

    content = nfo_path.read_text(encoding="utf-8")
    root = ET.fromstring(content)
    assert root.find("uniqueid") is None
    assert root.findtext("theporndbid") == metadata.theporndb_uuid
    assert root.findtext("othersourceid") is None
    assert root.findtext("other_sourceid") == "external-123"
    assert content.index("<poster>") < content.index("<cover>")
    assert content.index("<cover>") < content.index("<trailer>")
    assert content.index("<trailer>") < content.index("<other_sourceid>")
    assert content.index("<other_sourceid>") < content.index("<theporndbid>")
    assert content.rstrip().endswith("</movie>")
    assert content.rfind("<theporndbid>") < content.rfind("</movie>")


@pytest.mark.asyncio(loop_scope="function")
async def test_write_nfo_does_not_export_western_uuid_for_non_western(tmp_path: Path):
    metadata = Metadata(
        number="JAV-001",
        content_type=ContentType.CENSORED,
        theporndb_uuid="must-not-export",
    )
    nfo_path = tmp_path / "JAV-001.nfo"

    assert await write_nfo(metadata, nfo_path) is True

    root = ET.parse(nfo_path).getroot()
    assert root.find("uniqueid") is None


@pytest.mark.asyncio(loop_scope="function")
async def test_write_nfo_preserves_multiple_series(tmp_path: Path):
    metadata = Metadata(number="SERIES-2", series="Series A", series_names=["Series A", "Series B"])
    nfo_path = tmp_path / "SERIES-2.nfo"

    assert await write_nfo(metadata, nfo_path) is True
    root = ET.parse(nfo_path).getroot()
    assert root.findtext("series") == "Series A"
    # Kodi Movie NFO 的 set 仅支持一个; 导出首项, Amane 数据库保留完整列表.
    assert [node.findtext("name") for node in root.findall("set")] == ["Series A"]


def test_title_with_number_deduplicates_only_at_token_boundary() -> None:
    from amane.media.nfo import _title_with_number

    assert _title_with_number("ABC-123", "ABC-123 作品标题") == "ABC-123 作品标题"
    assert _title_with_number("ABC-123", "ABC-123-作品标题") == "ABC-123 作品标题"
    assert _title_with_number("ABF-06", "ABF-062作品标题") == "ABF-06 ABF-062作品标题"


@pytest.mark.asyncio(loop_scope="function")
async def test_write_nfo_omits_cover_when_original_url_missing(tmp_path: Path):
    metadata = Metadata(
        number="POSTER-2",
        cover_urls=["/api/resources/manual-cover"],
        original_cover_urls=[],
    )
    nfo_path = tmp_path / "POSTER-2.nfo"
    assert await write_nfo(metadata, nfo_path) is True
    root = ET.parse(nfo_path).getroot()
    assert root.find("cover") is None
