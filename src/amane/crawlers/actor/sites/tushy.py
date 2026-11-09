"""Tushy / Tushy Raw performer-list avatar sources."""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any, override
from urllib.parse import urlencode

from amane.enums import ActorGender, SiteName
from amane.net.connectivity import ConnectivityOutcome, probe_get
from amane.plugins.models import SourceCapability

from ...base import CrawlerProfile
from ..base import ActorCrawler
from ..models import ActorMetadata


class _TushyActorCrawler(ActorCrawler):
    host: str
    site_name: SiteName
    display_name: str
    ordered: bool = False

    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(
            name=cls.site_name,
            base_url=f"https://www.{cls.host}",
            urls=[f"https://www.{cls.host}/performers"],
            capabilities=frozenset({SourceCapability.ACTOR_IMAGE}),
            genders=frozenset({ActorGender.FEMALE}),
        )

    async def fetch(self, name: str) -> ActorMetadata | None:
        wanted = _name_key(name)
        if not wanted:
            return None
        page = 1
        while True:
            url = self._listing_url(page)
            text = await self.client.get_html(url, cookies=self.cookies, headers=self.headers)
            models, page_num, total_count = _listing_page_from_html(text)
            if not models:
                return None
            for model in models:
                if not isinstance(model, dict) or _name_key(str(model.get("name") or "")) != wanted:
                    continue
                slug = model.get("slug")
                if not isinstance(slug, str) or not slug.strip():
                    return None
                image = _listing_webp(model)
                if not image:
                    return None
                return ActorMetadata(
                    name=str(model.get("name") or name),
                    image_urls=[image],
                    provider_ids={str(self.name): str(slug)},
                    source_url=f"https://www.{self.host}/performers/{slug.strip('/')}",
                )
            # 列表响应提供当前页与总条数；用本页实际条数推导末页，避免无界翻页。
            # 缺少分页元数据时不猜测下一页，防止站点改版后持续请求。
            if page_num is None or total_count is None or total_count <= 0:
                return None
            if page_num * len(models) >= total_count:
                return None
            page += 1

    @override
    async def check_connectivity(self) -> ConnectivityOutcome:
        # 检测实际列表入口；年龄验证页保留 AGE_VERIFICATION，不伪装为成功。
        return await probe_get(
            self.client.web_client,
            self._listing_url(1),
            cookies=self.cookies,
            headers=self.headers,
        )

    def _listing_url(self, page: int) -> str:
        query = {"order": "name", "page": page} if self.ordered else {"page": page}
        return f"https://www.{self.host}/performers?{urlencode(query)}"

    async def _search(self, name: str) -> str | None:
        raise NotImplementedError

    async def _scrape(self, url: str) -> ActorMetadata | None:
        raise NotImplementedError


class TushyActorCrawler(_TushyActorCrawler):
    host = "tushy.com"
    site_name = SiteName.TUSHY
    display_name = "Tushy"
    ordered = True


class TushyRawActorCrawler(_TushyActorCrawler):
    host = "tushyraw.com"
    site_name = SiteName.TUSHY_RAW
    display_name = "Tushy Raw"


def _name_key(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip().casefold()


def _listing_page_from_html(text: str) -> tuple[list[dict[str, Any]], int | None, int | None]:
    match = re.search(r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', text, re.DOTALL | re.IGNORECASE)
    if not match:
        return [], None, None
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError, TypeError:
        return [], None, None
    props = payload.get("props", {}).get("pageProps", {}) if isinstance(payload, dict) else {}
    if not isinstance(props, dict):
        return [], None, None
    models = props.get("models", [])
    if not isinstance(models, list):
        models = []
    page_num = props.get("pageNum")
    total_count = props.get("totalCount")
    page_num = page_num if isinstance(page_num, int) and page_num > 0 else None
    total_count = total_count if isinstance(total_count, int) and total_count >= 0 else None
    return models, page_num, total_count


def _models_from_html(text: str) -> list[dict[str, Any]]:
    """兼容测试与调用方只需要演员列表的场景。"""
    return _listing_page_from_html(text)[0]


def _listing_webp(model: dict[str, Any]) -> str | None:
    images = model.get("images")
    listing = images.get("listing") if isinstance(images, dict) else None
    if not isinstance(listing, list):
        return None
    for item in listing:
        if not isinstance(item, dict):
            continue
        webp = item.get("webp")
        highdpi = webp.get("highdpi") if isinstance(webp, dict) else None
        url = highdpi.get("double") if isinstance(highdpi, dict) else None
        if isinstance(url, str) and "_620x1060.webp" in url:
            return url
    return None
