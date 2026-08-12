from __future__ import annotations

from collections import Counter
import re
from typing import Any

from src.batch_state_store import iso_utc

NAME_MEMORY_FILE_NAME = ".photoidentifier_name_memory.json"
NAME_MEMORY_SCHEMA_VERSION = "photoidentifier.name_memory.v1"
NAME_MEMORY_AUTO_NAME_PATTERN = re.compile(r"^人物\s*\d+$")


def normalize_person_name(name: Any) -> str:
    return " ".join(str(name or "").split()).strip()


def is_auto_generated_person_name(name: Any) -> bool:
    return bool(NAME_MEMORY_AUTO_NAME_PATTERN.fullmatch(normalize_person_name(name)))


def is_storable_person_name(name: Any) -> bool:
    normalized = normalize_person_name(name)
    return bool(normalized) and not is_auto_generated_person_name(normalized)


def default_name_memory_document() -> dict[str, Any]:
    return {
        "schema_version": NAME_MEMORY_SCHEMA_VERSION,
        "updated_at": "",
        "names": [],
    }


def normalize_name_memory_item(entry: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(entry, dict):
        return None
    display_name = normalize_person_name(entry.get("display_name") or entry.get("name"))
    if not is_storable_person_name(display_name):
        return None
    try:
        usage_count = max(int(entry.get("usage_count") or 0), 0)
    except (TypeError, ValueError):
        usage_count = 0
    created_at = str(entry.get("created_at") or "")
    updated_at = str(entry.get("updated_at") or "")
    last_used_at = str(entry.get("last_used_at") or updated_at or "")
    return {
        "display_name": display_name,
        "normalized_name": display_name,
        "source": str(entry.get("source") or "manual"),
        "usage_count": usage_count,
        "created_at": created_at,
        "updated_at": updated_at,
        "last_used_at": last_used_at,
    }


def sort_name_memory_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        items,
        key=lambda item: (
            int(item.get("usage_count") or 0),
            str(item.get("last_used_at") or ""),
            str(item.get("updated_at") or ""),
            str(item.get("display_name") or ""),
        ),
        reverse=True,
    )


def normalize_name_memory_document(raw: dict[str, Any] | None) -> dict[str, Any]:
    payload = default_name_memory_document()
    if not isinstance(raw, dict):
        return payload
    entries = raw.get("names")
    if not isinstance(entries, list):
        entries = raw.get("items")
    normalized: dict[str, dict[str, Any]] = {}
    for entry in entries or []:
        item = normalize_name_memory_item(entry)
        if item is None:
            continue
        normalized_name = item["normalized_name"]
        existing = normalized.get(normalized_name)
        if existing is None:
            normalized[normalized_name] = item
            continue
        existing["usage_count"] = max(
            int(existing.get("usage_count") or 0),
            int(item.get("usage_count") or 0),
        )
        if str(item.get("updated_at") or "") > str(existing.get("updated_at") or ""):
            existing["updated_at"] = item.get("updated_at") or existing.get("updated_at") or ""
        if str(item.get("last_used_at") or "") > str(existing.get("last_used_at") or ""):
            existing["last_used_at"] = item.get("last_used_at") or existing.get("last_used_at") or ""
        if not existing.get("created_at") and item.get("created_at"):
            existing["created_at"] = item["created_at"]
    payload["schema_version"] = str(raw.get("schema_version") or NAME_MEMORY_SCHEMA_VERSION)
    payload["names"] = sort_name_memory_items(list(normalized.values()))
    payload["updated_at"] = str(raw.get("updated_at") or "")
    if not payload["updated_at"] and payload["names"]:
        payload["updated_at"] = max(
            (
                str(item.get("updated_at") or item.get("last_used_at") or item.get("created_at") or "")
                for item in payload["names"]
            ),
            default="",
        )
    return payload


def merge_name_memory_names(
    document: dict[str, Any] | None,
    names: list[str],
    *,
    source: str = "manual",
    max_items: int = 200,
) -> dict[str, Any]:
    payload = normalize_name_memory_document(document)
    counts = Counter(
        normalize_person_name(name)
        for name in (names or [])
        if is_storable_person_name(name)
    )
    if not counts:
        return payload
    now = iso_utc()
    merged = {
        item["normalized_name"]: dict(item)
        for item in payload.get("names", [])
        if isinstance(item, dict) and item.get("normalized_name")
    }
    for normalized_name, count in counts.items():
        item = merged.get(normalized_name)
        if item is None:
            merged[normalized_name] = {
                "display_name": normalized_name,
                "normalized_name": normalized_name,
                "source": source or "manual",
                "usage_count": count,
                "created_at": now,
                "updated_at": now,
                "last_used_at": now,
            }
            continue
        item["display_name"] = normalized_name
        item["normalized_name"] = normalized_name
        item["source"] = item.get("source") or source or "manual"
        item["usage_count"] = int(item.get("usage_count") or 0) + count
        item["updated_at"] = now
        item["last_used_at"] = now
        if not item.get("created_at"):
            item["created_at"] = now
    payload["names"] = sort_name_memory_items(list(merged.values()))[: max(int(max_items), 1)]
    payload["updated_at"] = now
    payload["schema_version"] = NAME_MEMORY_SCHEMA_VERSION
    return payload
