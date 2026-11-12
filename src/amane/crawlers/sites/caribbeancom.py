"""Caribbeancom regular-site detail-page crawler."""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from urllib.parse import urljoin

from ...enums import SiteName
from ...net.connectivity import ConnectivityOutcome, probe_get
from ..base import Crawler, CrawlerProfile
from ..models import FetchOptions, MediaMetadata, SearchQuery, film_actors

_BASE_URL = "https://www.caribbeancom.com"
_NUMBER_RE = re.compile(r"[0-9]{6}-[0-9]{3}")
_CONNECTIVITY_NUMBER = "072926-001"


def _number(value: str) -> str | None:
    """Keep the exact six-digit-three-digit hyphenated identifier."""
    return value if _NUMBER_RE.fullmatch(value) else None


class _DetailParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.plot = ""
        self.actors: list[str] = []
        self.release = ""
        self.runtime = ""
        self.series = ""
        self.tags: list[str] = []
        self.gallery: list[str] = []
        self._capture: str | None = None
        self._capture_tag = ""
        self._capture_depth = 0
        self._capture_text: list[str] = []
        self._spec_key = ""
        self._in_spec_content = False
        self._spec_depth = 0
        self._spec_text: list[str] = []
        self._actor_link_depth = 0
        self._actor_link_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        classes = set((a.get("class") or "").split())
        if tag == "a" and "gallery-item" in classes:
            href = a.get("href", "") or ""
            if (
                a.get("data-is_sample") == "1"
                and href
                and not href.startswith(("javascript:", "#"))
                and href not in self.gallery
            ):
                self.gallery.append(href)
        if tag == "h1" and a.get("itemprop") == "name":
            self._start_capture("title", tag)
        elif tag == "p" and a.get("itemprop") == "description":
            self._start_capture("plot", tag)
        elif tag == "span" and "spec-title" in classes:
            self._start_capture("spec-title", tag)
        elif tag == "span" and "spec-content" in classes:
            self._in_spec_content = True
            self._spec_depth = 1
            self._spec_text = []
        elif tag == "a" and "spec-item" in classes:
            self._start_capture("tag", tag)
        elif self._capture:
            self._capture_depth += 1
        if self._in_spec_content and not (tag == "span" and "spec-content" in classes):
            self._spec_depth += 1
            if self._spec_key == "出演" and tag == "a":
                self._actor_link_depth = 1
                self._actor_link_text = []
            elif self._actor_link_depth:
                self._actor_link_depth += 1

    def _start_capture(self, kind: str, tag: str) -> None:
        self._capture = kind
        self._capture_tag = tag
        self._capture_depth = 1
        self._capture_text = []

    def handle_endtag(self, tag: str) -> None:
        if self._capture:
            self._capture_depth -= 1
            if self._capture_depth <= 0 and tag == self._capture_tag:
                value = "".join(self._capture_text).strip()
                if self._capture == "title":
                    self.title = value
                elif self._capture == "plot":
                    self.plot = value
                elif self._capture == "spec-title":
                    self._spec_key = value
                elif self._capture == "tag" and value and value not in self.tags:
                    self.tags.append(value)
                self._capture = None
        if self._in_spec_content:
            if self._actor_link_depth:
                self._actor_link_depth -= 1
                if self._actor_link_depth == 0 and tag == "a":
                    name = re.sub(r"\s+", " ", "".join(self._actor_link_text)).strip()
                    if name and name not in self.actors:
                        self.actors.append(name)
                    self._actor_link_text = []
            self._spec_depth -= 1
            if self._spec_depth <= 0 and tag == "span":
                value = "".join(self._spec_text).strip()
                if self._spec_key == "出演":
                    # 只按独立演员链接取名，避免相邻链接之间的换行/Tab 被拼成一个演员。
                    # 对旧版页面中没有链接的纯文本结构保留逗号/顿号拆分。
                    if not self.actors:
                        for name in re.split(r"[,、]", value):
                            name = re.sub(r"\s+", " ", name).strip()
                            if name and name not in self.actors:
                                self.actors.append(name)
                elif self._spec_key == "配信日":
                    self.release = value
                elif self._spec_key == "再生時間":
                    self.runtime = value
                elif self._spec_key == "シリーズ":
                    self.series = value
                self._in_spec_content = False
                self._spec_key = ""
                self._spec_text = []

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._capture_text.append(data)
        if self._in_spec_content:
            self._spec_text.append(data)
            if self._actor_link_depth:
                self._actor_link_text.append(data)


def _trailer_urls(html: str) -> list[str]:
    match = re.search(r"\bvar\s+Movie\s*=\s*(\{.*?\})\s*;", html, re.DOTALL)
    if not match:
        return []
    try:
        movie = json.loads(match.group(1))
    except json.JSONDecodeError:
        return []
    urls: list[str] = []
    for key in ("sample_flash_url", "sample_m_flash_url", "sample_aff_flash_url"):
        value = movie.get(key)
        if isinstance(value, str) and value.startswith(("https://", "http://")) and value not in urls:
            urls.append(value)
    return urls


def _detail(html: str, page_url: str) -> tuple[dict[str, object], list[str]]:
    parser = _DetailParser()
    parser.feed(html)
    metadata: dict[str, object] = {
        "title": parser.title or None,
        "plot": parser.plot or None,
        "actors": parser.actors,
        "release": parser.release.replace("/", "-") or None,
        "runtime": _runtime_minutes(parser.runtime),
        "series": parser.series or None,
        "tags": parser.tags,
    }
    gallery = [urljoin(page_url, item) for item in parser.gallery]
    return metadata, gallery


def _runtime_minutes(value: str) -> int | None:
    match = re.fullmatch(r"\s*(\d{1,2}):(\d{2}):(\d{2})\s*", value)
    if not match:
        return None
    hours, minutes, seconds = map(int, match.groups())
    return hours * 60 + minutes + (1 if seconds >= 30 else 0)


class CaribbeancomCrawler(Crawler):
    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(name=SiteName.CARIBBEANCOM, base_url=_BASE_URL)

    def _detail_url(self, number: str) -> str | None:
        exact = _number(number)
        return f"{self.base_url}/moviepages/{exact}/index.html" if exact else None

    async def check_connectivity(self) -> ConnectivityOutcome:
        """探测实际刮削使用的详情页, 避免首页年龄验证页造成假阳性/假阴性."""
        url = f"{self.base_url}/moviepages/{_CONNECTIVITY_NUMBER}/index.html"
        return await probe_get(
            self.client.web_client,
            url,
            cookies=self.cookies,
            headers={**self.headers, "Accept-Language": "ja,en;q=0.8"},
        )

    async def _search(self, query: SearchQuery, options: FetchOptions | None = None) -> str | None:
        del options
        return self._detail_url(query.number)

    async def _scrape(self, url: str, options: FetchOptions | None = None) -> MediaMetadata | None:
        del options
        match = re.search(r"/moviepages/([^/]+)/index\.html(?:[?#].*)?$", url)
        requested = _number(match.group(1)) if match else None
        if not requested:
            return None
        html = await self.client.get_html(url, headers={"Accept-Language": "ja,en;q=0.8"}, encoding="euc_jp")
        metadata, extrafanart = _detail(html, url)
        # This is the site's dedicated video-player poster, not a frame captured from the trailer.
        poster = f"{self.base_url}/moviepages/{requested}/images/l_l.jpg"
        return MediaMetadata(
            number=requested,
            studio="カリビアンコム",
            publisher="Caribbeancom",
            title=metadata["title"] if isinstance(metadata["title"], str) else None,
            plot=metadata["plot"] if isinstance(metadata["plot"], str) else None,
            actors=film_actors(metadata["actors"] if isinstance(metadata["actors"], list) else []),
            release=metadata["release"] if isinstance(metadata["release"], str) else None,
            runtime=metadata["runtime"] if isinstance(metadata["runtime"], int) else None,
            series=metadata["series"] if isinstance(metadata["series"], str) else None,
            tags=metadata["tags"] if isinstance(metadata["tags"], list) else [],
            poster_urls=[poster],
            cover_urls=[poster],
            extrafanart=extrafanart,
            trailer_urls=_trailer_urls(html),
            source_url=url,
            external_id=requested,
        )
