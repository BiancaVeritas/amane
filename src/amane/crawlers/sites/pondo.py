"""1pondo JSON detail API crawler with public gallery extraction."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin

from ...enums import SiteName
from ..base import Crawler, CrawlerProfile
from ..models import FetchOptions, MediaMetadata, SearchQuery, film_actors

_BASE_URL = "https://www.1pondo.tv"
_DETAIL_PATH = "/dyn/phpauto/movie_details/movie_id/{}.json"


def _number(value: str) -> str | None:
    # Preserve the supplied identifier byte-for-byte: separators, case and
    # digit count are significant and must never be normalized.
    return value if re.fullmatch(r"[0-9]{6}_[0-9]{3}", value) else None


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            if isinstance(item, str) and item.strip():
                result.append(item.strip())
            elif isinstance(item, dict):
                for key in ("NameJa", "NameEn", "Name", "name"):
                    candidate = item.get(key)
                    if isinstance(candidate, str) and candidate.strip():
                        result.append(candidate.strip())
        return result
    return []


class _PublicGalleryParser(HTMLParser):
    """Only accept gallery images carrying the site's public click-through attributes."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "img":
            return
        values = dict(attrs)
        classes = set((values.get("class") or "").split())
        src = values.get("data-vue-img-src")
        if "gallery-image" not in classes or "pointer" not in classes or not src:
            return
        if src not in self.urls:
            self.urls.append(src)


def _public_gallery(html: str, page_url: str) -> list[str]:
    parser = _PublicGalleryParser()
    parser.feed(html)
    return [urljoin(page_url, item) for item in parser.urls]


def _sample_trailers(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    urls: list[str] = []
    for item in value:
        if isinstance(item, str):
            candidate = item.strip()
        elif isinstance(item, dict):
            candidate = next(
                (
                    item.get(k)
                    for k in ("URL", "Url", "url", "File", "file", "Src", "src")
                    if isinstance(item.get(k), str)
                ),
                "",
            )
        else:
            continue
        if candidate and candidate not in urls:
            urls.append(candidate)
    return urls


class PondoCrawler(Crawler):
    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(name=SiteName.PONDO, base_url=_BASE_URL)

    def _detail_url(self, number: str) -> str | None:
        normalized = _number(number)
        return f"{self.base_url}{_DETAIL_PATH.format(normalized)}" if normalized else None

    async def _search(self, query: SearchQuery, options: FetchOptions | None = None) -> str | None:
        del options
        return self._detail_url(query.number)

    async def _scrape(self, url: str, options: FetchOptions | None = None) -> MediaMetadata | None:
        del options
        data = await self.client.get_json(url, headers={"Accept": "application/json"})
        if not isinstance(data, dict):
            return None
        raw_number = data.get("MovieID")
        match = re.search(r"/movie_id/([^/]+)\.json$", url)
        requested_number = _number(match.group(1)) if match else None
        number = _number(raw_number) if isinstance(raw_number, str) else None
        if not requested_number or number != requested_number:
            return None
        title = data.get("Title") or data.get("TitleEn")
        plot = data.get("Desc") or data.get("DescEn")
        actors = (
            _strings(data.get("ActressesList"))
            or _strings(data.get("ActressesJa"))
            or _strings(data.get("ActressesEn"))
            or _strings(data.get("Actor"))
        )
        release = data.get("Release")
        if not isinstance(release, str) or not release.strip():
            year = data.get("Year")
            release = f"{year}-01-01" if isinstance(year, (int, str)) and str(year).isdigit() else None
        duration = data.get("Duration")
        runtime = int(round(duration / 60)) if isinstance(duration, (int, float)) and duration > 0 else None
        poster = next(
            (
                data.get(key)
                for key in ("ThumbUltra", "ThumbHigh", "ThumbMed", "ThumbLow")
                if isinstance(data.get(key), str) and data.get(key).strip()
            ),
            None,
        )
        page_url = f"{self.base_url}/movies/{number}/"
        html = await self.client.get_html(page_url)
        extrafanart = _public_gallery(html, page_url)
        tags = list(dict.fromkeys(_strings(data.get("UCNAME")) + _strings(data.get("UCNAMEEn"))))
        series = data.get("Series") if isinstance(data.get("Series"), str) else None
        if not series and isinstance(data.get("SeriesEn"), str):
            series = data.get("SeriesEn")
        return MediaMetadata(
            number=number,
            studio="一本道",
            publisher="1pondo",
            title=title if isinstance(title, str) and title.strip() else None,
            plot=plot if isinstance(plot, str) and plot.strip() else None,
            actors=film_actors(actors),
            release=release,
            runtime=runtime,
            series=series,
            tags=tags,
            poster_urls=[poster] if poster else [],
            thumb_urls=[poster] if poster else [],
            extrafanart=extrafanart,
            trailer_urls=_sample_trailers(data.get("SampleFiles")),
            source_url=page_url,
            external_id=number,
        )
