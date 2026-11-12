"""LitePan STRM 播放 URL 中的 115 原生文件 ID 解析。"""

from __future__ import annotations

import base64
import binascii
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .threads import in_thread

_PLAY_PATH = re.compile(r"(?:^|/)api/strm/play/([A-Za-z0-9_-]+)(?:/|$)")


def parse_litepan_115_file_id(content: str) -> str | None:
    """仅解析 LitePan `/api/strm/play/{base64url(file_id)}` 路径段。

    `/t/` 媒体访问 Key 和 URL 查询签名均不参与身份提取。
    """
    for line in content.splitlines():
        value = line.strip()
        if not value:
            continue
        try:
            path = unquote(urlsplit(value).path)
        except ValueError:
            continue
        match = _PLAY_PATH.search(path)
        if match is None:
            continue
        token = match.group(1)
        try:
            decoded = base64.b64decode(token + "=" * (-len(token) % 4), altchars=b"-_", validate=True).decode("ascii")
        except binascii.Error, UnicodeDecodeError, ValueError:
            continue
        if decoded.isascii() and decoded.isdigit():
            return decoded
    return None


@in_thread
def read_litepan_115_file_id(path: Path) -> str | None:
    """读取 STRM 文本并提取 LitePan 的 115 文件 ID；不可读时忽略。"""
    if path.suffix.casefold() != ".strm":
        return None
    try:
        return parse_litepan_115_file_id(path.read_text(encoding="utf-8-sig"))
    except OSError, UnicodeError:
        return None
