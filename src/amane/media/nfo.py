"""Kodi ``<movie>`` NFO, 供 Emby / Jellyfin / Kodi 读取."""

import json
import re
from io import StringIO
from typing import TYPE_CHECKING

import aiofiles
import structlog

from ..parsing import ContentType
from ..utils.text import strip_illegal_xml_chars

if TYPE_CHECKING:
    from pathlib import Path

    from ..db.models import Metadata


logger = structlog.get_logger()


_XML_ESCAPE_MAP: dict[str, str] = {
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "'": "&apos;",
    '"': "&quot;",
}


def _escape_xml(text: str) -> str:
    """写进 XML 文本节点: 先剥非法字符 (存量脏数据兜底), 再转义; 换行原样保留."""
    escaped = strip_illegal_xml_chars(text)
    for char, entity in _XML_ESCAPE_MAP.items():
        escaped = escaped.replace(char, entity)
    return escaped


async def write_nfo(metadata: Metadata, nfo_path: Path, *, tag_mapping_path: Path | None = None) -> bool:
    try:
        nfo_path.parent.mkdir(parents=True, exist_ok=True)

        code = StringIO()
        code.write('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n')
        code.write("<movie>\n")

        if metadata.plot:
            # 长文本按纯文本写出: 转义后 "]]>" 不可能出现, CDATA 只带来非法 XML 的风险.
            plot_escaped = _escape_xml(metadata.plot)
            code.write(f"  <plot>{plot_escaped}</plot>\n")

        if metadata.original_plot:
            code.write(f"  <originalplot>{_escape_xml(metadata.original_plot)}</originalplot>\n")

        if metadata.release:
            code.write(f"  <tagline>发行日期 {metadata.release}</tagline>\n")
            code.write(f"  <premiered>{metadata.release}</premiered>\n")
            code.write(f"  <releasedate>{metadata.release}</releasedate>\n")
            code.write(f"  <release>{metadata.release}</release>\n")
            year_match = re.match(r"(\d{4})", metadata.release)
            if year_match:
                code.write(f"  <year>{year_match.group(1)}</year>\n")

        code.write(f"  <num>{_escape_xml(metadata.number)}</num>\n")

        if metadata.title:
            code.write(f"  <title>{_escape_xml(metadata.number)} {_escape_xml(metadata.title)}</title>\n")

        if metadata.original_title:
            original_title = f"{_escape_xml(metadata.number)} {_escape_xml(metadata.original_title)}"
            code.write(f"  <originaltitle>{original_title}</originaltitle>\n")
            code.write(f"  <sorttitle>{original_title}</sorttitle>\n")

        code.write("  <mpaa>JP-18+</mpaa>\n")

        if metadata.actors:
            for actor in metadata.actors:
                code.write("  <actor>\n")
                code.write(f"    <name>{_escape_xml(actor)}</name>\n")
                code.write("    <type>Actor</type>\n")
                code.write("  </actor>\n")

        if metadata.score is not None:
            code.write(f"  <rating>{metadata.score}</rating>\n")
            code.write(f"  <criticrating>{int(metadata.score * 10)}</criticrating>\n")

        if metadata.runtime is not None:
            code.write(f"  <runtime>{metadata.runtime}</runtime>\n")

        series_values = metadata.series_names or ([metadata.series] if metadata.series else [])
        series_names = list(dict.fromkeys(name for name in series_values if name))
        if series_names:
            # Kodi Movie NFO 的 <set> 是单值字段; 保留首项, 多系列完整数据仍保存在 Amane 数据库中.
            code.write(f"  <series>{_escape_xml(series_names[0])}</series>\n")
            code.write("  <set>\n")
            code.write(f"    <name>{_escape_xml(series_names[0])}</name>\n")
            code.write("  </set>\n")

        if metadata.studio:
            code.write(f"  <studio>{_escape_xml(metadata.studio)}</studio>\n")
            code.write(f"  <maker>{_escape_xml(metadata.studio)}</maker>\n")

        if metadata.publisher:
            code.write(f"  <publisher>{_escape_xml(metadata.publisher)}</publisher>\n")
            code.write(f"  <label>{_escape_xml(metadata.publisher)}</label>\n")

        if metadata.tags:
            tags = [tag for tag in metadata.tags if tag]
            if tag_mapping_path is not None and tag_mapping_path.is_file():
                try:
                    mapping_data = json.loads(tag_mapping_path.read_text(encoding="utf-8"))
                    entries = mapping_data.get("entries", {})
                    tags = list(dict.fromkeys(entries.get(tag, {}).get("translation") or tag for tag in tags))
                except OSError, ValueError, TypeError:
                    logger.warning(
                        "tag mapping dictionary could not be read; writing original tags", path=str(tag_mapping_path)
                    )
            for tag in tags:
                code.write(f"  <genre>{_escape_xml(tag)}</genre>\n")
            for tag in tags:
                code.write(f"  <tag>{_escape_xml(tag)}</tag>\n")

        if metadata.original_poster_urls:
            code.write(f"  <poster>{_escape_xml(metadata.original_poster_urls[0])}</poster>\n")

        if metadata.thumb_url:
            code.write(f"  <cover>{_escape_xml(metadata.thumb_url)}</cover>\n")

        if metadata.trailer_url:
            code.write(f"  <trailer>{_escape_xml(metadata.trailer_url)}</trailer>\n")

        if metadata.external_ids:
            for site, ext_id in metadata.external_ids.items():
                if ext_id and str(site).casefold() not in {"javbus", "dmm"}:
                    # WESTERN 的 ThePornDB UUID 使用专用列, 只在文末以 theporndbid 输出.
                    if metadata.content_type == ContentType.WESTERN and str(site).casefold() == "theporndb":
                        continue
                    code.write(f"  <{site}id>{_escape_xml(str(ext_id))}</{site}id>\n")

        # WESTERN NFO 的 ThePornDB UUID 固定放在全部字段末尾, 海报/封面/预告及其他 ID 之后.
        if metadata.content_type == ContentType.WESTERN and metadata.theporndb_uuid:
            code.write(f"  <theporndbid>{_escape_xml(metadata.theporndb_uuid)}</theporndbid>\n")

        code.write("</movie>\n")

        async with aiofiles.open(nfo_path, "w", encoding="UTF-8") as f:
            await f.write(code.getvalue())

        logger.debug("nfo written", path=str(nfo_path))
        return True

    except Exception:
        logger.exception("nfo write failed", path=str(nfo_path))
        return False
