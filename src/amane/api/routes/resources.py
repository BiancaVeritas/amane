"""本地文件按 url hash 直出 (无选源/裁剪/超分).
按超分开关区分浏览器缓存 URL: 关闭时原图缓存 30 天, 开启时就地覆盖仍走 ETag 验证.
"""

from pathlib import Path
from typing import Annotated
from urllib.parse import urlparse

import structlog
from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse

from ...crawlers import actor_registry, registry
from ...net.egress import EgressPolicy
from ...net.errors import EgressRejected, RequestError
from ..deps import RuntimeDep
from ..support.http_cache import (
    ORIGINAL_RESOURCE_CACHE_HEADERS,
    RESOURCE_CACHE_HEADERS,
    format_etag,
    maybe_not_modified,
)
from ..support.media_type import media_type_and_headers

logger = structlog.get_logger()

router = APIRouter(prefix="/resources", tags=["resources"])

_PROXY_EGRESS = EgressPolicy()
_PROXY_MAX_BYTES = 32 * 1024 * 1024


def _image_response(request: Request, path: Path, content_hash: str | None, cache_mode: str) -> Response:
    media_type, safe_headers = media_type_and_headers(path)
    if cache_mode == "original":
        headers = {**safe_headers, **ORIGINAL_RESOURCE_CACHE_HEADERS}
        if content_hash:
            headers["ETag"] = format_etag(content_hash)
        return FileResponse(path=path, media_type=media_type, headers=headers)
    not_modified = maybe_not_modified(request, content_hash)
    if not_modified is not None:
        not_modified.headers.update({"Content-Type": media_type, **safe_headers})
        return not_modified
    headers = {**safe_headers, **RESOURCE_CACHE_HEADERS}
    if content_hash:
        headers["ETag"] = format_etag(content_hash)
    return FileResponse(path=path, media_type=media_type, headers=headers)


def _cache_mode(runtime: object, requested: str | None) -> str:
    if requested in {"original", "sr"}:
        return requested
    config = getattr(runtime, "config", None)
    hot = getattr(config, "hot", None)
    sr = getattr(hot, "sr", None)
    return "sr" if bool(getattr(sr, "enabled", False)) else "original"


@router.get("/proxy")
async def proxy_image(
    runtime: RuntimeDep,
    request: Request,
    url: Annotated[str, Query(description="External image URL to proxy")],
    mode: Annotated[str | None, Query(description="Browser cache mode")] = None,
    source: Annotated[str | None, Query(description="Owning scraper source")] = None,
) -> Response:
    """上游失败记入进程内负缓存 (15 分钟), 窗口内同 URL 直接 502.
    同 URL + 来源的进行中请求合并为一次 acquire.
    使用图片 token 的请求只从缓存读取资源: 未命中返回 404, 不发起对外请求, 不写入负缓存.
    """
    try:
        parsed = urlparse(url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="URL 无法解析") from exc
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise HTTPException(status_code=400, detail="仅支持 http/https URL")

    cache_mode = _cache_mode(runtime, mode)
    store = runtime.resource_store
    # 缓存命中无需访问上游, 因此不对既有资源重新做公网地址判定.
    cached = await store.resolve_record(url)
    if cached is not None:
        record, local_path = cached
        return _image_response(request, local_path, record.content_hash, cache_mode)
    if request.state.via_image_token:
        raise HTTPException(status_code=404, detail="资源不在本地缓存")

    try:
        await _PROXY_EGRESS.ensure_allowed(url)
    except EgressRejected as exc:
        logger.warning("proxy image address rejected", url=url, reason=exc.message)
        raise HTTPException(status_code=400, detail=exc.detail) from exc
    except RequestError as exc:
        logger.warning("proxy image host unreachable", url=url, reason=exc.message)
        raise HTTPException(status_code=502, detail="上游图片获取失败") from exc

    # 来源必须是已知刮削源，避免客户端借来源参数任意选择代理。
    if source is not None:
        allowed = {str(site) for site in registry.sites()} | {str(site) for site in actor_registry.sites()}
        allowed.update(str(key) for key in runtime.config.hot.scraping.site_config)
        if runtime.plugin_manager is not None:
            allowed.update(descriptor.id for descriptor in runtime.plugin_manager.descriptors())
        if source not in allowed:
            raise HTTPException(status_code=400, detail="未知图片来源")

    # 负缓存命中则直接 502
    failures = runtime.proxy_failure_cache
    failure_key = f"{source}:{url}" if source is not None else url
    if failures.is_blocked(failure_key):
        raise HTTPException(status_code=502, detail="上游图片获取失败")

    # 同 URL + 来源的进行中 acquire 合并为一次, 避免不同来源串用代理策略.
    try:
        found = await failures.coalesce(
            failure_key,
            lambda: store.acquire_with_record(
                url, runtime.web_client, source=source, egress=_PROXY_EGRESS, max_bytes=_PROXY_MAX_BYTES
            ),
        )
    except EgressRejected as exc:
        logger.warning("proxy image address rejected after DNS recheck", url=url, reason=exc.message)
        raise HTTPException(status_code=400, detail=exc.detail) from exc
    except RequestError as exc:
        logger.warning("proxy image upstream request failed", url=url, reason=exc.message)
        raise HTTPException(status_code=502, detail="上游图片获取失败") from exc
    if found is None:
        failures.remember(failure_key)
        logger.warning("proxy image acquire failed", url=url)
        raise HTTPException(status_code=502, detail="上游图片获取失败")

    # 成功后清除负缓存
    failures.forget(failure_key)
    record, local_path = found
    return _image_response(request, local_path, record.content_hash, cache_mode)


@router.get("/{url_hash}")
async def serve_resource(
    url_hash: str,
    request: Request,
    runtime: RuntimeDep,
    mode: Annotated[str | None, Query(description="Browser cache mode")] = None,
) -> Response:
    found = await runtime.resource_store.get_by_url_hash(url_hash)
    if found is None:
        raise HTTPException(status_code=404, detail="资源不存在")
    record, path = found

    cache_mode = _cache_mode(runtime, mode)
    media_type, safe_headers = media_type_and_headers(path)
    if cache_mode == "original":
        headers = dict(ORIGINAL_RESOURCE_CACHE_HEADERS)
    else:
        not_modified = maybe_not_modified(request, record.content_hash)
        if not_modified is not None:
            not_modified.headers.update({"Content-Type": media_type, **safe_headers})
            return not_modified
        headers = dict(RESOURCE_CACHE_HEADERS)
        if record.content_hash:
            headers["ETag"] = format_etag(record.content_hash)
    headers.update(safe_headers)
    return FileResponse(path=path, media_type=media_type, headers=headers)
