"""D2PASS 商品库爬虫 —— 用于 HEYZO 一类官网已关停的片商.

D2PASS 聚合多片商的商品条目, 每个条目带 ``product_url``, 指向片商原始页并含番号
(``heyzo.com/moviepages/0333/index.html``). 因此它的价值是: 拿番号反查完整元数据.

站点搜索只认标题与演员名, 不认番号, 所以先用 javbus 取标题, 再用标题搜本站,
最后用 ``product_url`` 里的番号复核 —— 同系列不同分卷的标题往往只差续集序号,
不复核会串。

离线索引 ``heyzo_index.json`` 把该链路的结果固化下来 (番号 → product_id), 命中时
直接访问详情页, 省掉 javbus 与搜索两次往返; 未命中才退回在线查找。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from ...enums import SiteName
from ..base import Crawler, CrawlerProfile
from ..http import HttpClient, RequestError
from ..models import FetchOptions, MediaMetadata, SearchQuery, film_actors
from .javbus import JavBusCrawler

if TYPE_CHECKING:
    from ...config import SiteConfig

logger = structlog.get_logger(__name__)

_BASE_URL = "https://www.d2pass.com"
_SEARCH_PATH = "/search?k={}"
_DETAIL_PATH = "/product/movies/{}"
_IMAGE_URL = "https://images.d2pass.com/images/meta/movies/flash/{}.webp"

#: 番号 → product_id 的离线索引. 由 amane-work/heyzo_map 的采集脚本生成.
_INDEX_PATH = Path(__file__).with_name("d2pass_heyzo_index.json")
_index: dict[str, dict[str, object]] | None = None


def _load_index() -> dict[str, dict[str, object]]:
    global _index
    if _index is not None:
        return _index
    try:
        raw = json.loads(_INDEX_PATH.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
        logger.warning("heyzo index unavailable", path=str(_INDEX_PATH))
        _index = {}
        return _index
    _index = {str(k): v for k, v in raw.items() if isinstance(v, dict)}
    return _index


def _index_pid(number: str) -> int | None:
    """按番号查离线索引. 索引键统一 4 位补零, 番号数字段也照此补齐."""
    digits = _digits(number)
    if digits is None:
        return None
    pid = _load_index().get(digits.zfill(4), {}).get("id")
    return int(pid) if isinstance(pid, int) else None


#: 只认官网已关停、别处查不到的片商. 本站每次刮削要 javbus + 搜索 + 若干详情页,
#: 对别的站能正常供给的番号做这些请求纯属浪费, 而且容易触发风控; 前缀不符直接返回.
_NUMBER_PREFIX = re.compile(r"^(?P<prefix>HEYZO)[-_ ]?(?P<id>\d+)$", re.IGNORECASE)
_D2PASS_PREFIXES = frozenset({"HEYZO"})

#: 片商页 URL 里的番号段: ``/moviepages/0333/index.html`` → ``0333``.
_PRODUCT_NUMBER_RE = re.compile(r"/moviepages/([0-9A-Za-z_-]+)/", re.IGNORECASE)
#: 搜索结果里的商品链接.
_RESULT_LINK_RE = re.compile(r'href="(/product/movies/\d+)"')
#: ``product`` 数据块的起点 (Next.js 流式片段里的转义 JSON).
_PRODUCT_START = '"product":{"product_id":'
#: 单个 ``product`` 对象的取值窗口; 对象内没有再嵌套的 ``product`` 键.
_BLOCK_SPAN = 4000


def _field(block: str, name: str) -> str | None:
    """取字符串字段; 片段里的 JSON 是转义过的, 用 json 还原转义序列."""
    m = re.search(rf'"{re.escape(name)}":\s*"((?:[^"\\]|\\.)*)"', block)
    if not m:
        return None
    try:
        return json.loads(f'"{m.group(1)}"')
    except json.JSONDecodeError:
        return m.group(1)


def _number(block: str, name: str) -> int | None:
    m = re.search(rf'"{re.escape(name)}":\s*(\d+)', block)
    return int(m.group(1)) if m else None


def _block(html: str) -> str | None:
    text = html.replace('\\"', '"')
    start = text.find(_PRODUCT_START)
    return text[start : start + _BLOCK_SPAN] if start >= 0 else None


def _catalog_number(product_url: str | None) -> str | None:
    if not product_url:
        return None
    m = _PRODUCT_NUMBER_RE.search(product_url)
    return m.group(1) if m else None


def _bare(digits: str) -> str:
    """纯数字段归一到去掉前导零的形式, 两侧比对用 (``0333`` 与 ``333``)."""
    return digits.lstrip("0") or "0"


def _digits(value: str) -> str | None:
    """番号的数字段, 去掉前导零; 前缀不符返回 None."""
    if match := _NUMBER_PREFIX.match(value.strip()):
        return _bare(match["id"])
    return None


def _supported(number: str) -> bool:
    """只处理官网已关停的片商前缀; 其余番号本站帮不上忙, 也不必为它发请求."""
    match = _NUMBER_PREFIX.match(number.strip())
    return match is not None and match["prefix"].upper() in _D2PASS_PREFIXES


class D2PassCrawler(Crawler):
    def __init__(self, client: HttpClient, config: SiteConfig | None = None):
        super().__init__(client, config)
        self._pages: dict[str, str] = {}

    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(name=SiteName.D2PASS, base_url=_BASE_URL)

    async def _javbus_title(self, number: str) -> str | None:
        javbus = JavBusCrawler(self.client)
        try:
            detail_url = await javbus._search_number(number, number)
            if not detail_url:
                return None
            text = await self.client.get_html(detail_url, cookies=javbus.cookies, headers=javbus.headers)
        except RequestError as exc:
            self.logger.warning("JAVBUS未找到原始标题", number=number, error=str(exc))
            return None
        m = re.search(r"<h3[^>]*>(.*?)</h3>", text, re.DOTALL)
        if not m:
            return None
        return re.sub(r"<[^>]+>", "", m.group(1)).strip() or None

    async def _search(self, query: SearchQuery, options: FetchOptions | None = None) -> str | None:
        del options
        self._pages = {}
        if not _supported(query.number):
            # 其余片商由各自的专站供给, 本站不为它们发任何请求.
            return None

        # 离线索引优先: 命中时省掉 javbus 与站点搜索两次往返, 直接进详情页.
        if (pid := _index_pid(query.number)) is not None:
            detail_url = f"{self.base_url.rstrip('/')}{_DETAIL_PATH.format(pid)}"
            html = await self.client.get_html(detail_url)
            block = _block(html)
            catalog = _catalog_number(_field(block, "product_url")) if block else None
            if catalog is not None and _bare(catalog) == _digits(query.number):
                self._pages[detail_url] = html
                return detail_url
            self.logger.warning("离线索引番号不符, 退回在线查找", number=query.number, product_id=pid)

        target = _digits(query.number)
        title = await self._javbus_title(query.number)
        if not title:
            return None

        for term in _search_terms(title):
            page = await self.client.get_html(f"{self.base_url.rstrip('/')}{_SEARCH_PATH.format(term)}")
            for href in dict.fromkeys(_RESULT_LINK_RE.findall(page)):
                detail_url = f"{self.base_url.rstrip('/')}{href}"
                html = await self.client.get_html(detail_url)
                block = _block(html)
                if block is None:
                    continue
                catalog = _catalog_number(_field(block, "product_url"))
                if catalog is None or _bare(catalog) != target:
                    continue
                self._pages[detail_url] = html
                return detail_url
        return None

    async def _scrape(self, url: str, options: FetchOptions | None = None) -> MediaMetadata | None:
        del options
        html = self._pages.pop(url, None)
        if html is None:
            html = await self.client.get_html(url)
        block = _block(html)
        if block is None:
            return None

        title = _field(block, "title")
        number = _catalog_number(_field(block, "product_url"))
        if not title or not number:
            return None
        product_id = _number(block, "product_id")
        actor = _field(block, "actor_name")
        site = _field(block, "site_name")
        release = (_field(block, "release_date") or "")[:10] or None
        duration = _number(block, "duration")
        tags = [t.strip() for t in (_field(block, "uc_list") or "").split(":") if t.strip()]
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
            thumb_urls=list(image),
            source_url=url,
            external_id=str(product_id) if product_id else None,
        )


def _search_terms(title: str) -> list[str]:
    """搜索词候选: 整段标题优先, 再退到其中较长的片段.

    站点只按标题与演员名做索引, 番号无效; 标题过长时整段可能筛掉全部结果,
    因此补上分段尝试.
    """
    text = " ".join(title.split())
    terms = [text]
    for part in text.split(" "):
        if len(part) >= 4 and part not in terms:
            terms.append(part)
    return terms
