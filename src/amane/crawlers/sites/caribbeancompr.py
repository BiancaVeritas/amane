"""Caribbeancompr HEYZO crawler backed exclusively by the local number mapping."""

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
_NUMBER_RE = re.compile(r"HEYZO-\d+", re.IGNORECASE)


_DETAIL_RE = re.compile(r"/moviepages/(\d{6}_\d{3})/index\.html(?:[?#].*)?$", re.IGNORECASE)


def _number(value: str) -> str | None:
    candidate = value.strip()
    return candidate.upper() if _NUMBER_RE.fullmatch(candidate) else None


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
        return html.xpath(
            '//li[contains(concat(" ",normalize-space(@class)," ")," movie-spec ")]'
            f'/span[contains(concat(" ",normalize-space(@class)," ")," spec-title ") and normalize-space()="{label}"]/following-sibling::span[contains(concat(" ",normalize-space(@class)," ")," spec-content ")][1]'
        )

    # 以每个演员链接为边界聚合其内部文本，避免嵌套 span 将一个姓名拆成多段。
    actors = [
        name
        for anchor in spec("出演").xpath(".//a")
        if (name := re.sub(r"\s+", " ", "".join(anchor.xpath(".//text()").getall())).strip())
    ]
    runtime_text = " ".join(spec("再生時間").xpath(".//text()").getall()).strip()
    tags = [
        re.sub(r"\s+", " ", x).strip()
        for x in spec("タグ")
        .xpath('.//a[contains(concat(" ",normalize-space(@class)," ")," spec-item ")]/text()')
        .getall()
        if x.strip()
    ]

    # Caribbeancompr builds the player poster URL in JavaScript:
    # posterImage = '/moviepages/' + movie_id + '/images/l_l.jpg'
    poster_match = re.search(
        r"""\bposterImage\s*=\s*['"]([^'"]+)['"]\s*\+\s*movie_id\s*\+\s*['"]([^'"]+)['"]""",
        html_text,
        flags=re.IGNORECASE,
    )
    poster = None
    if poster_match:
        detail_match = _DETAIL_RE.search(page_url)
        if detail_match:
            poster_path = poster_match.group(1) + detail_match.group(1) + poster_match.group(2)
            poster = urljoin(page_url, poster_path)

    if not poster:
        # Keep the original CSS poster parser as a fallback.
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


class CaribbeancomprCrawler(Crawler):
    def __init__(self, client, config=None, *, mapping_path: Path | None = None):
        super().__init__(client, config)
        self._mapping_path = mapping_path
        self._lookup_cache: dict[str, str] = {}

    def _index_entry(self, number: str) -> dict[str, object] | None:
        digits = re.split(r"[-_ ]+", number.strip())[-1]
        return load_number_mapping(self._mapping_path).get(digits.zfill(4))

    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(name=SiteName.CARIBBEANCOMPR, base_url=_BASE_URL)

    async def _search(self, query: SearchQuery, options: FetchOptions | None = None) -> str | None:
        del options
        self._lookup_cache.clear()
        number = _number(query.number)
        if not number:
            self.logger.warning("HEYZO 映射番号无效", number=query.number)
            return None
        entry = self._index_entry(number)
        mapped_id = entry.get("id") if entry else None
        if not isinstance(mapped_id, str) or not re.fullmatch(r"\d{6}_\d{3}", mapped_id):
            self.logger.info("Caribbeancompr HEYZO 映射未命中", number=number)
            return None
        url = f"{self.base_url}/moviepages/{mapped_id}/index.html"
        self._lookup_cache[number] = url
        return url

    async def _scrape(self, url: str, options: FetchOptions | None = None) -> MediaMetadata | None:
        del options
        match = _DETAIL_RE.search(url)
        if not match:
            return None
        number = next((candidate for candidate, mapped_url in self._lookup_cache.items() if mapped_url == url), None)
        if not number:
            return None
        try:
            text = await self.client.get_html(url, headers={"Accept-Language": "ja,en;q=0.8"}, encoding="euc_jp")
        except RequestError as exc:
            self.logger.warning("Premium未找到候选商品", number=number, url=url, error=str(exc))
            return None
        data, gallery, poster = _detail(text, url)
        mapped_entry = self._index_entry(number)
        override_title = mapped_entry.get("override_title") if mapped_entry else None
        if isinstance(override_title, str) and override_title.strip():
            data["title"] = override_title.strip()
        if not data["title"]:
            self.logger.warning("Premium未找到候选商品", number=number, url=url, detail="详情页缺少标题")
            return None
        if not poster:
            self.logger.warning("Premium未找到候选商品", number=number, url=url, detail="详情页缺少播放器静态图")
            return None
        return MediaMetadata(
            number=number,
            title=data["title"] if isinstance(data["title"], str) else None,
            plot=data["plot"] if isinstance(data["plot"], str) else None,
            actors=film_actors(data["actors"] if isinstance(data["actors"], list) else []),
            runtime=data["runtime"] if isinstance(data["runtime"], int) else None,
            tags=data["tags"] if isinstance(data["tags"], list) else [],
            release=None,
            studio="HEYZO",
            publisher="HEYZO",
            poster_urls=[poster],
            cover_urls=[poster],
            extrafanart=gallery,
            original_poster_urls=None,
            source_url=url,
            external_id=match.group(1),
        )
