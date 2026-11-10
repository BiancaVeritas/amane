"""本地文件按 url hash 直出 (无选源/裁剪/超分).
按超分开关区分浏览器缓存 URL: 关闭时原图缓存 30 天, 开启时就地覆盖仍走 ETag 验证.
"""

from pathlib import Path
from typing import Annotated
from urllib.parse import urlparse

import structlog
from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse

from ..deps import RuntimeDep
from ..support.http_cache import (
    RESOURCE_CACHE_HEADERS,
    format_etag,
    maybe_not_modified,
)
from ..support.media_type import media_type_and_headers

logger = structlog.get_logger()

router = APIRouter(prefix="/resources", tags=["resources"])

def _image_response(request: Request, path: Path, content_hash: str | None, cache_mode: str | None = None) -> Response:
    # 一律 ETag 协商: 文件名是 URL 的哈希, 覆盖同一地址后浏览器若不回源就会一直显示旧图.
    _ = cache_mode
    not_modified = maybe_not_modified(request, content_hash)
    if not_modified is not None:
        return not_modified
    media_type, headers = media_type_and_headers(path)
    headers.update(RESOURCE_CACHE_HEADERS)
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
) -> Response:
    """上游失败记入进程内负缓存 (15 分钟), 窗口内同 URL 直接 502.
    同 URL 进行中请求合并为一次 acquire.
    使用图片 token 的请求只从缓存读取资源: 未命中返回 404, 不发起对外请求, 不写入负缓存.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise HTTPException(status_code=400, detail="仅支持 http/https URL")

    cache_mode = _cache_mode(runtime, mode)
    store = runtime.resource_store
    if request.state.via_image_token:
        found = await store.resolve_record(url)
        if found is None:
            raise HTTPException(status_code=404, detail="资源不在本地缓存")
        record, local_path = found
        return _image_response(request, local_path, record.content_hash, cache_mode)

    # 负缓存命中则直接 502
    failures = runtime.proxy_failure_cache
    if failures.is_blocked(url):
        raise HTTPException(status_code=502, detail="上游图片获取失败")

    # 同 URL 进行中的 acquire 合并为一次
    found = await failures.coalesce(url, lambda: store.acquire_with_record(url, runtime.web_client))
    if found is None:
        failures.remember(url)
        logger.warning("proxy image acquire failed", url=url)
        raise HTTPException(status_code=502, detail="上游图片获取失败")

    # 成功后清除负缓存
    failures.forget(url)
    record, local_path = found
    return _image_response(request, local_path, record.content_hash, cache_mode)


@router.get("/{url_hash}")
async def serve_resource(
    url_hash: str,
    request: Request,
    runtime: RuntimeDep,
    mode: Annotated[str | None, Query(description="Browser cache mode")] = None,
) -> Response:
    store = runtime.resource_store
    found = await store.get_by_url_hash(url_hash)
    if found is None:
        # 记录还在但文件丢了: 派生件能按 meta 里的源与参数重跑一次, 比退回低优先级来源更准.
        # 原始件无法重建 (源图本身丢了), 直接 404.
        stale = await store.get_record_by_url_hash(url_hash)
        if stale is not None:
            rebuilt = await store.rebuild_derived(stale)
            if rebuilt is not None:
                # rebuild_derived 返回 (记录, 路径); 但 acquire_derived 只给记录, 这里统一成二元组.
                found = rebuilt
                logger.info("derived resource rebuilt", url_hash=url_hash, url=rebuilt[0].url)
        if found is None:
            raise HTTPException(status_code=404, detail="资源不存在")
    record, path = found

    # 原始件与派生件都走 ETag 协商: 文件名是 URL 的哈希, 内容变了地址也不变 —— 强刮削会
    # 就地覆盖同一张封面. 长缓存会让浏览器拿着旧图不放, 必须手动强刷才看得到新内容.
    _ = _cache_mode(runtime, mode)
    not_modified = maybe_not_modified(request, record.content_hash)
    if not_modified is not None:
        return not_modified
    # 资源字节来自第三方, 记录里的 mime 不可信: 按文件头判定, 判不出就按附件下载.
    media_type, headers = media_type_and_headers(path)
    headers.update(RESOURCE_CACHE_HEADERS)
    if record.content_hash:
        headers["ETag"] = format_etag(record.content_hash)
    return FileResponse(path=path, media_type=media_type, headers=headers)
