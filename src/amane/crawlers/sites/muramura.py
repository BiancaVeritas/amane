"""Muramura JSON detail API crawler.

与 1pondo 同属一套后端 (``/dyn/phpauto/movie_details``), 字段结构一致, 因此解析逻辑
直接复用 ``pondo`` 模块的辅助函数, 这里只覆盖站点相关的几处: 域名、厂商名与剧照目录.
"""

from __future__ import annotations

import re

from ...enums import SiteName
from ..base import Crawler, CrawlerProfile
from ..models import FetchOptions, MediaMetadata, SearchQuery, film_actors
from .pondo import _GALLERY_PATH, _public_gallery, _sample_trailers, _strings

_BASE_URL = "https://www.muramura.tv"
_DETAIL_PATH = "/dyn/phpauto/movie_details/movie_id/{}.json"
#: 剧照目录与 1pondo 不同, 按站点实际路径填.
_GALLERY_DIR = "popu"

#: muramura 的序号比 1pondo 多一位 (``110826_1274``), 不能直接复用 pondo 的 6+3 校验.
_NUMBER_RE = re.compile(r"[0-9]{6}_[0-9]{3,4}")


def _muramura_number(value: str | None) -> str | None:
    """本站番号: 6 位日期 + 下划线 + 3~4 位序号, 原样保留不作规范化."""
    if not isinstance(value, str):
        return None
    return value if _NUMBER_RE.fullmatch(value) else None


class MuramuraCrawler(Crawler):
    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(name=SiteName.MURAMURA, base_url=_BASE_URL)

    def _detail_url(self, number: str) -> str | None:
        normalized = _muramura_number(number)
        return f"{self.base_url}{_DETAIL_PATH.format(normalized)}" if normalized else None

    async def scrape_url(self, url: str) -> MediaMetadata | None:
        """页面地址换成本站详情 JSON 再解析: ``_scrape`` 只认 JSON URL."""
        match = re.search(r"/movies/([^/]+)/?$", url)
        number = _muramura_number(match.group(1)) if match else None
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
        requested_number = _muramura_number(match.group(1)) if match else None
        number = _muramura_number(raw_number) if isinstance(raw_number, str) else None
        # 站点对不存在的番号也返回 200 + 一条别的记录, 因此必须核对番号.
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
            studio="ムラムラってくる素人",
            publisher="ムラムラってくる素人",
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


__all__ = ["MuramuraCrawler"]
