"""Manage the three externally stored scraper number maps."""

from __future__ import annotations

import builtins
import json
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Any, Literal

Source = Literal["caribbeancompr", "d2pass", "caribbeancom"]
FILES: dict[Source, str] = {
    "caribbeancompr": "caribbeancompr_heyzo_index.json",
    "d2pass": "d2pass_heyzo_index.json",
    "caribbeancom": "caribbeancom_index.json",
}


class NumberMappingStore:
    def __init__(self, data_dir: Path, seed_dir: Path | None = None) -> None:
        self.directory = data_dir / "mappings"
        self.directory.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        for source, filename in FILES.items():
            path = self.directory / filename
            if path.exists():
                # The local mapping is authoritative after first initialization.
                # Never overwrite user-managed data during startup or upgrades.
                continue
            seed_path = seed_dir / filename if seed_dir is not None else None
            seed = self._read_seed(source, seed_path) if seed_path is not None and seed_path.is_file() else {}
            self._atomic_write(path, seed)

    @classmethod
    def _read_seed(cls, source: Source, path: Path) -> dict[str, dict[str, Any]]:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"番号映射初始文件读取失败：{path.name}: {exc}") from exc
        if not isinstance(raw, dict):
            raise ValueError(f"番号映射初始文件必须是 JSON 对象：{path.name}")
        return {key: cls._validate(source, key, value) for key, value in raw.items()}

    def path(self, source: Source) -> Path:
        return self.directory / FILES[source]

    @staticmethod
    def _validate(source: Source, key: str, value: Any) -> dict[str, Any]:
        if not isinstance(key, str) or not key:
            raise ValueError("番号键必须是非空字符串")
        if source in {"caribbeancompr", "d2pass"} and re.fullmatch(r"[0-9]{4}", key) is None:
            raise ValueError(f"{key}: HEYZO 映射键必须是四位数字，且不带 HEYZO 前缀")
        if not isinstance(value, dict) or set(value) - {"id", "title", "override_title", "actor"}:
            raise ValueError(f"{key}: 只允许 id、actor、title 和旧版 override_title 字段")
        identifier = value.get("id")
        if source == "d2pass":
            if type(identifier) is not int:
                raise ValueError(f"{key}: D2PASS 的 id 必须是整数")
        else:
            if not isinstance(identifier, str) or not identifier:
                raise ValueError(f"{key}: id 必须是非空字符串")

            if source == "caribbeancompr" and re.fullmatch(r"[0-9]{6}_[0-9]{3}", identifier) is None:
                raise ValueError(f"{key}: Caribbeancompr 的 id 必须符合 6位数字_3位数字 格式")

            if source == "caribbeancom" and re.fullmatch(r"[A-Za-z0-9_-]+", identifier) is None:
                raise ValueError(f"{key}: Caribbeancom Index 的 id 只能包含英文字母、数字、下划线和连字符")

        title = value.get("title")
        if title is not None and not isinstance(title, str):
            raise ValueError(f"{key}: title 必须是字符串或省略")
        legacy_override_title = value.get("override_title")
        if legacy_override_title is not None and not isinstance(legacy_override_title, str):
            raise ValueError(f"{key}: override_title 必须是字符串或省略")
        actor = value.get("actor")
        if actor is not None and not isinstance(actor, str):
            raise ValueError(f"{key}: actor 必须是字符串或省略")
        result: dict[str, Any] = {"id": identifier}
        if actor and actor.strip():
            result["actor"] = actor.strip()
        if title and title.strip():
            result["title"] = title.strip()
        # Keep legacy scraper override data intact; the mapping UI no longer edits it.
        if legacy_override_title and legacy_override_title.strip():
            result["override_title"] = legacy_override_title.strip()
        return result

    def _conflict_path(self, source: Source) -> Path:
        return self.directory / f"{source}_mapping_exceptions.json"

    def list_conflicts(self, source: Source) -> builtins.list[dict[str, Any]]:
        with self._lock:
            path = self._conflict_path(source)
            if not path.exists():
                return []
            try:
                items = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError(f"异常映射文件读取失败：{exc}") from exc
            if not isinstance(items, list):
                raise ValueError("异常映射文件必须是 JSON 数组")
            return items

    def _write_conflicts(self, source: Source, items: builtins.list[dict[str, Any]]) -> None:
        # Re-number only the visible groups; group identity and side records stay intact.
        compact = []
        for index, item in enumerate(items, 1):
            row = dict(item)
            row["group_id"] = index
            compact.append(row)
        self._atomic_write(self._conflict_path(source), compact)

    def add_conflicts(self, source: Source, items: builtins.list[dict[str, Any]]) -> builtins.list[dict[str, Any]]:
        with self._lock:
            current = self.list_conflicts(source)
            current.extend(items)
            self._write_conflicts(source, current)
            return self.list_conflicts(source)

    def update_conflict(
        self, source: Source, group_id: int, side: str, entry: dict[str, Any] | None
    ) -> builtins.list[dict[str, Any]]:
        if side not in {"old", "new"}:
            raise ValueError("异常记录侧别无效")
        with self._lock:
            items = self.list_conflicts(source)
            if group_id < 1 or group_id > len(items):
                raise ValueError("异常组不存在")
            if entry is None:
                items[group_id - 1].pop(side, None)
                if not items[group_id - 1].get("old") and not items[group_id - 1].get("new"):
                    items.pop(group_id - 1)
            else:
                items[group_id - 1][side] = entry
            self._write_conflicts(source, items)
            return self.list_conflicts(source)

    def clear_conflicts(self, source: Source) -> None:
        with self._lock:
            self._write_conflicts(source, [])

    def list(self, source: Source, search: str = "") -> dict[str, dict[str, Any]]:
        with self._lock:
            try:
                data = json.loads(self.path(source).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError(f"映射文件读取失败：{exc}") from exc
            if not isinstance(data, dict):
                raise ValueError("映射文件必须是 JSON 对象")
            if not search:
                return data
            needle = search.casefold()
            return {key: value for key, value in data.items() if needle in key.casefold()}

    @staticmethod
    def _atomic_write(path: Path, data: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
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

    def save(self, source: Source, key: str, value: dict[str, Any], original_key: str | None = None) -> None:
        item = self._validate(source, key, value)
        with self._lock:
            data = self.list(source)
            if original_key is not None and original_key not in data:
                raise ValueError(f"待编辑的原番号不存在：{original_key}")
            existing_at_key = data.get(key)
            if existing_at_key is not None and original_key is not None and key != original_key:
                raise ValueError(f"番号已存在：{key}")
            for other_key, other in data.items():
                if other_key not in {original_key, key} and other.get("id") == item["id"]:
                    raise ValueError(f"ID 已被番号 {other_key} 使用：{item['id']}")
            if original_key is not None and original_key != key:
                old = data.pop(original_key)
                if "override_title" not in item and "override_title" in old:
                    item["override_title"] = old["override_title"]
            elif original_key is None and key in data:
                # Explicit save is an upsert for the same number. Preserve the
                # scraper-only legacy override unless the caller supplied it.
                old = data[key]
                if "override_title" not in item and "override_title" in old:
                    item["override_title"] = old["override_title"]
            data[key] = item
            data = dict(sorted(data.items(), key=lambda pair: pair[0]))
            self._atomic_write(self.path(source), data)

    def delete(self, source: Source, key: str) -> bool:
        with self._lock:
            data = self.list(source)
            if key not in data:
                return False
            del data[key]
            data = dict(sorted(data.items(), key=lambda pair: pair[0]))
            self._atomic_write(self.path(source), data)
            return True

    def import_merge(self, source: Source, incoming: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(incoming, dict):
            raise ValueError("导入内容必须是 JSON 对象")
        return self.import_merge_rows(source, list(incoming.items()))

    def import_merge_rows(self, source: Source, rows: builtins.list[tuple[str, Any]]) -> dict[str, Any]:
        """Merge safe rows and quarantine number/ID collisions for review."""
        normalized = [(key, self._validate(source, key, value)) for key, value in rows]
        with self._lock:
            current = self.list(source)
            added: builtins.list[str] = []
            unchanged: builtins.list[str] = []
            conflicts: builtins.list[dict[str, Any]] = []
            accepted_this_batch: dict[str, dict[str, Any]] = {}
            for key, value in normalized:
                same_number = current.get(key)
                if same_number is not None:
                    if same_number.get("id") == value.get("id"):
                        # Mapping metadata may be refreshed by an import. The
                        # legacy scraper override remains independently owned.
                        merged = dict(same_number)
                        for field in ("actor", "title"):
                            if field in value:
                                merged[field] = value[field]
                            else:
                                merged.pop(field, None)
                        # override_title belongs to the local scraper override,
                        # not to imported mapping metadata. Keep the existing
                        # value even when an incoming row contains that legacy field.
                        current[key] = self._validate(source, key, merged)
                        unchanged.append(key)
                        continue
                    old_key, old = key, same_number
                    reason = "number"
                else:
                    matches = [(old_key, old) for old_key, old in current.items() if old.get("id") == value.get("id")]
                    matches += [
                        (old_key, old)
                        for old_key, old in accepted_this_batch.items()
                        if old.get("id") == value.get("id")
                    ]
                    if matches:
                        old_key, old = matches[0]
                        reason = "id"
                    else:
                        current[key] = value
                        accepted_this_batch[key] = value
                        added.append(key)
                        continue

                # Remove the existing side from active mappings and quarantine
                # both sides. Rows already accepted in this import are removed
                # from `added` and treated identically.
                # Keep the active old mapping intact until the user explicitly
                # resolves this conflict; only the incoming side is quarantined.
                accepted_this_batch.pop(old_key, None)
                conflicts.append(
                    {
                        "group_id": len(conflicts) + 1,
                        "key": key,
                        "old_key": old_key,
                        "reason": reason,
                        "old": {"key": old_key, **old},
                        "new": {"key": key, **value},
                    }
                )

            current = dict(sorted(current.items(), key=lambda pair: pair[0]))
            self._atomic_write(self.path(source), current)
            if conflicts:
                self.add_conflicts(source, conflicts)
            return {"added": added, "unchanged": unchanged, "conflicts": conflicts}

    def resolve(self, source: Source, decisions: builtins.list[dict[str, Any]]) -> int:
        """Apply explicitly selected import sides using optimistic ID checks."""
        changed = 0
        with self._lock:
            data = self.list(source)
            for decision in decisions:
                if not decision.get("use_import"):
                    continue
                key = decision.get("key")
                value = decision.get("value")
                old_key = decision.get("old_key")
                expected_old_id = decision.get("expected_old_id")
                if not isinstance(key, str) or not isinstance(value, dict):
                    continue
                source_key = old_key if isinstance(old_key, str) else key
                existing = data.get(source_key)
                # A missing old key means the mapping was deleted after the
                # conflict was recorded. Do not validate or recreate it.
                if existing is None or existing.get("id") != expected_old_id:
                    continue
                item = self._validate(source, key, value)
                if source_key != key and key in data:
                    continue
                if any(
                    other_key not in {source_key, key} and other.get("id") == item["id"]
                    for other_key, other in data.items()
                ):
                    continue
                if "override_title" not in item and "override_title" in existing:
                    item["override_title"] = existing["override_title"]
                data.pop(source_key, None)
                data[key] = item
                changed += 1
            if changed:
                self._atomic_write(self.path(source), dict(sorted(data.items(), key=lambda pair: pair[0])))
        return changed

    def export_data(self, source: Source) -> dict[str, dict[str, Any]]:
        """Return only active mappings for export; quarantined exceptions are never exported."""
        # Deliberately read the authoritative normal mapping file via list().
        # Do not combine this with list_conflicts(), which is review-only data.
        return self.list(source)

    def replace(self, source: Source, incoming: dict[str, Any]) -> int:
        normalized = {key: self._validate(source, key, value) for key, value in incoming.items()}
        with self._lock:
            current = self.list(source)
            # Full replacement replaces the mapping set, but does not silently
            # erase the scraper's legacy override_title for keys still present
            # when the new mapping format omits that legacy-only field.
            for key, value in normalized.items():
                if "override_title" not in value and key in current and "override_title" in current[key]:
                    value["override_title"] = current[key]["override_title"]
            normalized = dict(sorted(normalized.items(), key=lambda pair: pair[0]))
            self._atomic_write(self.path(source), normalized)
        return len(normalized)

    def delete_many(self, source: Source, keys: builtins.list[str]) -> int:
        with self._lock:
            data = self.list(source)
            removed = 0
            for key in dict.fromkeys(keys):
                if key in data:
                    del data[key]
                    removed += 1
            if removed:
                data = dict(sorted(data.items(), key=lambda pair: pair[0]))
                self._atomic_write(self.path(source), data)
            return removed
