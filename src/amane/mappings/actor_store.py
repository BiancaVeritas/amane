"""Persistent Minnano actress ID/name mappings."""

from __future__ import annotations

import builtins
import csv
import io
import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any


class ActorMappingStore:
    def __init__(self, data_dir: Path, seed_path: Path | None = None) -> None:
        self.directory = data_dir / "mappings"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.file = self.directory / "minnano_actress_map.json"
        self._lock = threading.RLock()
        self._lookup_stamp: int | None = None
        self._lookup_index: dict[str, builtins.list[str]] = {}
        seed_available = seed_path is not None and seed_path.is_file()
        if not self.file.exists():
            seed = self._read_seed(seed_path) if seed_path is not None and seed_path.is_file() else {}
            self._atomic_write(self.file, seed)
        # Older builds created an empty local map before bundling the initial data.
        # Seed that one-time empty state, but never overwrite a non-empty user map.
        marker = self.directory / ".minnano_actress_map_seeded"
        if seed_available and not marker.exists():
            try:
                current = json.loads(self.file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError(f"演员映射文件读取失败：{exc}") from exc
            if not isinstance(current, dict):
                raise ValueError("演员映射文件必须是 JSON 对象")
            if not current and seed_path is not None and seed_path.is_file():
                seed = self._read_seed(seed_path)
                if seed:
                    self._atomic_write(self.file, seed)
            marker.write_text("1\n", encoding="utf-8")

    @staticmethod
    def _read_seed(path: Path) -> dict[str, dict[str, Any]]:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"演员映射初始文件读取失败：{exc}") from exc
        if not isinstance(raw, dict):
            raise ValueError("演员映射文件必须是 JSON 对象")
        return {str(k): ActorMappingStore._validate(str(k), v) for k, v in raw.items()}

    @staticmethod
    def _validate(identifier: str, value: Any) -> dict[str, Any]:
        if not identifier.isdigit() or len(identifier) > 12:
            raise ValueError(f"ID 必须是最多 12 位的数字：{identifier}")
        if not isinstance(value, dict) or set(value) - {"name", "aliases"}:
            raise ValueError(f"{identifier}: 只允许 name 和 aliases 字段")
        name = value.get("name")
        aliases = value.get("aliases", [])
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"{identifier}: 标准姓名不能为空")
        if not isinstance(aliases, list) or any(not isinstance(a, str) for a in aliases):
            raise ValueError(f"{identifier}: aliases 必须是字符串数组")
        clean: builtins.list[str] = []
        for alias in aliases:
            alias = alias.strip()
            if alias and alias != name.strip() and alias not in clean:
                clean.append(alias)
        return {"name": name.strip(), "aliases": clean}

    @staticmethod
    def _atomic_write(path: Path, data: Any) -> None:
        fd, temporary = tempfile.mkstemp(prefix=f"{path.stem}-", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(data, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            Path(temporary).replace(path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    @property
    def exceptions_file(self) -> Path:
        return self.directory / "minnano_actress_map_exceptions.json"

    def list_conflicts(self) -> builtins.list[dict[str, Any]]:
        with self._lock:
            if not self.exceptions_file.exists():
                return []
            try:
                items = json.loads(self.exceptions_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError(f"演员异常映射文件读取失败：{exc}") from exc
            if not isinstance(items, list):
                raise ValueError("演员异常映射文件必须是 JSON 数组")
            return items

    def _write_conflicts(self, items: builtins.list[dict[str, Any]]) -> None:
        compact = []
        for index, item in enumerate(items, 1):
            row = dict(item)
            row["group_id"] = index
            compact.append(row)
        self._atomic_write(self.exceptions_file, compact)

    def add_conflicts(self, items: builtins.list[dict[str, Any]]) -> builtins.list[dict[str, Any]]:
        with self._lock:
            current = self.list_conflicts()
            current.extend(items)
            self._write_conflicts(current)
            return self.list_conflicts()

    def update_conflict(self, group_id: int, side: str, entry: dict[str, Any] | None) -> builtins.list[dict[str, Any]]:
        if side not in {"old", "new"}:
            raise ValueError("异常记录侧别无效")
        with self._lock:
            items = self.list_conflicts()
            if group_id < 1 or group_id > len(items):
                raise ValueError("异常组不存在")
            if entry is None:
                items[group_id - 1].pop(side, None)
                if not items[group_id - 1].get("old") and not items[group_id - 1].get("new"):
                    items.pop(group_id - 1)
            else:
                items[group_id - 1][side] = entry
            self._write_conflicts(items)
            return self.list_conflicts()

    def save_conflict_entry(
        self,
        group_id: int,
        side: str,
        identifier: str,
        value: dict[str, Any],
    ) -> builtins.list[dict[str, Any]]:
        if side not in {"old", "new"}:
            raise ValueError("异常记录侧别无效")
        with self._lock:
            items = self.list_conflicts()
            if group_id < 1 or group_id > len(items):
                raise ValueError("异常组不存在")
            self.save(identifier, value, check_names=True)
            return self.update_conflict(group_id, side, None)

    def clear_conflicts(self) -> None:
        with self._lock:
            self._write_conflicts([])

    def list(self, search: str = "") -> dict[str, dict[str, Any]]:
        with self._lock:
            try:
                data = json.loads(self.file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError(f"演员映射文件读取失败：{exc}") from exc
            if not isinstance(data, dict):
                raise ValueError("演员映射文件必须是 JSON 对象")
            if not search:
                return data
            needle = search.strip()
            return {key: item for key, item in data.items() if item.get("name", "") == needle}

    def find_ids_by_name(self, name: str) -> builtins.list[str]:
        """Return IDs whose standard name exactly matches; aliases are never indexed."""
        needle = name.strip()
        if not needle:
            return []
        with self._lock:
            stamp = self.file.stat().st_mtime_ns
            if stamp != self._lookup_stamp:
                index: dict[str, builtins.list[str]] = {}
                for identifier, item in self.list().items():
                    index.setdefault(item["name"], []).append(identifier)
                self._lookup_index = index
                self._lookup_stamp = stamp
            return list(self._lookup_index.get(needle, []))

    def find_id(self, name: str) -> str | None:
        """Compatibility helper: return the sole exact standard-name match only."""
        matches = self.find_ids_by_name(name)
        return matches[0] if len(matches) == 1 else None

    def save(
        self,
        identifier: str,
        value: dict[str, Any],
        original_id: str | None = None,
        *,
        check_names: bool = True,
    ) -> None:
        item = self._validate(identifier, value)
        with self._lock:
            data = self.list()
            if original_id is not None and original_id not in data:
                raise ValueError(f"待编辑的演员 ID 不存在：{original_id}")
            if identifier in data and identifier != original_id:
                raise ValueError(f"ID 已存在：{data[identifier]['name']}")
            if check_names:
                for other_id, other in data.items():
                    if other_id != original_id and other["name"] == item["name"]:
                        raise ValueError(f"标准姓名重复：{other['name']}")
            if original_id and original_id != identifier:
                data.pop(original_id, None)
            data[identifier] = item
            data = dict(sorted(data.items(), key=lambda pair: int(pair[0])))
            self._atomic_write(self.file, data)

    def delete(self, identifier: str) -> bool:
        with self._lock:
            data = self.list()
            if identifier not in data:
                return False
            del data[identifier]
            data = dict(sorted(data.items(), key=lambda pair: int(pair[0])))
            self._atomic_write(self.file, data)
            return True

    def delete_many(self, identifiers: builtins.list[str]) -> int:
        with self._lock:
            data = self.list()
            count = 0
            for identifier in dict.fromkeys(identifiers):
                if identifier in data:
                    del data[identifier]
                    count += 1
            if count:
                data = dict(sorted(data.items(), key=lambda pair: int(pair[0])))
                self._atomic_write(self.file, data)
            return count

    def import_merge(self, incoming: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(incoming, dict):
            raise ValueError("导入内容必须是 JSON 对象")
        return self.import_merge_rows(list(incoming.items()))

    def import_merge_rows(self, rows: builtins.list[tuple[str, Any]]) -> dict[str, Any]:
        """Add rows; quarantine both sides of ID or standard-name collisions."""
        normalized = [(str(identifier), self._validate(str(identifier), value)) for identifier, value in rows]
        with self._lock:
            current = self.list()
            added: builtins.list[str] = []
            conflicts: builtins.list[dict[str, Any]] = []
            blocked: builtins.list[tuple[str, dict[str, Any]]] = []
            for identifier, value in normalized:
                candidate = {"id": identifier, **value}
                prior = next(
                    (
                        (old_id, old)
                        for old_id, old in current.items()
                        if old_id == identifier or old["name"] == value["name"]
                    ),
                    None,
                )
                if prior is None:
                    prior = next(
                        (
                            (old["id"], {k: v for k, v in old.items() if k != "id"})
                            for old_id, old in blocked
                            if old_id == identifier or old["name"] == value["name"]
                        ),
                        None,
                    )
                if prior is not None:
                    old_id, old_value = prior
                    reason = "id" if old_id == identifier else "name"
                    if old_id in current:
                        current.pop(old_id)
                        if old_id in added:
                            added.remove(old_id)
                    conflicts.append(
                        {
                            "group_id": len(conflicts) + 1,
                            "reason": reason,
                            "old": {"id": old_id, **old_value},
                            "new": candidate,
                        }
                    )
                    blocked.append((identifier, candidate))
                    continue
                current[identifier] = value
                added.append(identifier)
                blocked.append((identifier, candidate))
            current = dict(sorted(current.items(), key=lambda pair: int(pair[0])))
            self._atomic_write(self.file, current)
            if conflicts:
                self.add_conflicts(conflicts)
            return {"added": added, "conflicts": conflicts}

    def replace(self, incoming: dict[str, Any]) -> int:
        normalized = {str(k): self._validate(str(k), v) for k, v in incoming.items()}
        names: set[str] = set()
        for identifier, item in normalized.items():
            key = item["name"]
            if key in names:
                raise ValueError(f"{identifier}: 标准姓名重复：{item['name']}")
            names.add(key)
        with self._lock:
            normalized = dict(sorted(normalized.items(), key=lambda pair: int(pair[0])))
            self._atomic_write(self.file, normalized)
        return len(normalized)

    def export_data(self) -> dict[str, dict[str, Any]]:
        """Return only active actor mappings; exception-review records are excluded."""
        # The normal map is the sole export source. Never merge in list_conflicts().
        return self.list()

    def export_csv(self) -> str:
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(["ID", "标准姓名", "别名"])
        # CSV follows the same normal-list-only rule as JSON export.
        for identifier, item in self.export_data().items():
            writer.writerow([identifier, item["name"], "\n".join(item["aliases"])])
        return output.getvalue()
