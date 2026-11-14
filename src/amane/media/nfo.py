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


def _display_tags(metadata: Metadata, tag_mapping_path: Path | None) -> list[str]:
    """标签的显示名 (有译文取译文); NFO 的 ``genre`` 用它.

    Emby 把 ``genre`` 当「类型」、``tag`` 当「标签」两个独立维度, 因此标签只进 genre,
    厂商与系列只进 tag, 两边不混.
    """
    tags = [tag for tag in metadata.tags if tag]
    if tag_mapping_path is not None and tag_mapping_path.is_file():
        try:
            mapping_data = json.loads(tag_mapping_path.read_text(encoding="utf-8"))
            entries = mapping_data.get("entries", {})
            tags = [entries.get(tag, {}).get("translation") or tag for tag in tags]
        except OSError, ValueError, TypeError:
            logger.warning(
                "tag mapping dictionary could not be read; writing original tags", path=str(tag_mapping_path)
            )
    return list(dict.fromkeys(tags))


def _vendor_tags(metadata: Metadata) -> list[str]:
    """NFO 的 ``tag`` 取值: 发行商 + 系列.

    只放厂商与系列, 不与标签混: Emby 里「类型」与「标签」是两套筛选口径.
    """
    values = [metadata.publisher, metadata.series]
    # 系列可能是多个; 单个字段与列表字段都要收.
    values.extend(metadata.series_names or [])
    return list(dict.fromkeys(value for value in values if value))


def _load_actor_names(path: Path | None) -> dict[str, str]:
    """读演员显示名映射; 文件缺失或损坏时返回空表, 调用方回退原名."""
    if path is None or not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError, ValueError:
        logger.warning("actor name mapping unreadable; falling back to original names", path=str(path))
        return {}
    entries = raw.get("entries")
    if not isinstance(entries, dict):
        return {}
    names: dict[str, str] = {}
    for original, value in entries.items():
        if isinstance(original, str) and isinstance(value, dict):
            translation = value.get("translation")
            if isinstance(translation, str) and translation.strip():
                names[original] = translation.strip()
    return names


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


def _title_with_number(number: str, title: str) -> str:
    """NFO 显示标题: 「番号 + 空格 + 标题」; 标题已以番号开头时不再重复拼接.

    番号后须为非字母数字, 否则 ``ABF-06`` 会把 ``ABF-062`` 当作自己的前缀.
    """
    text = title.strip()
    if number:
        match = re.match(re.escape(number), text, re.IGNORECASE)
        if match is not None:
            rest = text[match.end() :]
            if not rest or not rest[0].isalnum():
                text = rest.strip()
    return f"{number} {text}".strip()


async def write_nfo(
    metadata: Metadata,
    nfo_path: Path,
    *,
    tag_mapping_path: Path | None = None,
    actor_mapping_path: Path | None = None,
) -> bool:
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
            code.write(f"  <title>{_escape_xml(_title_with_number(metadata.number, metadata.title))}</title>\n")

        # 原题独立成项 (上游没有这一列): 有原题时按同一规则带上番号, 否则沿用显示标题.
        if metadata.original_title:
            original_title = _escape_xml(_title_with_number(metadata.number, metadata.original_title))
            code.write(f"  <originaltitle>{original_title}</originaltitle>\n")
            code.write(f"  <sorttitle>{original_title}</sorttitle>\n")

        code.write("  <mpaa>JP-18+</mpaa>\n")

        # 演员名优先写中文映射: 媒体库里显示的就是熟名, 用原名会让人觉得认不出.
        # 没配映射的演员照原名写, 与映射前完全一致.
        actor_names = _load_actor_names(actor_mapping_path) if actor_mapping_path is not None else {}
        if metadata.actors:
            for actor in metadata.actors:
                code.write("  <actor>\n")
                code.write(f"    <name>{_escape_xml(actor_names.get(actor) or actor)}</name>\n")
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

        # genre = 类型 (标签), tag = 标签 (发行商与系列): Emby 把这两者当独立维度.
        for value in _display_tags(metadata, tag_mapping_path):
            code.write(f"  <genre>{_escape_xml(value)}</genre>\n")
        for value in _vendor_tags(metadata):
            code.write(f"  <tag>{_escape_xml(value)}</tag>\n")

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
