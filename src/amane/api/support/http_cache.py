"""资源图 HTTP 缓存头.

SR 开启: URL 不变且文件就地覆盖, 用 ``public, no-cache`` + ETag 协商失效.
SR 关闭: 原图不变, 用 ``public, max-age=2592000`` 缓存 30 天.
"""

from fastapi import Request, Response

RESOURCE_CACHE_HEADERS = {"Cache-Control": "public, no-cache"}
ORIGINAL_RESOURCE_CACHE_HEADERS = {"Cache-Control": "public, max-age=2592000"}


def format_etag(content_hash: str) -> str:
    return f'"{content_hash}"'


def etag_matches(if_none_match: str | None, content_hash: str) -> bool:
    if not if_none_match or not content_hash:
        return False
    quoted = format_etag(content_hash)
    for part in if_none_match.split(","):
        candidate = part.strip()
        if candidate.startswith("W/"):
            candidate = candidate[2:].strip()
        if candidate == "*" or candidate == quoted:
            return True
    return False


def not_modified_response(content_hash: str) -> Response:
    return Response(status_code=304, headers={**RESOURCE_CACHE_HEADERS, "ETag": format_etag(content_hash)})


def maybe_not_modified(request: Request, content_hash: str | None) -> Response | None:
    if content_hash and etag_matches(request.headers.get("if-none-match"), content_hash):
        return not_modified_response(content_hash)
    return None
