"""Pacopacomama JSON detail API crawler with public gallery extraction."""
from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import urljoin

from ...enums import SiteName
from ..base import Crawler, CrawlerProfile
from ..models import FetchOptions, MediaMetadata, SearchQuery, film_actors

_BASE_URL = "https://www.pacopacomama.com"
_DETAIL_PATH = "/dyn/phpauto/movie_details/movie_id/{}.json"
_NUMBER_RE = re.compile(r"[0-9]{6}_[0-9]{3}")


def _number(value: str) -> str | None:
    """Accept only the exact six-digit, underscore, three-digit identifier."""
    return value if _NUMBER_RE.fullmatch(value) else None


def _strings(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


class _PublicGalleryParser(HTMLParser):
    """Collect only full-size images exposed as public gallery click targets."""

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


class PacopacomamaCrawler(Crawler):
    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(name=SiteName.PACOPACOMAMA, base_url=_BASE_URL)

    def _detail_url(self, number: str) -> str | None:
        exact = _number(number)
        return f"{self.base_url}{_DETAIL_PATH.format(exact)}" if exact else None

    async def _search(self, query: SearchQuery, options: FetchOptions | None = None) -> str | None:
        del options
        return self._detail_url(query.number)

    async def _scrape(self, url: str, options: FetchOptions | None = None) -> MediaMetadata | None:
        del options
        match = re.search(r"/movie_id/([^/]+)\.json$", url)
        requested_number = _number(match.group(1)) if match else None
        if not requested_number:
            return None

        data = await self.client.get_json(url, headers={"Accept": "application/json"})
        if not isinstance(data, dict) or data.get("MovieID") != requested_number:
            return None

        title = data.get("Title")
        plot = data.get("Desc")
        release = data.get("Release")
        duration = data.get("Duration")
        runtime = (
            int(round(duration / 60))
            if isinstance(duration, (int, float)) and not isinstance(duration, bool) and duration > 0
            else None
        )
        poster = next(
            (
                data.get(key)
                for key in ("ThumbUltra", "ThumbHigh", "ThumbMed", "ThumbLow")
                if isinstance(data.get(key), str) and data.get(key).strip()
            ),
            None,
        )
        page_url = f"{self.base_url}/movies/{requested_number}/"
        html = await self.client.get_html(page_url)
        extrafanart = _public_gallery(html, page_url)
        tags = list(dict.fromkeys(_strings(data.get("UCNAME"))))
        series = data.get("Series")
        actors = _strings(data.get("ActressesJa"))

        return MediaMetadata(
            number=requested_number,
            studio="パコパコママ",
            publisher="Pacopacomama",
            title=title.strip() if isinstance(title, str) and title.strip() else None,
            plot=plot.strip() if isinstance(plot, str) and plot.strip() else None,
            actors=film_actors(actors),
            release=release.strip() if isinstance(release, str) and release.strip() else None,
            runtime=runtime,
            series=series.strip() if isinstance(series, str) and series.strip() else None,
            tags=tags,
            poster_urls=[poster] if poster else [],
            thumb_urls=[poster] if poster else [],
            extrafanart=extrafanart,
            source_url=page_url,
            external_id=requested_number,
        )
