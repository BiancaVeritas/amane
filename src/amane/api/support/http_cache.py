"""资源图 HTTP 缓存头.

一律 ``public, no-cache`` + ETag 协商.

文件名是 URL 的哈希, 内容变了地址也不变: 强刮削会就地覆盖同一张封面, 派生件 (裁切/超分)
的键也只含「源 + 参数」. 用 ``max-age`` 长缓存时浏览器会一直拿旧图不放, 必须手动强刷才能
看到新内容; ``no-cache`` 只是要求每次复用前向服务器确认, 未变时回 304, 速度与直取缓存相当.
"""

from fastapi import Request, Response

RESOURCE_CACHE_HEADERS = {"Cache-Control": "public, no-cache"}


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
