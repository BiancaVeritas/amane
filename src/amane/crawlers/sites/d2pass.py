"""D2PASS HEYZO 离线映射刮削源。只通过映射进入商品详情页，不调用 JAVBUS。"""

from __future__ import annotations

import json
import re
from pathlib import Path

import structlog

from ...enums import SiteName
from ..base import Crawler, CrawlerProfile
from ..http import RequestError
from ..models import FetchOptions, MediaMetadata, SearchQuery, film_actors

logger = structlog.get_logger(__name__)
_BASE_URL = "https://www.d2pass.com"
_DETAIL_PATH = "/product/movies/{}"
_IMAGE_URL = "https://images.d2pass.com/images/meta/movies/flash/{}.webp"
_INDEX_PATH = Path(__file__).with_name("d2pass_heyzo_index.json")
_index: dict[str, dict[str, object]] | None = None
_NUMBER_RE = re.compile(r"^HEYZO[-_ ]?(?P<digits>\d+)$", re.IGNORECASE)
_PRODUCT_START = '"product":{"product_id":'
_BLOCK_SPAN = 4000


def _load_index() -> dict[str, dict[str, object]]:
    global _index
    if _index is not None:
        return _index
    try:
        raw = json.loads(_INDEX_PATH.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
        logger.warning("D2PASS HEYZO 映射索引不可用", path=str(_INDEX_PATH))
        _index = {}
        return _index
    _index = {str(k): v for k, v in raw.items() if isinstance(v, dict)} if isinstance(raw, dict) else {}
    return _index


def _product_id(number: str) -> int | None:
    match = _NUMBER_RE.fullmatch(number.strip())
    if not match:
        return None
    key = match.group("digits").lstrip("0") or "0"
    entry = _load_index().get(key.zfill(4))
    value = entry.get("id") if entry else None
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _field(block: str, name: str) -> str | None:
    match = re.search(rf'"{re.escape(name)}":\s*"((?:[^"\\]|\\.)*)"', block)
    if not match:
        return None
    try:
        return json.loads(f'"{match.group(1)}"')
    except json.JSONDecodeError:
        return match.group(1)


def _number(block: str, name: str) -> int | None:
    match = re.search(rf'"{re.escape(name)}":\s*(\d+)', block)
    return int(match.group(1)) if match else None


def _block(html: str) -> str | None:
    text = html.replace('\\"', '"')
    start = text.find(_PRODUCT_START)
    return text[start : start + _BLOCK_SPAN] if start >= 0 else None


class D2PassCrawler(Crawler):
    def __init__(self, client, config=None):
        super().__init__(client, config)
        self._pages: dict[str, str] = {}
        self._numbers: dict[str, str] = {}

    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(name=SiteName.D2PASS, base_url=_BASE_URL)

    async def _search(self, query: SearchQuery, options: FetchOptions | None = None) -> str | None:
        del options
        self._pages.clear()
        self._numbers.clear()
        pid = _product_id(query.number)
        if pid is None:
            return None
        url = f"{self.base_url.rstrip('/')}{_DETAIL_PATH.format(pid)}"
        try:
            html = await self.client.get_html(url)
        except RequestError as exc:
            self.logger.warning("D2PASS 映射商品页访问失败", number=query.number, product_id=pid, error=str(exc))
            return None
        block = _block(html)
        if not block or not _field(block, "title"):
            self.logger.warning("D2PASS 商品页缺少有效作品数据", number=query.number, product_id=pid)
            return None
        self._pages[url] = html
        self._numbers[url] = query.number
        return url

    async def _scrape(self, url: str, options: FetchOptions | None = None) -> MediaMetadata | None:
        del options
        html = self._pages.pop(url, None)
        number = self._numbers.pop(url, None)
        if html is None or number is None:
            try:
                html = await self.client.get_html(url)
            except RequestError as exc:
                self.logger.warning("D2PASS 商品页读取失败", url=url, error=str(exc))
                return None
        if number is None:
            self.logger.warning("D2PASS 商品页缺少原始番号缓存，跳过刮削", url=url)
            return None
        block = _block(html)
        if not block:
            return None
        title = _field(block, "title")
        if not title:
            return None
        product_id = _number(block, "product_id")
        actor = _field(block, "actor_name")
        site = _field(block, "site_name")
        release = (_field(block, "release_date") or "")[:10] or None
        duration = _number(block, "duration")
        tags = [item.strip() for item in (_field(block, "uc_list") or "").split(":") if item.strip()]
        image = [_IMAGE_URL.format(product_id)] if product_id else []
        return MediaMetadata(
            number=number,
            title=title,
            plot=_field(block, "description"),
            actors=film_actors([actor] if actor else []),
            release=release,
            runtime=round(duration / 60) if duration else None,
            studio=site,
            publisher=site,
            tags=tags,
            poster_urls=image,
            cover_urls=list(image),
            source_url=url,
            external_id=str(product_id) if product_id else None,
        )
