"""用户上传的封面/海报图片落地.

上传的图与刮削下载的 Resource 分开存放: 前者是用户自己的文件, 需要独立目录便于备份与
清理, 且不应被 Resource 的 LRU 回收牵连. 但两者的读取通路相同 —— 仍然登记一条
``Resource`` 记录, 让现有的图片端点、缓存与超分逻辑照常工作. 为此 ``file_path`` 指到
``../uploads/...``, ``ResourceStore.full_path`` 会按相对路径解析到 ``data_dir/uploads``.

**不覆盖原图**: 上传只往 ``poster_urls`` / ``thumb_urls`` 头部插入新地址, 原有条目保留,
用户随时可以切回.
"""

from __future__ import annotations

import hashlib
import mimetypes
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ..db.models import Resource
from .resource_store import ResourceStore

#: 单文件上限. 海报原图很少超过这个量级, 再大基本是误传.
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
#: 允许的图片类型: 三者覆盖了常见来源, 且都能被 Pillow 直接解码.
ALLOWED_MIME = frozenset({"image/jpeg", "image/png", "image/webp"})

_MIME_TO_EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}


class UploadRejected(ValueError):
    """上传内容不合法 (类型/大小). 由路由翻译成 400."""


@dataclass(frozen=True, slots=True)
class StoredUpload:
    """一次上传的结果: 对内可供派生裁剪, 对外是图片地址."""

    url: str
    """内部 locator, 形如 ``upload:{sha256}:{kind}``."""

    path: Path


def uploads_dir(store: ResourceStore) -> Path:
    return store.data_dir / "uploads"


def _sniff_mime(data: bytes) -> str | None:
    """按文件头判类型, 不信任客户端给的 Content-Type."""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def validate(data: bytes) -> str:
    """校验并返回 MIME; 不合法时抛 ``UploadRejected``."""
    if not data:
        raise UploadRejected("文件为空")
    if len(data) > MAX_UPLOAD_BYTES:
        raise UploadRejected(f"文件超过 {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
    mime = _sniff_mime(data)
    if mime is None or mime not in ALLOWED_MIME:
        raise UploadRejected("仅支持 JPG / PNG / WEBP")
    return mime


async def store_upload(store: ResourceStore, data: bytes, *, kind: str) -> StoredUpload:
    """把字节写进 ``data_dir/uploads`` 并登记 Resource.

    ``kind`` 只用于文件名可读性 (``thumb`` / ``poster``); 内容哈希相同则复用同一文件,
    重复上传同一张图不会堆垃圾.
    """
    mime = validate(data)
    digest = hashlib.sha256(data).hexdigest()
    locator = f"upload:{digest}:{kind}"

    existing = await store.get_by_url(locator)
    if existing is not None:
        path = store.full_path(existing)
        if path.exists():
            return StoredUpload(url=locator, path=path)

    directory = uploads_dir(store)
    directory.mkdir(parents=True, exist_ok=True)
    suffix = _MIME_TO_EXT[mime]
    path = directory / f"{kind}_{digest[:16]}{suffix}"
    path.write_bytes(data)

    # file_path 相对 resources 目录; ../uploads 指回 data_dir/uploads.
    relative = f"../uploads/{path.name}"
    record = Resource(
        url=locator,
        file_path=relative,
        content_hash=digest,
        size=len(data),
        mime_type=mimetypes.guess_type(path.name)[0] or mime,
        meta={"op": "upload", "kind": kind, "uploaded_at": datetime.now(UTC).isoformat()},
    )
    await store.register(record)
    return StoredUpload(url=locator, path=path)
