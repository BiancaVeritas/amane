"""按映射词典取显示名.

落盘格式见 ``tags/mapping.py``; 这里只给「从目录读某一组」这一条路径, 供 NFO / 路径模板 /
媒体服务器推送这些不在 API 进程内共享店对象的调用方使用.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog

from .mapping import MAPPING_FILES, MappingGroup, VendorRole

if TYPE_CHECKING:
    from pathlib import Path

    from ..db.models import Metadata

logger = structlog.get_logger()


def load_translations(mapping_dir: Path | None, group: MappingGroup) -> dict[str, str]:
    """读某一组的 原名 → 中文名; 目录为空或文件缺失 / 损坏时返回空表, 调用方回退原名."""
    if mapping_dir is None:
        return {}
    path = mapping_dir / MAPPING_FILES[group]
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError, ValueError:
        logger.warning(
            "mapping dictionary unreadable; falling back to original names", path=str(path), group=str(group)
        )
        return {}
    entries = raw.get("entries")
    if not isinstance(entries, dict):
        return {}
    names: dict[str, str] = {}
    for original, value in entries.items():
        if isinstance(original, str) and isinstance(value, dict):
            translation = value.get("translation")
            if isinstance(translation, str) and translation.strip():
                names[original] = translation.strip()
    return names


def load_vendor_translations(mapping_dir: Path | None) -> dict[VendorRole, dict[str, str]]:
    """片商合表按分类拆成三份快照.

    一张表里同一行可以身兼制作商与发行商; 该行没有 ``roles`` 时算手动条目, 对三类都生效.
    """
    roots: dict[VendorRole, dict[str, str]] = {role: {} for role in VendorRole}
    if mapping_dir is None:
        return roots
    path = mapping_dir / MAPPING_FILES[MappingGroup.VENDOR]
    if not path.is_file():
        return roots
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError, ValueError:
        logger.warning("vendor mapping dictionary unreadable; falling back to original names", path=str(path))
        return roots
    entries = raw.get("entries")
    if not isinstance(entries, dict):
        return roots
    for original, value in entries.items():
        if not isinstance(original, str) or not isinstance(value, dict):
            continue
        translation = value.get("translation")
        if not isinstance(translation, str) or not translation.strip():
            continue
        roles = [str(role) for role in value.get("roles") or []]
        for role in VendorRole:
            if not roles or role in roles:
                roots[role][original] = translation.strip()
    return roots


@dataclass(frozen=True, slots=True)
class VendorNames:
    """影片的片商三件套 (原名或展示名); 缺发行商时它就是制作商, 系列不受这条规则影响."""

    studio: str | None
    publisher: str | None
    series: list[str]

    def translated(self, roots: dict[VendorRole, dict[str, str]]) -> VendorNames:
        """换成展示值; 没有译名的保持原名."""
        return VendorNames(
            studio=_show(roots[VendorRole.STUDIO], self.studio),
            publisher=_show(roots[VendorRole.PUBLISHER], self.publisher),
            series=[_show(roots[VendorRole.SERIES], name) or name for name in self.series],
        )


def _show(roots: dict[str, str], name: str | None) -> str | None:
    return roots.get(name, name) if name else None


def vendor_roots(mapping_dir: Path | None) -> dict[VendorRole, dict[str, str]]:
    """从目录读片商组的映射表."""
    return load_vendor_translations(mapping_dir)


def vendor_names(metadata: Metadata) -> VendorNames:
    """影片的片商三件套原名; 没有发行商时按制作商补上.

    发行商空着而制作商有值, 是刮削源的常见形态 (只给了 maker 没给 label).
    两者在片商分类里本就是同一层概念, 留空会让 NFO 与详情页少一个维度.
    """
    publisher = metadata.publisher or metadata.studio or None
    series = list(dict.fromkeys(name for name in (metadata.series_names or []) if name))
    if not series and metadata.series:
        series = [metadata.series]
    return VendorNames(studio=metadata.studio or None, publisher=publisher, series=series)
