"""Tokyo-Hot catalogue search and detail-page metadata crawler."""

from __future__ import annotations

import re
from urllib.parse import quote, urljoin

from parsel import Selector

from ...enums import SiteName
from ...utils.dates import normalize_calendar_date
from ..base import Crawler, CrawlerProfile
from ..models import FetchOptions, MediaMetadata, SearchQuery, film_actors

_BASE_URL = "https://my.tokyo-hot.com"
_CDN_URL = "https://my.cdn.tokyo-hot.com"
_NUMBER_RE = re.compile(r"^N\d+$", re.IGNORECASE)
_DETAIL_PATH_RE = re.compile(r"/product/(\d+)/?(?:\?.*)?$")
_NUMBER_LABEL_RE = re.compile(r"作品番号\s*[:：]\s*([A-Z]\d+)", re.IGNORECASE)


def _normalize_number(value: str | None) -> str | None:
    """Tokyo-Hot 番号仅规范首字母大小写, 数字段保持原样."""
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not _NUMBER_RE.fullmatch(candidate):
        return None
    return candidate[0].upper() + candidate[1:]


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value.strip() for value in values if value and value.strip()))


def _label_values(html: Selector, label: str) -> list[str]:
    """只读取详情页 dl.info 中指定 dt 对应的 dd, 不扫描侧栏推荐内容."""
    values = html.xpath(
        '//dl[contains(concat(" ", normalize-space(@class), " "), " info ")]'
        f'/dt[normalize-space()="{label}"]/following-sibling::dd[1]//a//text()'
    ).getall()
    if values:
        return _dedupe(values)
    text = html.xpath(
        '//dl[contains(concat(" ", normalize-space(@class), " "), " info ")]'
        f'/dt[normalize-space()="{label}"]/following-sibling::dd[1]/text()'
    ).getall()
    return _dedupe(text)


def _label_text(html: Selector, label: str) -> str | None:
    values = _label_values(html, label)
    if values:
        return " ".join(values)
    value = html.xpath(
        '//dl[contains(concat(" ", normalize-space(@class), " "), " info ")]'
        f'/dt[normalize-space()="{label}"]/following-sibling::dd[1]//text()'
    ).getall()
    text = re.sub(r"\s+", " ", " ".join(value)).strip()
    return text or None


def _parse_runtime(value: str | None) -> int | None:
    if not value:
        return None
    match = re.fullmatch(r"\s*(\d{1,2}):(\d{2}):(\d{2})\s*", value)
    if match is None:
        return None
    hours, minutes, seconds = (int(part) for part in match.groups())
    return round((hours * 3600 + minutes * 60 + seconds) / 60)


def _search_result_url(text: str, requested_number: str, page_url: str) -> str | None:
    html = Selector(text=text)
    for item in html.xpath('//li[contains(concat(" ", normalize-space(@class), " "), " detail ")]'):
        href = item.xpath('.//a[contains(concat(" ", normalize-space(@class), " "), " rm ")]/@href').get()
        label = " ".join(item.xpath('.//*[contains(@class,"actor")]/text()').getall())
        match = _NUMBER_LABEL_RE.search(label)
        if match is None:
            image_number = item.xpath('.//a[contains(@class,"rm")]/img/@alt').get()
            match_number = _normalize_number(image_number)
        else:
            match_number = _normalize_number(match.group(1))
        if match_number == requested_number and href:
            detail_url = urljoin(page_url, href)
            return f"{detail_url}?lang=ja" if "?" not in detail_url else detail_url
    return None


class TokyoHotCrawler(Crawler):
    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(name=SiteName.TOKYO_HOT, base_url=_BASE_URL)

    async def _search(self, query: SearchQuery, options: FetchOptions | None = None) -> str | None:
        del options
        number = _normalize_number(query.number)
        if number is None:
            return None
        search_url = f"{self.base_url}/product/?q={quote(number.lower())}&lang=ja"
        text = await self.client.get_html(search_url)
        if not text:
            return None
        return _search_result_url(text, number, search_url)

    async def _scrape(self, url: str, options: FetchOptions | None = None) -> MediaMetadata | None:
        del options
        if _DETAIL_PATH_RE.search(url) is None:
            return None
        text = await self.client.get_html(url)
        if not text:
            return None
        html = Selector(text=text)

        actual_number = _normalize_number(_label_text(html, "作品番号"))
        if actual_number is None:
            # Some language variants may render the value in a plain dd text node.
            raw_number = html.xpath(
                '//dl[contains(concat(" ", normalize-space(@class), " "), " info ")]'
                '/dt[normalize-space()="作品番号"]/following-sibling::dd[1]//text()'
            ).get()
            actual_number = _normalize_number(raw_number)
        if actual_number is None:
            return None

        title = html.xpath('normalize-space((//div[contains(@class,"sentence")]/preceding-sibling::h2)[1])').get()
        if not title:
            title = html.xpath("normalize-space((//h2)[1])").get()
        plot_parts = html.xpath(
            '//div[contains(concat(" ", normalize-space(@class), " "), " sentence ")]//text()'
        ).getall()
        plot = re.sub(r"\s+", " ", " ".join(part.strip() for part in plot_parts if part.strip())).strip() or None

        actors = _label_values(html, "出演者")
        play_tags = _label_values(html, "プレイ内容")
        tags = _dedupe([*play_tags, *_label_values(html, "タグ")])
        series_names = _dedupe([*_label_values(html, "シリーズ"), *_label_values(html, "レーベル")])

        release_raw = _label_text(html, "配信開始日")
        release = normalize_calendar_date(release_raw.replace("/", "-") if release_raw else None)
        runtime = _parse_runtime(_label_text(html, "収録時間"))

        jacket = html.xpath(
            '//ul[contains(@class,"control")]//li[contains(@class,"package")]//a[contains(@href,"/jacket/")]/@href'
        ).get()
        jacket_url = urljoin(url, jacket) if jacket else None
        extrafanart = _dedupe(
            [
                urljoin(url, href)
                for href in html.xpath(
                    '//div[contains(concat(" ", normalize-space(@class), " "), " scap ")]'
                    '//a[contains(@href,"/640x480_wlimited.jpg")]/@href'
                ).getall()
            ]
        )
        trailer = html.xpath('//div[contains(@class,"sample-player-shell")]/@data-mp4-src').get()
        trailer_url = urljoin(f"{_CDN_URL}/", trailer) if trailer else None

        return MediaMetadata(
            number=actual_number,
            title=title or None,
            plot=plot,
            actors=film_actors(actors),
            studio="東京熱",
            publisher="Tokyo-Hot",
            release=release,
            runtime=runtime,
            tags=tags,
            series=series_names[0] if series_names else None,
            series_names=series_names,
            poster_urls=[jacket_url] if jacket_url else [],
            original_poster_urls=[jacket_url] if jacket_url else [],
            extrafanart=extrafanart,
            trailer_urls=[trailer_url] if trailer_url else [],
            source_url=url,
        )
