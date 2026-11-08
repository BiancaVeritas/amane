"""AV Entertainments DVD/Blu-ray catalog crawler.

Searches the site's DVD catalog by product number. A work may have separate DVD
and Blu-ray product IDs; the Blu-ray detail page is preferred, with a complete
DVD-page fallback. Jacket is the single-image poster; Bigcover is the wide cover.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

from parsel import Selector

from ...enums import SiteName
from ..base import Crawler, CrawlerProfile
from ..http import HttpClient, RequestError
from ..models import FetchOptions, MediaMetadata, SearchQuery, film_actors

if TYPE_CHECKING:
    from ...config import SiteConfig


_BASE_URL = "https://www.aventertainments.com"
_SEARCH_PATH = "/dvd/search"

#: 该站的 VerifyBot 按「无 Referer 的浏览器请求」放行; 带上站内 Referer 反而落进挑战分支.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
}


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _number_key(value: str) -> str:
    """Normalize AVE's DVD and BD product numbers to one work identity."""
    compact = re.sub(r"[^A-Z0-9]", "", value.upper())
    return re.sub(r"BD(?=\d)", "", compact)


def _is_bd_number(number: str) -> bool:
    return re.search(r"BD-?\d+$", number.upper()) is not None


def _search_terms(number: str) -> list[str]:
    """Try the exact number and its DVD/BD sibling spelling without duplicates."""
    raw = number.strip().upper()
    terms = [raw]
    match = re.fullmatch(r"([A-Z0-9]+?)(BD)?-?(\d+)", raw)
    if match:
        prefix, bd, digits = match.groups()
        sibling = f"{prefix}{'' if bd else 'BD'}-{digits}"
        if sibling not in terms:
            terms.append(sibling)
    return terms


def _jsonld_video(html: Selector) -> dict:
    for raw in html.xpath('//script[@type="application/ld+json"]/text()').getall():
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        candidates = payload if isinstance(payload, list) else [payload]
        for item in candidates:
            if isinstance(item, dict) and "videoobject" in str(item.get("@type", "")).lower():
                return item
    return {}


def _detail_number(html: Selector, video: dict) -> str:
    for row in html.xpath('//*[contains(concat(" ", normalize-space(@class), " "), " single-info ")]'):
        label = _text(row.xpath('normalize-space(string(.//span[contains(@class,"title")][1]))').get())
        if label == "商品番号":
            value = _text(row.xpath('normalize-space(string(.//span[contains(@class,"tag-title")][1]))').get())
            if value:
                return value
    content_url = _text(video.get("contentUrl"))
    if not content_url:
        content_url = _text(html.xpath("//video[@id='player1_html5']/@data-xl-video-src").get())
    match = re.search(r"/([A-Za-z0-9_-]+)/[^/]*\.m3u8(?:$|\?)", content_url)
    if match:
        return match.group(1)
    return ""


def _label_value(html: Selector, label: str) -> str:
    for row in html.xpath('//*[contains(concat(" ", normalize-space(@class), " "), " single-info ")]'):
        key = _text(row.xpath('normalize-space(string(.//span[contains(@class,"title")][1]))').get())
        if key == label:
            return _text(row.xpath('normalize-space(string(.//span[contains(@class,"value")][1]))').get())
    return ""


def _parse_date(value: str) -> str | None:
    raw = value.strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(raw[:10], fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _parse_runtime(value: str) -> int | None:
    match = re.search(r"(\d+)\s*(?:min|分)", value, re.I)
    return int(match.group(1)) if match else None


def _metadata_from_page(url: str, text: str) -> MediaMetadata | None:
    if "pagenotfound" in url.lower() or "page not found" in text[:10000].lower():
        return None
    html = Selector(text=text)
    video = _jsonld_video(html)
    number = _detail_number(html, video)
    title = (
        _text(video.get("name"))
        or _text(html.xpath("normalize-space(string((//h1)[1]))").get())
        or _text(html.xpath("normalize-space(string((//h2)[1]))").get())
        or _text(html.xpath("normalize-space(string((//h3)[1]))").get())
    )
    if not number:
        return None

    jacket = _text(html.xpath('//meta[@property="og:image"]/@content').get()) or _text(
        html.xpath('//link[@rel="image_src"]/@href').get()
    )
    # Bigcover is the product's double-page cover, not the video player's
    # poster. AVE serves it from both legacy /archive/ and newer /new/ paths.
    # Read only explicit product-image links/images; never use <video poster>.
    bigcover = _text(
        html.xpath(
            "(//a[contains(@href, '/archive/bigcover/') or contains(@href, '/new/bigcover/')]/@href)[1]"
        ).get()
    ) or _text(
        html.xpath(
            "(//img[contains(@src, '/archive/bigcover/') or contains(@src, '/new/bigcover/')]/@src)[1]"
        ).get()
    )
    jacket = urljoin(url, jacket) if jacket else ""
    bigcover = urljoin(url, bigcover) if bigcover else ""

    actor = _label_value(html, "主演女優")
    actors = [name.strip() for name in re.split(r"[、,/／]", actor) if name.strip()]
    categories: list[str] = []
    for row in html.xpath('//*[contains(concat(" ", normalize-space(@class), " "), " single-info ")]'):
        label = _text(row.xpath('normalize-space(string(.//span[contains(@class,"title")][1]))').get())
        if label == "カテゴリ":
            categories = [
                _text(value)
                for value in row.xpath('.//span[contains(@class,"value-category")]//a//text()').getall()
                if _text(value)
            ]
            break

    # The actual synopsis is rendered in the product page body. AVE's JSON-LD
    # description is usually only a title/site tagline, so prefer the dedicated
    # synopsis container and use JSON-LD only as a meaningful fallback.
    synopsis_parts = html.xpath(
        '//*[contains(concat(" ", normalize-space(@class), " "), " product-description ")]//text()'
    ).getall()
    description = re.sub(r"\s+", " ", " ".join(part.strip() for part in synopsis_parts if part.strip())).strip()
    if not description:
        description = _text(video.get("description"))
        if description:
            description = re.split(r"\s*[|｜]\s*AVエンターテインメント", description, maxsplit=1)[0].strip()
            # Discard JSON-LD boilerplate (title-only or site slogan), rather
            # than saving it as a false plot.
            normalized_description = re.sub(r"\s+", " ", description).casefold()
            normalized_title = re.sub(r"\s+", " ", title or "").casefold()
            if not description or normalized_description == normalized_title:
                description = ""
    release = _parse_date(_label_value(html, "発売日"))
    if release is None:
        raw_date = _text(video.get("uploadDate"))
        release = _parse_date(raw_date)
    runtime = _parse_runtime(_label_value(html, "収録時間"))
    studio = _label_value(html, "スタジオ") or None
    series = _label_value(html, "シリーズ") or None

    # Confirmed mapping: Jacket = original poster; Bigcover = double-page cover.
    return MediaMetadata(
        number=number,
        title=title or None,
        plot=description or None,
        actors=film_actors(list(dict.fromkeys(actors))),
        tags=list(dict.fromkeys(categories)),
        release=release,
        runtime=runtime,
        studio=studio,
        series=series,
        poster_urls=[jacket] if jacket else [],
        original_poster_urls=[jacket] if jacket else [],
        thumb_urls=[bigcover] if bigcover else [],
        double_page_poster_urls=[jacket] if jacket and bigcover else [],
        double_page_original_poster_urls=[jacket] if jacket and bigcover else [],
        double_page_thumb_urls=[bigcover] if jacket and bigcover else [],
        external_id=(parse_qs(urlparse(url).query).get("pro") or [None])[0],
        source_url=url,
    )


def _version_links(url: str, text: str) -> tuple[str | None, str | None]:
    html = Selector(text=text)
    blu_ray: str | None = None
    dvd: str | None = None
    for anchor in html.xpath('//*[contains(concat(" ", normalize-space(@class), " "), " tab-area ")]//a[@href]'):
        href = urljoin(url, _text(anchor.xpath("./@href").get()))
        label_parts = (
            _text(anchor.xpath("normalize-space(string(.))").get()),
            _text(anchor.xpath("./@data-tippy").get()),
        )
        label = " ".join(part for part in label_parts if part).casefold()
        if "blu-ray" in label or "blu ray" in label or "ブルーレイ" in label:
            blu_ray = href
        elif re.search(r"\bdvd\b", label):
            dvd = href
    return blu_ray, dvd


def _detail_links(text: str, base_url: str) -> list[str]:
    html = Selector(text=text)
    links: list[str] = []
    for raw in html.xpath("//a[@href]/@href").getall():
        href = urljoin(base_url, _text(raw))
        parsed = urlparse(href)
        if (
            parsed.netloc.casefold() != urlparse(base_url).netloc.casefold()
            or not parsed.path.casefold().rstrip("/").endswith("/dvd/detail")
            or not parse_qs(parsed.query).get("pro")
        ):
            continue
        if href not in links:
            links.append(href)
    return links


def _image_bytes_valid(data: bytes) -> bool:
    return (
        data.startswith(b"\xff\xd8\xff")
        or data.startswith(b"\x89PNG\r\n\x1a\n")
        or (data.startswith(b"RIFF") and data[8:12] == b"WEBP")
        or data.startswith((b"GIF87a", b"GIF89a"))
    )


class AVEntertainmentsCrawler(Crawler):
    def __init__(self, client: HttpClient, config: SiteConfig | None = None):
        super().__init__(client, config)
        self._search_dvd_fallback: str | None = None
        self._search_detail_pages: dict[str, str] = {}

    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(
            name=SiteName.AVENTERTAINMENTS,
            base_url=_BASE_URL,
            urls=["https://imgs02.aventertainments.com"],
            headers=dict(_HEADERS),
        )

    async def _search(self, query: SearchQuery, options: FetchOptions | None = None) -> str | None:
        del options
        self._search_dvd_fallback = None
        self._search_detail_pages = {}
        target = _number_key(query.number)
        candidate_urls: list[str] = []
        for term in _search_terms(query.number):
            # culture 与参数顺序对齐站点自己的表单提交: 缺 culture 时 ASP.NET 按默认区域建会话,
            # 与浏览器访问落到不同分支.
            params = urlencode({"lang": "2", "cat": "29", "culture": "ja-JP", "keyword": term, "searchby": "keyword"})
            search_url = f"{self.base_url.rstrip('/')}{_SEARCH_PATH}?{params}"
            try:
                page = await self.client.get_html(search_url, cookies=self.cookies, headers=self.headers)
            except RequestError as exc:
                if exc.http_status == 404:
                    continue
                raise
            for candidate in _detail_links(page, search_url):
                if candidate not in candidate_urls:
                    candidate_urls.append(candidate)

        matched: list[tuple[int, str]] = []
        for candidate in candidate_urls[:10]:
            try:
                detail_html = await self.client.get_html(candidate, cookies=self.cookies, headers=self.headers)
            except RequestError as exc:
                if exc.http_status == 404:
                    continue
                raise
            self._search_detail_pages[candidate] = detail_html
            metadata = _metadata_from_page(candidate, detail_html)
            if metadata is None or _number_key(metadata.number) != target:
                continue
            is_bd = _is_bd_number(metadata.number)
            matched.append((0 if is_bd else 1, candidate))
        self._search_dvd_fallback = next((candidate for priority, candidate in matched if priority == 1), None)
        if not matched:
            return None
        return min(matched, key=lambda item: item[0])[1]

    async def _scrape(self, url: str, options: FetchOptions | None = None) -> MediaMetadata | None:
        del options
        initial_html = self._search_detail_pages.pop(url, None)
        if initial_html is None:
            try:
                initial_html = await self.client.get_html(url, cookies=self.cookies, headers=self.headers)
            except RequestError as exc:
                if exc.http_status == 404:
                    return None
                raise
        bd_url, dvd_url = _version_links(url, initial_html)
        initial = _metadata_from_page(url, initial_html)
        if initial is None:
            return None

        # If the current page itself is BD, use it as the preferred candidate even
        # when the version tabs are absent. Otherwise prefer the explicit BD tab.
        work_key = _number_key(initial.number)
        current_is_bd = _is_bd_number(initial.number)
        ordered: list[str] = []
        preferred_bd = bd_url or (url if current_is_bd else None)
        preferred_dvd = (
            dvd_url
            or (url if not current_is_bd else None)
            or self._search_dvd_fallback
        )
        for candidate in (preferred_bd, preferred_dvd):
            if candidate and candidate not in ordered:
                ordered.append(candidate)

        for candidate in ordered:
            page = self._search_detail_pages.pop(candidate, None)
            if page is None and candidate == url:
                page = initial_html
            if page is None:
                try:
                    page = await self.client.get_html(
                        candidate,
                        cookies=self.cookies,
                        headers={**self.headers, "Referer": url},
                    )
                except RequestError as exc:
                    if exc.http_status == 404:
                        continue
                    raise
            metadata = _metadata_from_page(candidate, page)
            if (
                metadata is None
                or _number_key(metadata.number) != work_key
                or not metadata.title
                or not metadata.poster_urls
                or not metadata.thumb_urls
            ):
                continue
            image_urls = (*metadata.poster_urls, *metadata.thumb_urls)
            checks = await asyncio.gather(
                *(
                    self.client.get_bytes(
                        image_url,
                        headers={**self.headers, "Referer": candidate},
                    )
                    for image_url in image_urls
                ),
                return_exceptions=True,
            )
            if all(isinstance(data, bytes) and _image_bytes_valid(data) for data in checks):
                return metadata
        return None
