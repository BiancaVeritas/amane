"""Tushy / Tushy Raw performer-list avatar sources."""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any, override
from urllib.parse import urlencode, urljoin

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
            urls=[f"https://www.{cls.host}/search"],
            capabilities=frozenset({SourceCapability.ACTOR_IMAGE}),
            genders=frozenset({ActorGender.FEMALE}),
        )

    async def fetch(self, name: str) -> ActorMetadata | None:
        """搜索页只取 models 中完整姓名匹配的演员; 不扫描 performers 目录或打开详情页."""
        wanted = _name_key(name)
        if not wanted:
            return None
        search_url = f"{self.base_url}/search?{urlencode({'q': name})}"
        text = await self.client.get_html(search_url, cookies=self.cookies, headers=self.headers)
        models = _models_from_html(text)
        for model in models:
            if not isinstance(model, dict) or _name_key(str(model.get("name") or "")) != wanted:
                continue
            slug = model.get("slug")
            if not isinstance(slug, str) or not slug.strip():
                return None
            image = _listing_image(model, self.base_url)
            return ActorMetadata(
                name=str(model.get("name") or name),
                image_urls=[image] if image else [],
                provider_ids={str(self.name): str(slug)},
                source_url=f"{self.base_url}/performers/{slug.strip('/')}",
            )
        return None

    @override
    async def check_connectivity(self) -> ConnectivityOutcome:
        # 检测实际列表入口；年龄验证页保留 AGE_VERIFICATION，不伪装为成功。
        return await probe_get(
            self.client.web_client,
            self.base_url,
            cookies=self.cookies,
            headers=self.headers,
        )

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


def _models_from_html(text: str) -> list[dict[str, Any]]:
    match = re.search(r"<script[^>]+id=[\"']__NEXT_DATA__[\"'][^>]*>(.*?)</script>", text, re.DOTALL | re.IGNORECASE)
    if not match:
        return []
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError, TypeError:
        return []
    props = payload.get("props", {}).get("pageProps", {}) if isinstance(payload, dict) else {}
    models = props.get("models", []) if isinstance(props, dict) else []
    return models if isinstance(models, list) else []


def _listing_image(model: dict[str, Any], base_url: str) -> str | None:
    """取演员卡片真实展示图; 排除站点的通用女性占位头像."""
    images = model.get("images")
    listing = images.get("listing") if isinstance(images, dict) else None
    if not isinstance(listing, list):
        return None
    for item in listing:
        if not isinstance(item, dict):
            continue
        webp = item.get("webp")
        highdpi = webp.get("highdpi") if isinstance(webp, dict) else None
        double = highdpi.get("double") if isinstance(highdpi, dict) else None
        candidates = [double, item.get("src")]
        for url in candidates:
            if not isinstance(url, str) or not url.strip():
                continue
            lowered = url.casefold()
            if "female-fallback-portrait" in lowered or lowered.endswith(".svg"):
                continue
            return urljoin(base_url.rstrip("/") + "/", url)
    return None


def _listing_webp(model: dict[str, Any]) -> str | None:
    """兼容旧测试辅助函数; 返回真实高分辨率 WebP."""
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
        if isinstance(url, str) and "_620x1060.webp" in url and "female-fallback-portrait" not in url:
            return url
    return None
