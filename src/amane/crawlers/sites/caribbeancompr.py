"""Caribbeancompr premium title-search crawler backed by JAVBUS title lookup."""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from urllib.parse import quote_from_bytes, urljoin

import structlog
from parsel import Selector, SelectorList

from ...enums import SiteName
from ...utils.dates import normalize_calendar_date
from ..base import Crawler, CrawlerProfile
from ..http import RequestError
from ..models import FetchOptions, MediaMetadata, SearchQuery, film_actors
from .javbus import JavBusCrawler

logger = structlog.get_logger(__name__)


_BASE_URL = "https://www.caribbeancompr.com"
_JAVBUS_URL = "https://www.javbus.com"
_NUMBER_RE = re.compile(r"HEYZO-\d+", re.IGNORECASE)
_INDEX_PATH = Path(__file__).with_name("caribbeancompr_heyzo_index.json")
_index: dict[str, dict[str, object]] | None = None


def _load_index() -> dict[str, dict[str, object]]:
    global _index
    if _index is not None:
        return _index
    try:
        raw = json.loads(_INDEX_PATH.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
        logger.warning("HEYZO 映射索引不可用", path=str(_INDEX_PATH))
        _index = {}
        return _index
    _index = {str(k): v for k, v in raw.items() if isinstance(v, dict)} if isinstance(raw, dict) else {}
    return _index


def _index_entry(number: str) -> dict[str, object] | None:
    digits = re.split(r"[-_ ]+", number.strip())[-1]
    return _load_index().get(digits.zfill(4))


_DETAIL_RE = re.compile(r"/moviepages/(\d{6}_\d{3})/index\.html(?:[?#].*)?$", re.IGNORECASE)
_VOL_RE = re.compile(r"(?:vol\s*\.?\s*0*(\d+)\b|[（(]\s*0*(\d+)\s*[）)])", re.IGNORECASE)
_SEARCH_NOISE_RE = re.compile(r"アダルト動画|HEYZO", re.IGNORECASE)


def _number(value: str) -> str | None:
    candidate = value.strip()
    return candidate.upper() if _NUMBER_RE.fullmatch(candidate) else None


def _split_title(value: str, number: str | None = None) -> tuple[str, int | None]:
    title = re.sub(r"\s+", " ", value or "").strip()
    if number:
        prefix = re.compile(rf"^{re.escape(number)}(?:\s*[-:：_、]?)\s*", re.IGNORECASE)
        while prefix.match(title):
            title = prefix.sub("", title, count=1).lstrip()
    vol: int | None = None
    match = _VOL_RE.search(title)
    if match:
        vol = int(match.group(1) or match.group(2))
        title = title[: match.start()] + title[match.end() :]
    return title.strip(" \t-:：_、"), vol


def _clean_search_title(value: str) -> str:
    """仅清理发往 Caribbeancompr 搜索框的临时标题, 不改写任何持久化原标题."""
    title = unicodedata.normalize("NFKC", value)
    title = _SEARCH_NOISE_RE.sub("", title)
    title = title.replace("-", "")
    return re.sub(r"\s+", " ", title).strip(" \t-:：_、")


def _title_key(value: str) -> str:
    folded = unicodedata.normalize("NFKC", value).casefold()
    return "".join(ch for ch in folded if ch.isalnum())


def _runtime_minutes(value: str | None) -> int | None:
    if not value:
        return None
    match = re.fullmatch(r"\s*(\d{1,2}):(\d{2}):(\d{2})\s*", value)
    if not match:
        return None
    hours, minutes, seconds = map(int, match.groups())
    return hours * 60 + minutes + (1 if seconds >= 30 else 0)


def _javbus_title_release(html_text: str) -> tuple[str | None, str | None]:
    html = Selector(text=html_text)
    title = html.xpath("normalize-space(//h3[1])").get()
    release = html.xpath(
        'normalize-space(//span[contains(concat(" ",normalize-space(@class)," ")," header ") and contains(.,"發行日期:")]/parent::p/text())'
    ).get()
    if release:
        release = normalize_calendar_date(release.strip()) or release.strip()
    return (title.strip() if title else None, release or None)


def _search_candidates(html_text: str, page_url: str) -> list[tuple[str, str]]:
    html = Selector(text=html_text)
    found: list[tuple[str, str]] = []
    for anchor in html.xpath(
        '//a[contains(concat(" ",normalize-space(@class)," ")," meta-title ") and contains(@href, "/moviepages/")]'
    ):
        href = anchor.xpath("./@href").get()
        candidate_title = re.sub(r"\s+", " ", "".join(anchor.xpath(".//text()").getall())).strip()
        if not href or not candidate_title:
            continue
        url = urljoin(page_url, href)
        if _DETAIL_RE.search(url) and (url, candidate_title) not in found:
            found.append((url, candidate_title))
    return found


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
    def __init__(self, client, config=None):
        super().__init__(client, config)
        self._lookup_cache: dict[str, tuple[str | None, str | None, str, int | None]] = {}

    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(name=SiteName.CARIBBEANCOMPR, base_url=_BASE_URL)

    async def _lookup_javbus(self, number: str) -> tuple[str, str | None] | None:
        javbus = JavBusCrawler(self.client)
        try:
            # Intentionally use the complete HEYZO-prefixed number. Do not fall back to bare digits.
            detail_url = await javbus._search_number(number, number)
        except RequestError as exc:
            self.logger.warning("JAVBUS未找到原始番号", number=number, error=str(exc))
            return None
        if not detail_url:
            self.logger.warning("JAVBUS未找到原始番号", number=number)
            return None
        try:
            text = await self.client.get_html(detail_url, cookies=javbus.cookies, headers=javbus.headers)
        except RequestError as exc:
            self.logger.warning("JAVBUS未找到原始番号", number=number, error=str(exc))
            return None
        title, release = _javbus_title_release(text)
        if not title:
            self.logger.warning("JAVBUS未找到原始番号", number=number, detail_url=detail_url)
            return None
        return title, release

    async def _search(self, query: SearchQuery, options: FetchOptions | None = None) -> str | None:
        del options
        number = _number(query.number)
        if not number:
            self.logger.warning("JAVBUS未找到原始番号", number=query.number)
            return None
        # HEYZO 离线映射优先：映射记录就是可信的番号→详情页关系，详情页不要求再次出现番号。
        entry = _index_entry(number)
        mapped_id = entry.get("id") if entry else None
        if isinstance(mapped_id, str) and re.fullmatch(r"\d{6}_\d{3}", mapped_id):
            mapped_url = f"{self.base_url}/moviepages/{mapped_id}/index.html"
            self._lookup_cache[number] = (None, None, mapped_url, None)
            return mapped_url

        # 映射未命中才执行原有内部 JAVBUS 标题提取 + Caribbeancompr 搜索机制。
        lookup = await self._lookup_javbus(number)
        if lookup is None:
            return None
        javbus_title, release = lookup
        subject, source_vol = _split_title(javbus_title, number)
        subject = _clean_search_title(subject)
        if not subject:
            self.logger.warning("标题主体不一致", number=number, javbus_title=javbus_title)
            return None
        search_url = f"{self.base_url}/search/?q={quote_from_bytes(subject.encode('euc_jp'))}"
        try:
            search_html = await self.client.get_html(
                search_url, headers={"Accept-Language": "ja,en;q=0.8"}, encoding="euc_jp"
            )
        except RequestError as exc:
            self.logger.warning("Premium未找到候选商品", number=number, title=subject, error=str(exc))
            return None
        candidates = _search_candidates(search_html, search_url)
        if not candidates:
            self.logger.warning("Premium未找到候选商品", number=number, title=subject)
            return None
        if len(candidates) == 1:
            # Search returned exactly one detail page: use it directly. Title cleanup is
            # only a query aid, so a harmless site-side title decoration must not reject it.
            selected_url = candidates[0][0]
        else:
            source_key = _title_key(subject)
            matching: list[tuple[str, int | None]] = []
            for url, candidate_title in candidates:
                candidate_subject, candidate_vol = _split_title(candidate_title)
                if _title_key(_clean_search_title(candidate_subject)) == source_key:
                    matching.append((url, candidate_vol))
            if not matching:
                self.logger.warning(
                    "标题主体不一致", number=number, title=subject, candidates=[x[1] for x in candidates]
                )
                return None
            if source_vol is not None:
                same_vol = [item for item in matching if item[1] == source_vol]
                conflicting = [item for item in matching if item[1] is not None and item[1] != source_vol]
                if not same_vol and conflicting:
                    self.logger.warning(
                        "续集标记冲突", number=number, source_vol=source_vol, candidate_vols=[x[1] for x in matching]
                    )
                    return None
                if len(same_vol) == 1:
                    selected_url = same_vol[0][0]
                else:
                    self.logger.warning("出现多个候选商品", number=number, title=subject, candidate_count=len(matching))
                    return None
            else:
                self.logger.warning("出现多个候选商品", number=number, title=subject, candidate_count=len(matching))
                return None
        self._lookup_cache[number] = (javbus_title, release, selected_url, source_vol)
        return selected_url

    async def _scrape(self, url: str, options: FetchOptions | None = None) -> MediaMetadata | None:
        del options
        match = _DETAIL_RE.search(url)
        if not match:
            return None
        number = None
        for candidate, cached in self._lookup_cache.items():
            if cached[2] == url:
                number = candidate
                release = cached[1]
                break
        if not number:
            return None
        try:
            text = await self.client.get_html(url, headers={"Accept-Language": "ja,en;q=0.8"}, encoding="euc_jp")
        except RequestError as exc:
            self.logger.warning("Premium未找到候选商品", number=number, url=url, error=str(exc))
            return None
        data, gallery, poster = _detail(text, url)
        mapped_entry = _index_entry(number)
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
            release=release,
            studio="HEYZO",
            publisher="HEYZO",
            poster_urls=[poster],
            cover_urls=[poster],
            extrafanart=gallery,
            original_poster_urls=None,
            source_url=url,
            external_id=match.group(1),
        )
