"""Kodi ``<movie>`` NFO, 供 Emby / Jellyfin / Kodi 读取."""

import math
import re
from io import StringIO
from typing import TYPE_CHECKING

import aiofiles
import structlog

from ..parsing import ContentType
from ..tags import MappingGroup, VendorNames, load_translations, vendor_names, vendor_roots
from ..utils.text import strip_illegal_xml_chars

if TYPE_CHECKING:
    from pathlib import Path

    from ..db.models import Metadata


logger = structlog.get_logger()


def _nfo_runtime_minutes(metadata: Metadata) -> int | None:
    """写进 NFO 的分钟数: 有秒时向上取整, 没有秒就用已有的分钟值.

    取整规则集中在展示与写盘这一侧, 各站点只负责交出原始秒数 —— 否则每个爬虫各写一套
    四舍五入 / 截断, 同一部片换个来源就会差一分钟.
    """
    if metadata.runtime_seconds is not None and metadata.runtime_seconds > 0:
        return math.ceil(metadata.runtime_seconds / 60)
    return metadata.runtime


def _display_tags(metadata: Metadata, mapping_dir: Path | None) -> list[str]:
    """标签的显示名 (有译文取译文); NFO 的 ``genre`` 用它.

    Emby 把 ``genre`` 当「类型」、``tag`` 当「标签」两个独立维度, 因此标签只进 genre,
    厂商与系列只进 tag, 两边不混.
    """
    translations = load_translations(mapping_dir, MappingGroup.TAG)
    return [translations.get(tag, tag) for tag in dict.fromkeys(tag for tag in metadata.tags if tag)]


def _vendor_tag_values(vendor: VendorNames) -> list[str]:
    """NFO 的 ``tag`` 取值: 发行商 + 全部系列.

    只放厂商与系列, 不与标签混: Emby 里「类型」与「标签」是两套筛选口径.
    """
    return list(dict.fromkeys(value for value in [vendor.publisher, *vendor.series] if value))


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
    mapping_dir: Path | None = None,
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
        actor_names = load_translations(mapping_dir, MappingGroup.ACTOR)
        if metadata.actors:
            for actor in metadata.actors:
                code.write("  <actor>\n")
                code.write(f"    <name>{_escape_xml(actor_names.get(actor) or actor)}</name>\n")
                code.write("    <type>Actor</type>\n")
                code.write("  </actor>\n")

        if metadata.score is not None:
            code.write(f"  <rating>{metadata.score}</rating>\n")
            code.write(f"  <criticrating>{int(metadata.score * 10)}</criticrating>\n")

        # NFO 的 <runtime> 是分钟, 媒体服务器按它显示时长. 有秒时向上取整 ——
        # 站点时长普遍偏短一两秒, 向下取整会让分钟数少 1; 秒恰为 0 时不进位.
        minutes = _nfo_runtime_minutes(metadata)
        if minutes is not None:
            code.write(f"  <runtime>{minutes}</runtime>\n")

        # 片商三件套同样走映射; 缺发行商时已按制作商补齐, 因此不会少一个维度.
        vendor = vendor_names(metadata).translated(vendor_roots(mapping_dir))
        if vendor.series:
            # Kodi Movie NFO 的 <set> 是单值字段; 保留首项, 多系列完整数据仍保存在 Amane 数据库中.
            code.write(f"  <series>{_escape_xml(vendor.series[0])}</series>\n")
            code.write("  <set>\n")
            code.write(f"    <name>{_escape_xml(vendor.series[0])}</name>\n")
            code.write("  </set>\n")

        if vendor.studio:
            code.write(f"  <studio>{_escape_xml(vendor.studio)}</studio>\n")
            code.write(f"  <maker>{_escape_xml(vendor.studio)}</maker>\n")

        if vendor.publisher:
            code.write(f"  <publisher>{_escape_xml(vendor.publisher)}</publisher>\n")
            code.write(f"  <label>{_escape_xml(vendor.publisher)}</label>\n")

        # genre = 类型 (标签), tag = 标签 (发行商与系列): Emby 把这两者当独立维度.
        for value in _display_tags(metadata, mapping_dir):
            code.write(f"  <genre>{_escape_xml(value)}</genre>\n")
        for value in _vendor_tag_values(vendor):
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
