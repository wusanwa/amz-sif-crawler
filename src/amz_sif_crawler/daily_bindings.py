from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _bindings_path(base_dir: str | Path) -> Path:
    return Path(base_dir).resolve() / "config" / "daily_bindings.json"


def load_daily_bindings(base_dir: str | Path) -> dict[str, list[str]]:
    path = _bindings_path(base_dir)
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        payload: Any = json.load(handle)
    bindings = payload.get("bindings", {}) if isinstance(payload, dict) else {}
    if not isinstance(bindings, dict):
        return {}

    normalized: dict[str, list[str]] = {}
    for bind_key, values in bindings.items():
        if not isinstance(bind_key, str) or not isinstance(values, list):
            continue
        normalized[bind_key] = [str(value).strip() for value in values if str(value).strip()]
    return normalized


def save_daily_bindings(base_dir: str | Path, bindings: dict[str, list[str]]) -> Path:
    path = _bindings_path(base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "bindings": {
            str(bind_key): [str(value).strip() for value in values if str(value).strip()]
            for bind_key, values in sorted(bindings.items())
        }
    }
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return path


def add_daily_asins(base_dir: str | Path, bind_key: str, values: list[str]) -> list[str]:
    bindings = load_daily_bindings(base_dir)
    current = bindings.get(bind_key, [])
    seen = set(current)
    for value in values:
        normalized = str(value).strip()
        if normalized and normalized not in seen:
            current.append(normalized)
            seen.add(normalized)
    bindings[bind_key] = current
    save_daily_bindings(base_dir, bindings)
    return current


def remove_daily_asins(base_dir: str | Path, bind_key: str, values: list[str]) -> list[str]:
    bindings = load_daily_bindings(base_dir)
    current = bindings.get(bind_key, [])
    remove_set = {str(value).strip() for value in values if str(value).strip()}
    updated = [value for value in current if value not in remove_set]
    if updated:
        bindings[bind_key] = updated
    elif bind_key in bindings:
        del bindings[bind_key]
    save_daily_bindings(base_dir, bindings)
    return updated
