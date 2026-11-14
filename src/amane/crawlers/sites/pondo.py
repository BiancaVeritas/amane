"""1pondo JSON detail API crawler with public gallery extraction."""

from __future__ import annotations

import re
from typing import Any

from ...enums import SiteName
from ..base import Crawler, CrawlerProfile
from ..models import FetchOptions, MediaMetadata, SearchQuery, film_actors

_BASE_URL = "https://www.1pondo.tv"
_DETAIL_PATH = "/dyn/phpauto/movie_details/movie_id/{}.json"
_GALLERY_PATH = "/dyn/phpauto/movie_galleries/movie_id/{}.json"
#: 剧照在站点的 sample 目录下按站点再分一层, 1pondo 用 ``popu`` (paco 用 ``l``).
_GALLERY_DIR = "popu"
_GALLERY_IMAGE_PATH = "/assets/sample/{}/{}/{}"


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
        try:
            gallery = await self.client.get_json(
                f"{self.base_url}{_GALLERY_PATH.format(number)}",
                headers={"Accept": "application/json", "Referer": page_url},
            )
        except Exception as e:
            # 剧照是补充信息: 接口不可用时不拖累其余字段.
            self.logger.warning("剧照接口不可用", number=number, error=str(e))
            extrafanart: list[str] = []
        else:
            extrafanart = _public_gallery(gallery, number, self.base_url, _GALLERY_DIR)
        # 只取日文标签: UCNAMEEn 是同一批标签的英文写法, 并进来会让界面上日文夹英文.
        tags = list(dict.fromkeys(_strings(data.get("UCNAME"))))
        series = data.get("Series") if isinstance(data.get("Series"), str) else None
        if not series and isinstance(data.get("SeriesEn"), str):
            series = data.get("SeriesEn")
        return MediaMetadata(
            number=number,
            studio="一本道",
            publisher="一本道",
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
