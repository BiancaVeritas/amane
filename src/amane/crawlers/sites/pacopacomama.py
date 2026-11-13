"""Pacopacomama JSON detail API crawler with public gallery extraction."""

from __future__ import annotations

import re
from typing import Any

from ...enums import SiteName
from ..base import Crawler, CrawlerProfile
from ..models import FetchOptions, MediaMetadata, SearchQuery, film_actors

_BASE_URL = "https://www.pacopacomama.com"
_DETAIL_PATH = "/dyn/phpauto/movie_details/movie_id/{}.json"
_GALLERY_PATH = "/dyn/phpauto/movie_galleries/movie_id/{}.json"
#: 剧照在站点的 sample 目录下按站点再分一层, paco 用 ``l`` (1pondo 用 ``popu``).
_GALLERY_DIR = "l"
_GALLERY_IMAGE_PATH = "/assets/sample/{}/{}/{}"
_NUMBER_RE = re.compile(r"[0-9]{6}_[0-9]{3}")


def _number(value: str) -> str | None:
    """Accept only the exact six-digit, underscore, three-digit identifier."""
    return value if _NUMBER_RE.fullmatch(value) else None


def _strings(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _public_gallery(data: Any, number: str, base_url: str, gallery_dir: str) -> list[str]:
    """公开剧照地址: 站点页面不渲染这段, 只能由剧照接口的标记拼出.

    受保护的文件需要登录才能取, 直接拼地址会得到 404; 站点把两者混在同一列表里, 只能按标记筛.
    """
    if not isinstance(data, dict):
        return []
    rows = data.get("Rows")
    if not isinstance(rows, list):
        return []
    urls: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("Protected") is not False:
            continue
        # 列表可能混入别的条目, 只认本次番号.
        if row.get("MovieID") != number:
            continue
        filename = row.get("Filename")
        if not isinstance(filename, str) or not filename.strip():
            continue
        url = f"{base_url}{_GALLERY_IMAGE_PATH.format(number, gallery_dir, filename.strip())}"
        if url not in urls:
            urls.append(url)
    return urls


class PacopacomamaCrawler(Crawler):
    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(name=SiteName.PACOPACOMAMA, base_url=_BASE_URL)

    def _detail_url(self, number: str) -> str | None:
        exact = _number(number)
        return f"{self.base_url}{_DETAIL_PATH.format(exact)}" if exact else None

    async def scrape_url(self, url: str) -> MediaMetadata | None:
        """页面地址换成本站详情 JSON 再解析: ``_scrape`` 只认 JSON URL."""
        match = re.search(r"/movies/([^/]+)/?$", url)
        number = _number(match.group(1)) if match else None
        detail = self._detail_url(number) if number else None
        return await self._scrape(detail) if detail else None

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
        try:
            gallery = await self.client.get_json(
                f"{self.base_url}{_GALLERY_PATH.format(requested_number)}",
                headers={"Accept": "application/json", "Referer": page_url},
            )
        except Exception as e:
            # 剧照是补充信息: 接口不可用时不拖累其余字段.
            self.logger.warning("剧照接口不可用", number=requested_number, error=str(e))
            extrafanart: list[str] = []
        else:
            extrafanart = _public_gallery(gallery, requested_number, self.base_url, _GALLERY_DIR)
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
