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
# Tokyo-Hot 的番号只有 k / n 两个系列, 字母必须在第一位. 只按 N 判会把 k0794 这类
# 全部拒在搜索之前, 日志里只留一条 search miss, 看不出是被本地正则拦下的.
_NUMBER_RE = re.compile(r"^[KN]\d+$", re.IGNORECASE)
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


def _search_result(text: str, requested_number: str, page_url: str) -> tuple[str | None, str | None]:
    """搜索结果里与 ``requested_number`` 对应的 (详情页地址, 列表缩略图)."""
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
        if match_number != requested_number or not href:
            continue
        detail_url = urljoin(page_url, href)
        absolute = f"{detail_url}?lang=ja" if "?" not in detail_url else detail_url
        thumb = item.xpath('.//a[contains(@class,"rm")]/img/@src').get()
        return absolute, (urljoin(page_url, thumb) if thumb else None)
    return None, None


def _search_result_url(text: str, requested_number: str, page_url: str) -> str | None:
    return _search_result(text, requested_number, page_url)[0]


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

    async def _listing_thumb(self, number: str) -> str | None:
        """再查一次搜索页, 取该条目的列表缩略图.

        只有详情页的两处封面都缺时才走到这里, 属于罕见路径; 宁可多一次请求, 也好过让条目
        一张图都没有.
        """
        search_url = f"{self.base_url}/product/?q={quote(number.lower())}&lang=ja"
        text = await self.client.get_html(search_url)
        if not text:
            return None
        return _search_result(text, number, search_url)[1]

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

        # 封面有两个来源: 侧栏 jacket 链接 (3762×2522 的 DVD 封套) 与播放器的 data-poster
        # (站点按片生成的 820×462 横版图). 前者并非每部都有 —— ``li.package`` 会整段留空,
        # 只认它就会让这类条目一张封面都拿不到, 所以后者兜底.
        jacket = html.xpath(
            '//ul[contains(@class,"control")]//li[contains(@class,"package")]//a[contains(@href,"/jacket/")]/@href'
        ).get()
        if jacket:
            jacket_url = urljoin(url, jacket)
        else:
            poster = html.xpath('//div[contains(@class,"sample-player-shell")]/@data-poster').get()
            jacket_url = urljoin(_CDN_URL, poster) if poster else None
            if jacket_url is None:
                # 两处都没有时再回搜索页取列表缩略图: 它是 220×124 的小图, 但好过没有封面.
                jacket_url = await self._listing_thumb(actual_number)
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
            # 站点的 ``jacket`` 是 3762×2522 的横版封面 (DVD 封套展开图), 不是竖版海报.
            # 它该当封面, 海报交给物化阶段按人脸裁; 只填 poster_urls 会让详情页没有封面可显示.
            poster_urls=[jacket_url] if jacket_url else [],
            original_poster_urls=[jacket_url] if jacket_url else [],
            thumb_urls=[jacket_url] if jacket_url else [],
            extrafanart=extrafanart,
            trailer_urls=[trailer_url] if trailer_url else [],
            source_url=url,
        )
