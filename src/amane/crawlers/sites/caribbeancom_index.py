"""Caribbeancom detail-page crawler backed by a filename-to-ID mapping index."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urljoin

import structlog
from parsel import Selector, SelectorList

from ...enums import SiteName
from ..base import Crawler, CrawlerProfile
from ..http import RequestError
from ..models import FetchOptions, MediaMetadata, SearchQuery, film_actors
from .number_mapping import load_number_mapping

logger = structlog.get_logger(__name__)

_BASE_URL = "https://www.caribbeancompr.com"
_MAPPED_ID_RE = re.compile(r"[A-Za-z0-9_-]+")

_DETAIL_RE = re.compile(r"/moviepages/([A-Za-z0-9_-]+)/index\.html(?:[?#].*)?$", re.IGNORECASE)


def _runtime_minutes(value: str | None) -> int | None:
    if not value:
        return None
    match = re.fullmatch(r"\s*(\d{1,2}):(\d{2}):(\d{2})\s*", value)
    if not match:
        return None
    hours, minutes, seconds = map(int, match.groups())
    return hours * 60 + minutes + (1 if seconds >= 30 else 0)


def _detail(html_text: str, page_url: str) -> tuple[dict[str, object], list[str], str | None]:
    html = Selector(text=html_text)
    title = html.xpath(
        'normalize-space(//div[contains(concat(" ",normalize-space(@class)," ")," movie-info ")]//h1[1])'
    ).get()
    plot = html.xpath(
        'normalize-space(//div[contains(concat(" ",normalize-space(@class)," ")," movie-info ")]//p[1])'
    ).get()

    def spec(label: str) -> SelectorList[Selector]:
        row = '//li[contains(concat(" ",normalize-space(@class)," ")," movie-spec ")]'
        title = (
            f'/span[contains(concat(" ",normalize-space(@class)," ")," spec-title ") and normalize-space()="{label}"]'
        )
        content = '/following-sibling::span[contains(concat(" ",normalize-space(@class)," ")," spec-content ")][1]'
        return html.xpath(row + title + content)

    actors = [
        name
        for anchor in spec("出演").xpath(".//a")
        if (name := re.sub(r"\s+", " ", "".join(anchor.xpath(".//text()").getall())).strip())
    ]
    runtime_text = " ".join(spec("再生時間").xpath(".//text()").getall()).strip()
    tags = [
        re.sub(r"\s+", " ", value).strip()
        for value in spec("タグ")
        .xpath('.//a[contains(concat(" ",normalize-space(@class)," ")," spec-item ")]/text()')
        .getall()
        if value.strip()
    ]

    poster_match = re.search(
        r"""\bposterImage\s*=\s*['\"]([^'\"]+)['\"]\s*\+\s*movie_id\s*\+\s*['\"]([^'\"]+)['\"]""",
        html_text,
        flags=re.IGNORECASE,
    )
    poster = None
    if poster_match:
        detail_match = _DETAIL_RE.search(page_url)
        if detail_match:
            poster = urljoin(page_url, poster_match.group(1) + detail_match.group(1) + poster_match.group(2))

    if not poster:
        poster_path = html.xpath('//div[contains(concat(" ",normalize-space(@class)," ")," vjs-poster ")]/@style').get()
        css_poster_match = re.search(r"url\([\"']?(.*?)[\"']?\)", poster_path or "")
        poster = urljoin(page_url, css_poster_match.group(1)) if css_poster_match else None

    gallery = []
    for href in html.xpath(
        '//a[contains(concat(" ",normalize-space(@class)," ")," gallery-image-wrap ") and @data-is_sample="1"]/@href'
    ).getall():
        absolute = urljoin(page_url, href)
        if "/member/" not in absolute and absolute not in gallery:
            gallery.append(absolute)
    metadata: dict[str, object] = {
        "title": title or None,
        "plot": plot or None,
        "actors": list(dict.fromkeys(actors)),
        "runtime": _runtime_minutes(runtime_text),
        "tags": list(dict.fromkeys(tags)),
    }
    return metadata, gallery[:3], poster


def _index_entry(number: str, mapping_path: Path | None) -> dict[str, object] | None:
    # 只忽略英文字母大小写; 连字符、其他符号及数字(包括前导零)均原样参与匹配.
    normalized = number.casefold()
    return next(
        (entry for key, entry in load_number_mapping(mapping_path).items() if key.casefold() == normalized),
        None,
    )


class CaribbeancomIndexCrawler(Crawler):
    def __init__(self, client, config=None, *, mapping_path: Path | None = None):
        super().__init__(client, config)
        self._mapping_path = mapping_path
        self._lookup_cache: dict[str, tuple[str, str]] = {}

    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(name=SiteName.CARIBBEANCOM_INDEX, base_url=_BASE_URL)

    async def _search(self, query: SearchQuery, options: FetchOptions | None = None) -> str | None:
        del options
        number = query.original_number or query.number
        entry = _index_entry(number, self._mapping_path)
        mapped_id = entry.get("id") if entry else None
        if not isinstance(mapped_id, str) or not _MAPPED_ID_RE.fullmatch(mapped_id):
            self.logger.info("Caribbeancom Index 映射未命中", number=number)
            return None
        url = f"{self.base_url}/moviepages/{mapped_id}/index.html"
        self._lookup_cache[url] = (number, number)
        return url

    async def _scrape(self, url: str, options: FetchOptions | None = None) -> MediaMetadata | None:
        del options
        cached = self._lookup_cache.pop(url, None)
        if cached is None:
            return None
        number, canonical_number = cached
        try:
            text = await self.client.get_html(url, headers={"Accept-Language": "ja,en;q=0.8"}, encoding="euc_jp")
        except RequestError as exc:
            self.logger.warning("Caribbeancom Index 详情页请求失败", number=number, url=url, error=str(exc))
            return None
        data, gallery, poster = _detail(text, url)
        entry = _index_entry(number, self._mapping_path)
        override_title = entry.get("override_title") if entry else None
        if isinstance(override_title, str) and override_title.strip():
            data["title"] = override_title.strip()
        if not data["title"] or not poster:
            self.logger.info("Caribbeancom Index 详情页缺少必要信息", number=number, url=url)
            return None
        return MediaMetadata(
            # 检索结果归属原始番号, 不使用网页或其他来源返回的番号覆盖影片身份.
            number=canonical_number,
            title=data["title"] if isinstance(data["title"], str) else None,
            plot=data["plot"] if isinstance(data["plot"], str) else None,
            actors=film_actors(data["actors"] if isinstance(data["actors"], list) else []),
            runtime=data["runtime"] if isinstance(data["runtime"], int) else None,
            tags=data["tags"] if isinstance(data["tags"], list) else [],
            release=None,
            studio=None,
            publisher=None,
            poster_urls=[poster],
            cover_urls=[poster],
            extrafanart=gallery,
            source_url=url,
            external_id=url.split("/moviepages/", 1)[-1].split("/", 1)[0],
        )
