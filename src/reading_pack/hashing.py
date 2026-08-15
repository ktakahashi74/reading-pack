"""Stable semantic hashes used by the translation-freshness gate."""

from __future__ import annotations

import hashlib
import json
from typing import Any

_NON_SEMANTIC_KEYS = {
    "source_hash",
    "source_id",
    "status",
    "translation_status",
    "review_notes",
}


def semantic_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: semantic_value(item)
            for key, item in sorted(value.items())
            if key not in _NON_SEMANTIC_KEYS
        }
    if isinstance(value, list):
        return [semantic_value(item) for item in value]
    return value


def semantic_hash(record: dict[str, Any]) -> str:
    payload = json.dumps(
        semantic_value(record),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def canonical_data_hash(data_by_lang: dict[str, dict[str, Any]]) -> str:
    """Hash all publishable language data while excluding review metadata."""

    payload = json.dumps(
        semantic_value(data_by_lang),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def file_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
