"""Runtime access to externally managed number mapping JSON files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_number_mapping(path: Path | None) -> dict[str, dict[str, Any]]:
    """Load the current mapping on every lookup; missing files mean an empty map."""
    if path is None:
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(key): value for key, value in raw.items() if isinstance(value, dict)}
