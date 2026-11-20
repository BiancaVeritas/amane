"""Kodi ``<movie>`` NFO, 供 Emby / Jellyfin / Kodi 读取."""

import json
import re
from io import StringIO
from typing import TYPE_CHECKING

import aiofiles
import structlog

from ..enums import ActorGender
from ..parsing import ContentType
from ..utils.text import strip_illegal_xml_chars

if TYPE_CHECKING:
    from pathlib import Path

    from ..config.manager import NfoOutputConfig
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


def _title_with_number(number: str, title: str) -> str:
    """番号已在标题开头时不重复拼接; 后续字符必须是非字母数字边界."""
    text = title.strip()
    if number:
        match = re.match(re.escape(number), text, re.IGNORECASE)
        if match is not None:
            rest = text[match.end() :]
            if not rest or not rest[0].isalnum():
                text = rest.lstrip(" -").strip()
    return f"{number} {text}".strip()


async def write_nfo(
    metadata: Metadata,
    nfo_path: Path,
    *,
    tag_mapping_path: Path | None = None,
    nfo_output: NfoOutputConfig | None = None,
    actor_genders: dict[str, ActorGender] | None = None,
) -> bool:
    try:
        nfo_path.parent.mkdir(parents=True, exist_ok=True)

        code = StringIO()
        code.write('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n')
        code.write("<movie>\n")

        if (nfo_output is None or nfo_output.plot_release.plot) and metadata.plot:
            # 长文本按纯文本写出: 转义后 "]]>" 不可能出现, CDATA 只带来非法 XML 的风险.
            plot_escaped = _escape_xml(metadata.plot)
            code.write(f"  <plot>{plot_escaped}</plot>\n")

        if (nfo_output is None or nfo_output.plot_release.original_plot) and metadata.original_plot:
            code.write(f"  <originalplot>{_escape_xml(metadata.original_plot)}</originalplot>\n")

        if (nfo_output is None or nfo_output.plot_release.release) and metadata.release:
            code.write(f"  <tagline>发行日期 {metadata.release}</tagline>\n")
            code.write(f"  <premiered>{metadata.release}</premiered>\n")
            code.write(f"  <releasedate>{metadata.release}</releasedate>\n")
            code.write(f"  <release>{metadata.release}</release>\n")
            year_match = re.match(r"(\d{4})", metadata.release)
            if year_match:
                code.write(f"  <year>{year_match.group(1)}</year>\n")

        if nfo_output is None or nfo_output.basic_info.number:
            code.write(f"  <num>{_escape_xml(metadata.number)}</num>\n")

        if (nfo_output is None or nfo_output.basic_info.title) and metadata.title:
            code.write(f"  <title>{_escape_xml(metadata.title.strip())}</title>\n")

        if (nfo_output is None or nfo_output.basic_info.original_title) and metadata.original_title:
            original_title = _escape_xml(_title_with_number(metadata.number, metadata.original_title))
            code.write(f"  <originaltitle>{original_title}</originaltitle>\n")
            code.write(f"  <sorttitle>{original_title}</sorttitle>\n")

        if nfo_output is None or nfo_output.plot_release.mpaa:
            mpaa = "NC-17" if metadata.content_type == ContentType.WESTERN else "JP-18+"
            code.write(f"  <mpaa>{mpaa}</mpaa>\n")

        if metadata.actors:
            if nfo_output is None or nfo_output.people.actors:
                selected_actors = metadata.actors
            else:
                selected_genders: set[ActorGender] = set()
                if nfo_output.people.female:
                    selected_genders.add(ActorGender.FEMALE)
                if nfo_output.people.transgender_female:
                    selected_genders.add(ActorGender.TRANSGENDER_FEMALE)
                if nfo_output.people.male:
                    selected_genders.add(ActorGender.MALE)

                selected_actors = [
                    actor
                    for actor in metadata.actors
                    if (actor_genders or {}).get(actor, ActorGender.UNKNOWN) in selected_genders
                ]

            for actor in selected_actors:
                code.write("  <actor>\n")
                code.write(f"    <name>{_escape_xml(actor)}</name>\n")
                code.write("    <type>Actor</type>\n")
                code.write("  </actor>\n")

        if (nfo_output is None or nfo_output.people.director) and metadata.directors:
            for director in metadata.directors:
                if director:
                    code.write(f"  <director>{_escape_xml(director)}</director>\n")

        if (nfo_output is None or nfo_output.ratings.rating) and metadata.score is not None:
            code.write(f"  <rating>{metadata.score}</rating>\n")
            code.write(f"  <criticrating>{int(metadata.score * 10)}</criticrating>\n")

        if (nfo_output is None or nfo_output.plot_release.runtime) and metadata.runtime is not None:
            code.write(f"  <runtime>{metadata.runtime}</runtime>\n")

        series_values = metadata.series_names or ([metadata.series] if metadata.series else [])
        series_names = list(dict.fromkeys(name for name in series_values if name))
        if (nfo_output is None or nfo_output.categories.series) and series_names:
            # Kodi Movie NFO 的 <set> 是单值字段; 保留首项, 多系列完整数据仍保存在 Amane 数据库中.
            code.write(f"  <series>{_escape_xml(series_names[0])}</series>\n")
            code.write("  <set>\n")
            code.write(f"    <name>{_escape_xml(series_names[0])}</name>\n")
            code.write("  </set>\n")

        if (nfo_output is None or nfo_output.people.studio) and metadata.studio:
            code.write(f"  <studio>{_escape_xml(metadata.studio)}</studio>\n")
            code.write(f"  <maker>{_escape_xml(metadata.studio)}</maker>\n")

        if (nfo_output is None or nfo_output.people.publisher) and metadata.publisher:
            code.write(f"  <publisher>{_escape_xml(metadata.publisher)}</publisher>\n")
            code.write(f"  <label>{_escape_xml(metadata.publisher)}</label>\n")

        if metadata.tags:
            write_genre = nfo_output is None or nfo_output.categories.genre
            write_tag = nfo_output is None or nfo_output.categories.tag

            if write_genre or write_tag:
                tags = [tag for tag in metadata.tags if tag]
                if tag_mapping_path is not None and tag_mapping_path.is_file():
                    try:
                        mapping_data = json.loads(tag_mapping_path.read_text(encoding="utf-8"))
                        entries = mapping_data.get("entries", {})
                        if mapping_data.get("enabled", True):
                            tags = list(dict.fromkeys(entries.get(tag, {}).get("translation") or tag for tag in tags))
                    except OSError, ValueError, TypeError:
                        logger.warning(
                            "tag mapping dictionary could not be read; writing original tags",
                            path=str(tag_mapping_path),
                        )

                if write_genre:
                    for tag in tags:
                        code.write(f"  <genre>{_escape_xml(tag)}</genre>\n")

                if write_tag:
                    for tag in tags:
                        code.write(f"  <tag>{_escape_xml(tag)}</tag>\n")

        if (nfo_output is None or nfo_output.images.poster) and metadata.original_poster_urls:
            code.write(f"  <poster>{_escape_xml(metadata.original_poster_urls[0])}</poster>\n")

        if (nfo_output is None or nfo_output.images.cover) and metadata.original_cover_urls:
            code.write(f"  <cover>{_escape_xml(metadata.original_cover_urls[0])}</cover>\n")

        if (nfo_output is None or nfo_output.images.trailer) and metadata.trailer_url:
            code.write(f"  <trailer>{_escape_xml(metadata.trailer_url)}</trailer>\n")

        # 普通来源链接: 使用数据库中保存的完整 URL, 不再读取 external_ids.
        if nfo_output is None or nfo_output.basic_info.source_links:
            for source_key, source_url in metadata.source_urls.items():
                if not source_url:
                    continue

                site = str(source_key).split(":", 1)[0].casefold()

                # WESTERN 的 ThePornDB 来源链接在文末独立输出, 不受普通来源链接开关控制.
                if metadata.content_type == ContentType.WESTERN and site == "theporndb":
                    continue

                code.write(f"  <{site}id>{_escape_xml(str(source_url))}</{site}id>\n")

        # WESTERN NFO 的 ThePornDB 完整来源链接固定放在全部字段末尾, 不受来源链接开关控制.
        if metadata.content_type == ContentType.WESTERN:
            for source_key, source_url in metadata.source_urls.items():
                site = str(source_key).split(":", 1)[0].casefold()
                if site == "theporndb" and source_url:
                    code.write(f"  <theporndbid>{_escape_xml(str(source_url))}</theporndbid>\n")

        code.write("</movie>\n")

        async with aiofiles.open(nfo_path, "w", encoding="UTF-8") as f:
            await f.write(code.getvalue())

        logger.debug("nfo written", path=str(nfo_path))
        return True

    except Exception:
        logger.exception("nfo write failed", path=str(nfo_path))
        return False
