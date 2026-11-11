"""演员原名到显示名的映射词典.

与 ``tags/mapping.py`` 同一取向: 独立 JSON 存在数据目录, 不随源码升级丢失, 也不碰数据库.
映射只影响「怎么显示」; 搜索、去重、刮削的比对一律仍用原始名, 否则同一演员换个显示名就
会被当成另一个人.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class ActorNameEntry:
    original: str
    """原始名; 作为键, 与索引里的 actor.name 完全一致."""

    translation: str | None = None
    """显示用名; 为空表示未映射."""


class ActorNameMappingStore:
    """原始名 → 显示名. 读写都在锁内, 保存走临时文件替换."""

    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir / "actor_name_mappings.json"
        self._lock = threading.RLock()
        self._entries: dict[str, ActorNameEntry] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            for key, value in (raw.get("entries") or {}).items():
                if isinstance(value, dict):
                    self._entries[key] = ActorNameEntry(
                        original=key,
                        translation=(value.get("translation") or None),
                    )
        except OSError, ValueError, TypeError:
            # 损坏文件不覆盖: 保持服务可用, 后续保存时用当前可解析的内容重写.
            self._entries = {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "entries": {k: asdict(v) for k, v in sorted(self._entries.items())}}
        fd, name = tempfile.mkstemp(prefix="actor-names-", suffix=".json", dir=self.path.parent)
        temp_path = Path(name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            temp_path.replace(self.path)
        finally:
            if temp_path.exists():
                temp_path.unlink()

    def display(self, original: str) -> str:
        """显示名; 未映射时回落到原始名."""
        with self._lock:
            entry = self._entries.get(original)
            return entry.translation if entry and entry.translation else original

    def display_map(self, originals: list[str]) -> dict[str, str]:
        """批量取显示名, 只返回有映射的项."""
        with self._lock:
            result: dict[str, str] = {}
            for name in originals:
                entry = self._entries.get(name)
                if entry and entry.translation:
                    result[name] = entry.translation
            return result

    def list(self, *, search: str = "") -> list[ActorNameEntry]:
        with self._lock:
            values = list(self._entries.values())
            if search:
                needle = search.casefold()
                values = [
                    v for v in values if needle in v.original.casefold() or needle in (v.translation or "").casefold()
                ]
            values.sort(key=lambda v: v.original.casefold())
            return values

    def counts(self) -> dict[str, int]:
        with self._lock:
            total = len(self._entries)
            mapped = sum(1 for entry in self._entries.values() if entry.translation)
            return {"all": total, "mapped": mapped, "pending": total - mapped}

    def upsert(self, original: str, translation: str | None) -> ActorNameEntry:
        original = original.strip()
        if not original:
            raise ValueError("演员原名不能为空")
        with self._lock:
            entry = self._entries.get(original) or ActorNameEntry(original)
            entry.translation = translation.strip() if translation and translation.strip() else None
            self._entries[original] = entry
            self._save()
            return entry

    def delete(self, original: str) -> bool:
        with self._lock:
            if original not in self._entries:
                return False
            del self._entries[original]
            self._save()
            return True

    def export(self) -> dict:
        with self._lock:
            return {"version": 1, "entries": {k: asdict(v) for k, v in sorted(self._entries.items())}}

    def import_data(self, payload: dict) -> int:
        entries = payload.get("entries")
        if not isinstance(entries, dict):
            raise ValueError("导入文件格式不正确")
        count = 0
        with self._lock:
            for original, value in entries.items():
                if not isinstance(original, str) or not isinstance(value, dict):
                    continue
                current = self._entries.get(original) or ActorNameEntry(original)
                translation = value.get("translation")
                current.translation = (
                    translation.strip() if isinstance(translation, str) and translation.strip() else None
                )
                self._entries[original] = current
                count += 1
            self._save()
        return count
