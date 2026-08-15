"""Source-bound author-Q&A planning and deterministic candidate conversion.

Q&A plans retain stable IDs, facet hashes, and rehydratable locators.  They do
not retain criticism, response, impact, uncertainty, or any other excerpt.  A
candidate conversion must therefore be given the exact source again.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from bisect import bisect_left
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .candidates import (
    MAX_EVIDENCE_CHARACTERS,
    MAX_EVIDENCE_OCCURRENCE,
    MIN_EVIDENCE_CHARACTERS,
    json_string_values_view,
)
from reading_pack.errors import EXIT_IO, ReadingPackError
from reading_pack.project import write_json
from reading_pack.schema_validation import require_structure
from reading_pack.source_registry import (
    MAX_SOURCE_BYTES,
    fingerprint_source,
    validate_source_record,
)


QA_PLAN_SCHEMA_VERSION = 1
MAX_QA_PLAN_BYTES = 8 * 1024 * 1024
MAX_QA_ITEMS = 2_000
MAX_QA_FACET_CHARACTERS = 100_000
MAX_DIAGNOSTICS = 2_000
QA_KINDS = {"misreading", "clarification", "open_objection", "author_update"}
QA_FACETS = ("criticism", "impact", "response", "remaining_uncertainty")
_SOURCE_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}")
_CHAPTER_ID = re.compile(r"CH-[A-Z0-9][A-Z0-9.-]{0,99}")
_CLAIM_ID = re.compile(r"(?:CL|PROP)-[A-Z0-9][A-Z0-9.-]{0,99}")
_SHA256 = re.compile(r"[a-f0-9]{64}")
_UNSAFE = re.compile(
    r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\ud800-\udfff"
    r"\u202a-\u202e\u2066-\u2069]"
)
_ORG_FACET_LABELS = {
    "批判": "criticism",
    "本書への影響": "impact",
    "本書の応答": "response",
    "残る不確実性": "remaining_uncertainty",
    "criticism": "criticism",
    "impact on the book": "impact",
    "impact": "impact",
    "the book's response": "response",
    "book response": "response",
    "response": "response",
    "remaining uncertainty": "remaining_uncertainty",
}


@dataclass(frozen=True)
class _Facet:
    value: str
    locator: dict[str, Any]


@dataclass(frozen=True)
class _Item:
    source_key: str
    kind: str
    chapter_ids: tuple[str, ...]
    claim_ids: tuple[str, ...]
    facets: dict[str, _Facet]
    method: str


def _flat(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value)).strip()


def _content_hash(value: str) -> str:
    return hashlib.sha256(_flat(value).encode("utf-8")).hexdigest()


def _strict_json(text: str) -> Any:
    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate object key: {key}")
            result[key] = value
        return result

    return json.loads(text, object_pairs_hook=object_pairs)


def _read_text_snapshot(path: Path) -> tuple[dict[str, Any], str]:
    path = Path(path).resolve()
    before = fingerprint_source(path)
    if before["size_bytes"] > MAX_SOURCE_BYTES:
        raise ReadingPackError(
            f"Q&A source exceeds {MAX_SOURCE_BYTES} bytes", EXIT_IO
        )
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ReadingPackError(f"cannot read Q&A source {path}: {exc}", EXIT_IO) from exc
    after = fingerprint_source(path)
    if before != after:
        raise ReadingPackError("Q&A source changed while it was being read", EXIT_IO)
    return before, text


def _safe_identifier_list(
    value: Any, pattern: re.Pattern[str], label: str
) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and pattern.fullmatch(item) for item in value
    ):
        raise ReadingPackError(f"invalid author Q&A: {label} must be an ID array")
    if len(value) != len(set(value)) or len(value) > 500:
        raise ReadingPackError(f"invalid author Q&A: {label} must be bounded and unique")
    return tuple(value)


def _validate_facet_value(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise ReadingPackError(f"invalid author Q&A: {label} must be a string")
    flattened = _flat(value)
    if (
        not flattened
        or len(flattened) > MAX_QA_FACET_CHARACTERS
        or _UNSAFE.search(flattened)
    ):
        raise ReadingPackError(f"invalid author Q&A: {label} is empty or unsafe")
    return flattened


def _parse_structured_json(text: str) -> tuple[list[_Item], list[dict[str, str]]]:
    try:
        value = _strict_json(text)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ReadingPackError(f"author Q&A JSON is invalid: {exc}") from exc
    require_structure("author-qa.schema.json", value, label="author Q&A")
    if not isinstance(value, Mapping) or set(value) != {"format_version", "items"}:
        raise ReadingPackError(
            "invalid author Q&A: root fields must be format_version and items"
        )
    if value["format_version"] != 1:
        raise ReadingPackError("invalid author Q&A: format_version must equal 1")
    raw_items = value["items"]
    if not isinstance(raw_items, list) or not raw_items or len(raw_items) > MAX_QA_ITEMS:
        raise ReadingPackError(
            f"invalid author Q&A: items must contain 1 to {MAX_QA_ITEMS} objects"
        )
    result: list[_Item] = []
    source_keys: set[str] = set()
    required = {
        "source_key",
        "kind",
        "chapter_ids",
        "criticism",
        "impact",
        "response",
        "remaining_uncertainty",
    }
    allowed = required | {"claim_ids"}
    for index, raw in enumerate(raw_items):
        label = f"items[{index}]"
        if not isinstance(raw, Mapping):
            raise ReadingPackError(f"invalid author Q&A: {label} must be an object")
        if set(raw) != required and set(raw) != allowed:
            missing = required - set(raw)
            extra = set(raw) - allowed
            detail = []
            if missing:
                detail.append(f"missing {','.join(sorted(missing))}")
            if extra:
                detail.append(f"unexpected {','.join(sorted(extra))}")
            raise ReadingPackError(f"invalid author Q&A: {label} {'; '.join(detail)}")
        source_key = raw["source_key"]
        if (
            not isinstance(source_key, str)
            or not _SOURCE_KEY.fullmatch(source_key)
            or source_key in source_keys
        ):
            raise ReadingPackError(
                f"invalid author Q&A: {label}.source_key must be safe and unique"
            )
        source_keys.add(source_key)
        kind = raw["kind"]
        if kind not in QA_KINDS:
            raise ReadingPackError(f"invalid author Q&A: {label}.kind is unsupported")
        chapter_ids = _safe_identifier_list(
            raw["chapter_ids"], _CHAPTER_ID, f"{label}.chapter_ids"
        )
        claim_ids = _safe_identifier_list(
            raw.get("claim_ids", []), _CLAIM_ID, f"{label}.claim_ids"
        )
        facets: dict[str, _Facet] = {}
        for facet_name in QA_FACETS:
            facet_value = _validate_facet_value(raw[facet_name], f"{label}.{facet_name}")
            facets[facet_name] = _Facet(
                facet_value,
                {
                    "scheme": "json-pointer",
                    "start": f"/items/{index}/{facet_name}",
                },
            )
        result.append(
            _Item(
                source_key,
                kind,
                chapter_ids,
                claim_ids,
                facets,
                "structured-json-v1",
            )
        )
    return result, []


def _org_property(section: str, name: str) -> str:
    match = re.search(
        rf"(?im)^:{re.escape(name)}:[ \t]*(.*?)[ \t]*$", section
    )
    return match.group(1).strip() if match else ""


def _org_chapter_ids(heading: str) -> tuple[str, ...]:
    result: list[str] = []
    for number in re.findall(r"第\s*(\d{1,3})\s*章", heading):
        identifier = f"CH-{int(number):02d}"
        if identifier not in result:
            result.append(identifier)
    for number in re.findall(r"(?i)\bchapter\s+(\d{1,3})\b", heading):
        identifier = f"CH-{int(number):02d}"
        if identifier not in result:
            result.append(identifier)
    if "あとがき" in heading and "CH-AFTERWORD" not in result:
        result.append("CH-AFTERWORD")
    return tuple(result)


def _org_claim_ids(section: str) -> tuple[str, ...]:
    value = _org_property(section, "CLAIM_IDS")
    if not value:
        return ()
    identifiers = [item for item in re.split(r"[,\s]+", value) if item]
    return _safe_identifier_list(identifiers, _CLAIM_ID, "CLAIM_IDS")


def _org_kind(section: str) -> str:
    value = _org_property(section, "QA_TYPE").lower().replace("-", "_")
    if not value:
        return "unresolved"
    if value not in QA_KINDS:
        raise ReadingPackError(f"invalid author Q&A: unsupported QA_TYPE {value!r}")
    return value


def _org_kind_overrides(text: str) -> dict[str, str]:
    """Read an optional body-free source-key classification table.

    Authors can classify existing appendix items without editing every heading::

        #+READING_PACK_QA_TYPES: critique-a=misreading critique-b=open_objection
    """

    result: dict[str, str] = {}
    for raw in re.findall(r"(?im)^#\+READING_PACK_QA_TYPES:[ \t]*(.*)$", text):
        for token in raw.split():
            if "=" not in token:
                raise ReadingPackError("invalid READING_PACK_QA_TYPES entry")
            source_key, kind = token.split("=", 1)
            kind = kind.lower().replace("-", "_")
            if (
                not _SOURCE_KEY.fullmatch(source_key)
                or kind not in QA_KINDS
                or source_key in result
            ):
                raise ReadingPackError("invalid or duplicate READING_PACK_QA_TYPES entry")
            result[source_key] = kind
    return result


def _org_facets(section: str, section_start: int) -> dict[str, _Facet]:
    label_pattern = "|".join(
        sorted((re.escape(label) for label in _ORG_FACET_LABELS), key=len, reverse=True)
    )
    pattern = re.compile(
        rf"(?im)^-[ \t]+(?:\*)?({label_pattern})(?:\*)?[ \t]+::[ \t]*"
    )
    matches = list(pattern.finditer(section))
    facets: dict[str, _Facet] = {}
    for index, match in enumerate(matches):
        raw_label = unicodedata.normalize("NFKC", match.group(1)).casefold()
        facet_name = _ORG_FACET_LABELS.get(raw_label) or _ORG_FACET_LABELS.get(match.group(1))
        if facet_name is None or facet_name in facets:
            continue
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(section)
        if index + 1 == len(matches):
            boundary = re.search(
                r"(?m)^(?:\[fn:[^\]\r\n]+\]|#\+[A-Za-z0-9_]+:|-[ \t]+[^\r\n]+::)[ \t]*",
                section[start:end],
            )
            if boundary:
                end = start + boundary.start()
        raw_value = section[start:end]
        leading = len(raw_value) - len(raw_value.lstrip())
        trailing = len(raw_value) - len(raw_value.rstrip())
        content_start = start + leading
        content_end = end - trailing if trailing else end
        value = _validate_facet_value(
            section[content_start:content_end], f"Org facet {facet_name}"
        )
        facets[facet_name] = _Facet(
            value,
            {
                "scheme": "unicode-character",
                "start": section_start + content_start,
                "end": section_start + content_end,
            },
        )
    return facets


def _parse_org(text: str) -> tuple[list[_Item], list[dict[str, str]]]:
    headings = list(re.finditer(r"(?m)^(\*{2,8})[ \t]+(.+?)[ \t]*$", text))
    result: list[_Item] = []
    diagnostics: list[dict[str, str]] = []
    used_keys: set[str] = set()
    kind_overrides = _org_kind_overrides(text)
    for index, heading_match in enumerate(headings):
        level = len(heading_match.group(1))
        end = len(text)
        for later in headings[index + 1 :]:
            if len(later.group(1)) <= level:
                end = later.start()
                break
        section_start = heading_match.end()
        section = text[section_start:end]
        facets = _org_facets(section, section_start)
        if not facets:
            continue
        source_key = _org_property(section, "CUSTOM_ID")
        if not source_key or not _SOURCE_KEY.fullmatch(source_key):
            diagnostics.append(
                {
                    "code": "RPQA201",
                    "severity": "error",
                    "message": f"Q&A item at outline position {index + 1} needs a safe CUSTOM_ID",
                }
            )
            continue
        if source_key in used_keys:
            diagnostics.append(
                {
                    "code": "RPQA202",
                    "severity": "error",
                    "message": f"duplicate Q&A source key: {source_key}",
                    "source_key": source_key,
                }
            )
            continue
        used_keys.add(source_key)
        missing = set(QA_FACETS) - set(facets)
        if missing:
            diagnostics.append(
                {
                    "code": "RPQA203",
                    "severity": "error",
                    "message": f"Q&A item is missing facets: {','.join(sorted(missing))}",
                    "source_key": source_key,
                }
            )
            continue
        kind = _org_kind(section)
        if source_key in kind_overrides:
            if kind != "unresolved" and kind != kind_overrides[source_key]:
                raise ReadingPackError(
                    f"conflicting Q&A classification for {source_key}"
                )
            kind = kind_overrides[source_key]
        if kind == "unresolved":
            diagnostics.append(
                {
                    "code": "RPQA100",
                    "severity": "warning",
                    "message": "Q&A item requires an explicit QA_TYPE before candidate conversion",
                    "source_key": source_key,
                }
            )
        result.append(
            _Item(
                source_key,
                kind,
                _org_chapter_ids(heading_match.group(2)),
                _org_claim_ids(section),
                facets,
                "org-definition-list-v1",
            )
        )
        if len(result) > MAX_QA_ITEMS:
            raise ReadingPackError(f"author Q&A exceeds {MAX_QA_ITEMS} items")
    unknown_overrides = set(kind_overrides) - used_keys
    if unknown_overrides:
        raise ReadingPackError(
            "READING_PACK_QA_TYPES references unknown source key(s): "
            + ", ".join(sorted(unknown_overrides))
        )
    return result, diagnostics


def classify_qa_plan(
    plan: Mapping[str, Any], classifications: Mapping[str, str]
) -> dict[str, Any]:
    """Return a freshly bound plan after explicit human source-key classification."""

    checked = validate_qa_plan(plan)
    if not isinstance(classifications, Mapping) or not classifications:
        raise ReadingPackError("Q&A classifications must be a non-empty object")
    unknown = set(classifications) - {item["source_key"] for item in checked["items"]}
    if unknown:
        raise ReadingPackError(
            "Q&A classifications reference unknown source key(s): "
            + ", ".join(sorted(unknown))
        )
    for source_key, kind in classifications.items():
        if not isinstance(source_key, str) or kind not in QA_KINDS:
            raise ReadingPackError("Q&A classification contains an invalid kind")
    updated = deepcopy(checked)
    for item in updated["items"]:
        if item["source_key"] in classifications:
            if (
                item["kind"] != "unresolved"
                and item["kind"] != classifications[item["source_key"]]
            ):
                raise ReadingPackError(
                    f"Q&A classification conflicts with source kind for {item['source_key']}"
                )
            item["kind"] = classifications[item["source_key"]]
    updated["diagnostics"] = [
        diagnostic
        for diagnostic in updated["diagnostics"]
        if not (
            diagnostic.get("code") == "RPQA100"
            and diagnostic.get("source_key") in classifications
        )
    ]
    updated["outcome"] = (
        "blocked"
        if not updated["items"]
        or any(item["severity"] == "error" for item in updated["diagnostics"])
        else "review_required"
        if any(item["kind"] == "unresolved" for item in updated["items"])
        else "ready"
    )
    updated["plan_id"] = _plan_id(updated)
    validate_qa_plan(updated)
    return updated


def _item_identity(source_id: str, source_key: str) -> str:
    return hashlib.sha256(f"{source_id}\0{source_key}".encode("utf-8")).hexdigest().upper()


def _record_id(source_id: str, source_key: str) -> str:
    readable = re.sub(r"[^A-Z0-9.-]+", "-", source_key.upper()).strip("-.")
    readable = re.sub(r"^CRITIQUE-", "", readable) or "ITEM"
    digest = _item_identity(source_id, source_key)
    return f"MIS-{readable[:60].rstrip('-.')}-{digest[:8]}"


def _plan_id(plan: Mapping[str, Any]) -> str:
    body = {key: value for key, value in plan.items() if key != "plan_id"}
    encoded = json.dumps(
        body, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return f"QP-{hashlib.sha256(encoded).hexdigest()[:20].upper()}"


def create_qa_plan(
    source_path: Path, source_record: Mapping[str, Any]
) -> dict[str, Any]:
    """Build a body-free Q&A plan from a registered author-Q&A source."""

    source = validate_source_record(source_record, "Q&A source")
    if source["role"] != "author-qa":
        raise ReadingPackError("Q&A planning requires a source with role author-qa")
    if source["format"] not in {"json", "org"}:
        raise ReadingPackError("Q&A planning currently supports structured JSON or Org")
    fingerprint, text = _read_text_snapshot(source_path)
    if any(fingerprint[key] != source[key] for key in ("name", "sha256", "size_bytes")):
        raise ReadingPackError("Q&A source does not match its registered source identity")
    if source["format"] == "json":
        parsed, diagnostics = _parse_structured_json(text)
    else:
        parsed, diagnostics = _parse_org(text)
    items: list[dict[str, Any]] = []
    for item in parsed:
        digest = _item_identity(source["id"], item.source_key)
        facets = {
            facet_name: {
                "locator": deepcopy(item.facets[facet_name].locator),
                "content_sha256": _content_hash(item.facets[facet_name].value),
                "characters": len(item.facets[facet_name].value),
            }
            for facet_name in QA_FACETS
        }
        items.append(
            {
                "qa_id": f"QI-{digest[:20]}",
                "candidate_record_id": _record_id(source["id"], item.source_key),
                "source_key": item.source_key,
                "kind": item.kind,
                "chapter_ids": list(item.chapter_ids),
                "claim_ids": list(item.claim_ids),
                "facets": facets,
                "provenance": {
                    "source_id": source["id"],
                    "source_sha256": source["sha256"],
                    "method": item.method,
                },
            }
        )
    if not items:
        diagnostics.append(
            {
                "code": "RPQA200",
                "severity": "error",
                "message": "no complete four-facet Q&A items were found",
            }
        )
    if any(item["severity"] == "error" for item in diagnostics):
        outcome = "blocked"
    elif any(item["kind"] == "unresolved" for item in items):
        outcome = "review_required"
    else:
        outcome = "ready"
    plan = {
        "schema_version": QA_PLAN_SCHEMA_VERSION,
        "plan_id": "",
        "source": deepcopy(source),
        "items": items,
        "diagnostics": diagnostics,
        "outcome": outcome,
    }
    plan["plan_id"] = _plan_id(plan)
    validate_qa_plan(plan)
    return plan


def _validate_locator(value: Any, label: str) -> None:
    if not isinstance(value, Mapping) or value.get("scheme") not in {
        "json-pointer",
        "unicode-character",
    }:
        raise ReadingPackError(f"invalid Q&A plan: {label} locator is invalid")
    if value["scheme"] == "json-pointer":
        if set(value) != {"scheme", "start"} or not isinstance(value.get("start"), str):
            raise ReadingPackError(f"invalid Q&A plan: {label} JSON locator is invalid")
    elif (
        set(value) != {"scheme", "start", "end"}
        or not isinstance(value.get("start"), int)
        or isinstance(value.get("start"), bool)
        or not isinstance(value.get("end"), int)
        or isinstance(value.get("end"), bool)
        or value["start"] < 0
        or value["end"] <= value["start"]
    ):
        raise ReadingPackError(f"invalid Q&A plan: {label} character locator is invalid")


def validate_qa_plan(value: Any) -> dict[str, Any]:
    require_structure("qa-plan.schema.json", value, label="Q&A plan")
    if not isinstance(value, Mapping):
        raise ReadingPackError("invalid Q&A plan: root must be an object")
    fields = {
        "schema_version",
        "plan_id",
        "source",
        "items",
        "diagnostics",
        "outcome",
    }
    if set(value) != fields:
        raise ReadingPackError("invalid Q&A plan: missing or unexpected root fields")
    if value["schema_version"] != QA_PLAN_SCHEMA_VERSION:
        raise ReadingPackError("invalid Q&A plan: unsupported schema version")
    if not isinstance(value["plan_id"], str) or not re.fullmatch(
        r"QP-[A-F0-9]{20}", value["plan_id"]
    ):
        raise ReadingPackError("invalid Q&A plan: plan ID is invalid")
    source = validate_source_record(value["source"], "Q&A plan source")
    if source["role"] != "author-qa" or source["format"] not in {"json", "org"}:
        raise ReadingPackError("invalid Q&A plan: source role or format is invalid")
    raw_items = value["items"]
    if not isinstance(raw_items, list) or len(raw_items) > MAX_QA_ITEMS:
        raise ReadingPackError("invalid Q&A plan: items are invalid")
    qa_ids: set[str] = set()
    record_ids: set[str] = set()
    source_keys: set[str] = set()
    item_fields = {
        "qa_id",
        "candidate_record_id",
        "source_key",
        "kind",
        "chapter_ids",
        "claim_ids",
        "facets",
        "provenance",
    }
    for index, item in enumerate(raw_items):
        label = f"items[{index}]"
        if not isinstance(item, Mapping) or set(item) != item_fields:
            raise ReadingPackError(f"invalid Q&A plan: {label} fields are invalid")
        source_key = item["source_key"]
        if not isinstance(source_key, str) or not _SOURCE_KEY.fullmatch(source_key):
            raise ReadingPackError(f"invalid Q&A plan: {label}.source_key is invalid")
        digest = _item_identity(source["id"], source_key)
        if item["qa_id"] != f"QI-{digest[:20]}":
            raise ReadingPackError(f"invalid Q&A plan: {label}.qa_id binding is invalid")
        if item["candidate_record_id"] != _record_id(source["id"], source_key):
            raise ReadingPackError(
                f"invalid Q&A plan: {label}.candidate_record_id binding is invalid"
            )
        for collection, name in (
            (qa_ids, item["qa_id"]),
            (record_ids, item["candidate_record_id"]),
            (source_keys, source_key),
        ):
            if name in collection:
                raise ReadingPackError(f"invalid Q&A plan: duplicate item identity")
            collection.add(name)
        if item["kind"] not in QA_KINDS | {"unresolved"}:
            raise ReadingPackError(f"invalid Q&A plan: {label}.kind is invalid")
        _safe_identifier_list(item["chapter_ids"], _CHAPTER_ID, f"{label}.chapter_ids")
        _safe_identifier_list(item["claim_ids"], _CLAIM_ID, f"{label}.claim_ids")
        facets = item["facets"]
        if not isinstance(facets, Mapping) or set(facets) != set(QA_FACETS):
            raise ReadingPackError(f"invalid Q&A plan: {label}.facets are invalid")
        for facet_name in QA_FACETS:
            facet = facets[facet_name]
            if not isinstance(facet, Mapping) or set(facet) != {
                "locator",
                "content_sha256",
                "characters",
            }:
                raise ReadingPackError(f"invalid Q&A plan: {label}.{facet_name} is invalid")
            _validate_locator(facet["locator"], f"{label}.{facet_name}")
            if not isinstance(facet["content_sha256"], str) or not _SHA256.fullmatch(
                facet["content_sha256"]
            ):
                raise ReadingPackError(f"invalid Q&A plan: {label}.{facet_name} hash is invalid")
            if (
                not isinstance(facet["characters"], int)
                or isinstance(facet["characters"], bool)
                or not 1 <= facet["characters"] <= MAX_QA_FACET_CHARACTERS
            ):
                raise ReadingPackError(f"invalid Q&A plan: {label}.{facet_name} size is invalid")
        provenance = item["provenance"]
        if not isinstance(provenance, Mapping) or set(provenance) != {
            "source_id",
            "source_sha256",
            "method",
        }:
            raise ReadingPackError(f"invalid Q&A plan: {label}.provenance is invalid")
        if (
            provenance["source_id"] != source["id"]
            or provenance["source_sha256"] != source["sha256"]
            or provenance["method"] not in {"structured-json-v1", "org-definition-list-v1"}
        ):
            raise ReadingPackError(f"invalid Q&A plan: {label}.provenance is stale")
    diagnostics = value["diagnostics"]
    if not isinstance(diagnostics, list) or len(diagnostics) > MAX_DIAGNOSTICS:
        raise ReadingPackError("invalid Q&A plan: diagnostics are invalid")
    for diagnostic in diagnostics:
        if (
            not isinstance(diagnostic, Mapping)
            or not {"code", "severity", "message"}.issubset(diagnostic)
            or set(diagnostic) - {"code", "severity", "message", "source_key"}
            or not isinstance(diagnostic["code"], str)
            or not re.fullmatch(r"RPQA[0-9]{3}", diagnostic["code"])
            or diagnostic["severity"] not in {"info", "warning", "error"}
            or not isinstance(diagnostic["message"], str)
            or not 1 <= len(diagnostic["message"]) <= 1_000
            or ("source_key" in diagnostic and not _SOURCE_KEY.fullmatch(diagnostic["source_key"]))
        ):
            raise ReadingPackError("invalid Q&A plan: diagnostic record is invalid")
    if value["outcome"] not in {"ready", "review_required", "blocked"}:
        raise ReadingPackError("invalid Q&A plan: outcome is invalid")
    expected_outcome = (
        "blocked"
        if not raw_items or any(item["severity"] == "error" for item in diagnostics)
        else "review_required"
        if any(item["kind"] == "unresolved" for item in raw_items)
        else "ready"
    )
    if value["outcome"] != expected_outcome:
        raise ReadingPackError("invalid Q&A plan: outcome is inconsistent")
    if value["plan_id"] != _plan_id(value):
        raise ReadingPackError("Q&A plan checksum does not match its contents")
    return deepcopy(dict(value))


def write_qa_plan(path: Path, plan: Mapping[str, Any]) -> None:
    validate_qa_plan(plan)
    path = Path(path).resolve()
    if path.exists():
        raise ReadingPackError(f"refusing to overwrite existing Q&A plan: {path}", EXIT_IO)
    if path.name in {"reading-pack.toml", "quality-plan.json", "sources.json"} or (
        path.parent.name in {"data", "templates", "dist"}
    ):
        raise ReadingPackError(
            "refusing to write a Q&A plan over a canonical or generated project path",
            EXIT_IO,
        )
    source_name = plan["source"]["name"]
    if path.name == source_name and path.parent == Path.cwd().resolve():
        raise ReadingPackError("Q&A plan output must not overwrite its source", EXIT_IO)
    write_json(path, dict(plan))


def load_qa_plan(path: Path) -> dict[str, Any]:
    path = Path(path)
    try:
        if path.stat().st_size > MAX_QA_PLAN_BYTES:
            raise ReadingPackError(
                f"Q&A plan exceeds {MAX_QA_PLAN_BYTES} bytes", EXIT_IO
            )
        raw = _strict_json(path.read_text(encoding="utf-8"))
    except ReadingPackError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ReadingPackError(f"cannot read Q&A plan {path}: {exc}", EXIT_IO) from exc
    return validate_qa_plan(raw)


def load_qa_classifications(path: Path) -> dict[str, str]:
    """Load a bounded JSON object mapping stable source keys to QA kinds."""

    path = Path(path)
    try:
        if path.stat().st_size > MAX_QA_PLAN_BYTES:
            raise ReadingPackError("Q&A classifications are too large", EXIT_IO)
        value = _strict_json(path.read_text(encoding="utf-8"))
    except ReadingPackError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ReadingPackError(f"cannot read Q&A classifications {path}: {exc}", EXIT_IO) from exc
    if not isinstance(value, Mapping):
        raise ReadingPackError("Q&A classifications must be a JSON object")
    result = dict(value)
    for source_key, kind in result.items():
        if not isinstance(source_key, str) or not _SOURCE_KEY.fullmatch(source_key) or kind not in QA_KINDS:
            raise ReadingPackError("Q&A classifications contain an invalid source key or kind")
    return result


def _parsed_by_key(source_format: str, text: str) -> dict[str, _Item]:
    if source_format == "json":
        items, _ = _parse_structured_json(text)
    elif source_format == "org":
        items, _ = _parse_org(text)
    else:  # validated plans cannot reach this branch
        raise ReadingPackError("unsupported Q&A source format")
    return {item.source_key: item for item in items}


def _evidence_snippet(value: str, source_text: str) -> str:
    """Choose a bounded, direct source span for transient candidate evidence."""

    normalized_source = _flat(source_text).casefold()
    normalized_value = _flat(value)
    candidates = [normalized_value]
    candidates.extend(
        sorted(
            (_flat(part) for part in re.split(r"[\n\\]+", value)),
            key=len,
            reverse=True,
        )
    )
    for candidate in candidates:
        for width in (min(500, len(candidate)), min(320, len(candidate)), min(160, len(candidate))):
            if width >= 8:
                snippet = candidate[:width].strip()
                if len(snippet) >= 8 and snippet.casefold() in normalized_source:
                    return snippet
    raise ReadingPackError("Q&A facet cannot be rehydrated as direct source evidence")


def _facet_evidence_snippet(value: str) -> str:
    """Choose the same bounded snippet without rescanning the whole source."""

    normalized_value = _flat(value)
    for width in (
        min(MAX_EVIDENCE_CHARACTERS, len(normalized_value)),
        min(320, len(normalized_value)),
        min(160, len(normalized_value)),
    ):
        if width >= MIN_EVIDENCE_CHARACTERS:
            snippet = normalized_value[:width].strip()
            if len(snippet) >= MIN_EVIDENCE_CHARACTERS:
                return snippet
    raise ReadingPackError("Q&A facet is too short for direct evidence")


def _occurrence_in_span(
    snippet: str,
    normalized_source: str,
    span: tuple[int, int],
    positions_by_snippet: dict[str, list[int]],
) -> int:
    """Return the global occurrence whose complete span is inside one facet."""

    support = _flat(snippet).casefold()
    positions = positions_by_snippet.get(support)
    if positions is None:
        positions = []
        cursor = 0
        while len(positions) <= MAX_EVIDENCE_OCCURRENCE:
            position = normalized_source.find(support, cursor)
            if position < 0:
                break
            positions.append(position)
            cursor = position + len(support)
        positions_by_snippet[support] = positions
    occurrence = bisect_left(positions, span[0])
    if (
        occurrence < len(positions)
        and occurrence <= MAX_EVIDENCE_OCCURRENCE
        and positions[occurrence] + len(support) <= span[1]
    ):
        return occurrence
    if occurrence > MAX_EVIDENCE_OCCURRENCE:
        raise ReadingPackError(
            "Q&A evidence occurrence exceeds the bounded search limit"
        )
    raise ReadingPackError("Q&A evidence cannot be bound to its planned facet")


def qa_plan_to_candidate_responses(
    plan: Mapping[str, Any],
    source_path: Path,
    *,
    include_kinds: Iterable[str] = QA_KINDS,
) -> list[dict[str, Any]]:
    """Rehydrate an exact Q&A source into standard transient candidate input.

    The returned object contains prose and must remain transient.  Callers
    should pass it directly to ``create_candidate_run`` rather than write it to
    a public project path.
    """

    checked = validate_qa_plan(plan)
    if checked["outcome"] == "blocked":
        raise ReadingPackError("blocked Q&A plan cannot create candidates")
    selected_kinds = set(include_kinds)
    if not selected_kinds or not selected_kinds <= QA_KINDS:
        raise ReadingPackError("include_kinds contains an unsupported Q&A kind")
    unresolved = [item["source_key"] for item in checked["items"] if item["kind"] == "unresolved"]
    if unresolved:
        raise ReadingPackError(
            "Q&A plan contains unresolved item kinds; classify the author source explicitly"
        )
    fingerprint, source_text = _read_text_snapshot(source_path)
    source = checked["source"]
    if any(fingerprint[key] != source[key] for key in ("name", "sha256", "size_bytes")):
        raise ReadingPackError("Q&A source is stale or does not match the plan")
    parsed = _parsed_by_key(source["format"], source_text)
    if source["format"] == "json":
        evidence_text, json_spans = json_string_values_view(source_text)
        normalized_evidence_text = _flat(evidence_text).casefold()
        positions_by_snippet: dict[str, list[int]] = {}
    else:
        evidence_text, json_spans = source_text, {}
        normalized_evidence_text = ""
        positions_by_snippet = {}
    responses: list[dict[str, Any]] = []
    for planned in checked["items"]:
        current = parsed.get(planned["source_key"])
        if current is None:
            raise ReadingPackError("Q&A item is stale or missing from its source")
        if (
            (current.kind != "unresolved" and current.kind != planned["kind"])
            or list(current.chapter_ids) != planned["chapter_ids"]
            or list(current.claim_ids) != planned["claim_ids"]
        ):
            raise ReadingPackError("Q&A item metadata is stale or does not match the plan")
        values: dict[str, str] = {}
        for facet_name in QA_FACETS:
            facet = current.facets[facet_name]
            planned_facet = planned["facets"][facet_name]
            if (
                facet.locator != planned_facet["locator"]
                or _content_hash(facet.value) != planned_facet["content_sha256"]
                or len(facet.value) != planned_facet["characters"]
            ):
                raise ReadingPackError("Q&A facet is stale or does not match the plan")
            values[facet_name] = facet.value
        if planned["kind"] not in selected_kinds:
            continue
        record: dict[str, Any] = {
            "id": planned["candidate_record_id"],
            "kind": planned["kind"],
            "issue": values["criticism"],
            "impact": values["impact"],
            "response": values["response"],
            "remaining_uncertainty": values["remaining_uncertainty"],
            "chapter_ids": list(planned["chapter_ids"]),
            "status": "draft",
        }
        if planned["claim_ids"]:
            record["claim_ids"] = list(planned["claim_ids"])
        evidence: list[dict[str, Any]] = []
        for field, facet_name in (
            ("issue", "criticism"),
            ("impact", "impact"),
            ("response", "response"),
            ("remaining_uncertainty", "remaining_uncertainty"),
        ):
            snippet = (
                _facet_evidence_snippet(values[facet_name])
                if source["format"] == "json"
                else _evidence_snippet(values[facet_name], evidence_text)
            )
            item: dict[str, Any] = {
                "snippet": snippet,
                "supports_field": field,
            }
            if source["format"] == "json":
                pointer = current.facets[facet_name].locator["start"]
                span = json_spans.get(pointer)
                if span is None:
                    raise ReadingPackError("Q&A JSON facet is absent from evidence view")
                item["occurrence"] = _occurrence_in_span(
                    snippet,
                    normalized_evidence_text,
                    span,
                    positions_by_snippet,
                )
            evidence.append(item)
        responses.append(
            {
                "collection": "misreadings",
                "record": record,
                "evidence": evidence,
            }
        )
    if not responses:
        raise ReadingPackError("Q&A plan contains no items selected for candidate conversion")
    return responses


def validate_generated_qa_responses(
    plan: Mapping[str, Any], responses: Any, source_path: Path
) -> list[dict[str, Any]]:
    """Bind concise generated Q&A candidates to every reviewed plan item.

    This validation is structural. Source occurrence, copy risk, field limits,
    and canonical references are rechecked by ``create_candidate_run``.
    """

    checked = validate_qa_plan(plan)
    if checked["outcome"] != "ready":
        raise ReadingPackError("generated Q&A responses require a ready classified plan")
    fingerprint, source_text = _read_text_snapshot(source_path)
    source = checked["source"]
    if any(fingerprint[key] != source[key] for key in ("name", "sha256", "size_bytes")):
        raise ReadingPackError("Q&A source is stale or does not match the plan")
    parsed_by_key = _parsed_by_key(source["format"], source_text)
    if source["format"] == "json":
        evidence_text, json_spans = json_string_values_view(source_text)
    else:
        evidence_text, json_spans = source_text, {}
    normalized_source = _flat(evidence_text).casefold()
    value = responses
    if isinstance(value, str):
        try:
            value = _strict_json(value)
        except (json.JSONDecodeError, ValueError) as exc:
            raise ReadingPackError(f"generated Q&A responses are invalid JSON: {exc}") from exc
    if isinstance(value, Mapping) and set(value) == {"candidates"}:
        value = value["candidates"]
    if not isinstance(value, list):
        raise ReadingPackError("generated Q&A responses must be a candidates array")
    planned_by_id = {
        item["candidate_record_id"]: item for item in checked["items"]
    }
    if len(value) != len(planned_by_id):
        raise ReadingPackError(
            "generated Q&A responses must account for every classified plan item exactly once"
        )
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping) or set(raw) != {"collection", "record", "evidence"}:
            raise ReadingPackError(
                f"generated Q&A response {index} has missing or unexpected fields"
            )
        if raw["collection"] != "misreadings" or not isinstance(raw["record"], Mapping):
            raise ReadingPackError(f"generated Q&A response {index} is not a reading issue")
        record = raw["record"]
        issue_fields = {field for field in ("issue", "misreading") if field in record}
        if len(issue_fields) != 1:
            raise ReadingPackError(
                f"generated Q&A response {index} requires exactly one issue field"
            )
        issue_field = next(iter(issue_fields))
        facet_fields = {
            issue_field,
            "impact",
            "response",
            "remaining_uncertainty",
        }
        record_id = record.get("id")
        planned = planned_by_id.get(record_id)
        if planned is None or record_id in seen:
            raise ReadingPackError(
                f"generated Q&A response {index} has an unknown or duplicate record ID"
            )
        seen.add(record_id)
        expected_fields = {
            "id",
            "kind",
            *facet_fields,
            "chapter_ids",
            "status",
        }
        if planned["claim_ids"]:
            expected_fields.add("claim_ids")
        if set(record) != expected_fields:
            raise ReadingPackError(
                f"generated Q&A response {index} record fields do not match its plan item"
            )
        if (
            record.get("kind") != planned["kind"]
            or record.get("chapter_ids") != planned["chapter_ids"]
            or record.get("claim_ids", []) != planned["claim_ids"]
            or record.get("status") != "draft"
            or any(not isinstance(record.get(field), str) or not record[field].strip() for field in facet_fields)
        ):
            raise ReadingPackError(
                f"generated Q&A response {index} metadata or facets do not match its plan item"
            )
        evidence = raw["evidence"]
        if not isinstance(evidence, list) or not evidence:
            raise ReadingPackError(f"generated Q&A response {index} has no evidence")
        supported: set[str] = set()
        current = parsed_by_key.get(planned["source_key"])
        if current is None:
            raise ReadingPackError(
                f"generated Q&A response {index} plan item is stale or missing"
            )
        for evidence_item in evidence:
            if (
                not isinstance(evidence_item, Mapping)
                or set(evidence_item) - {"snippet", "occurrence", "supports_field"}
                or not isinstance(evidence_item.get("snippet"), str)
                or evidence_item.get("supports_field") not in facet_fields
            ):
                raise ReadingPackError(
                    f"generated Q&A response {index} has invalid field-bound evidence"
                )
            supported.add(evidence_item["supports_field"])
            field = evidence_item["supports_field"]
            facet_name = "criticism" if field in {"issue", "misreading"} else field
            snippet = _flat(evidence_item["snippet"]).casefold()
            if not MIN_EVIDENCE_CHARACTERS <= len(snippet) <= MAX_EVIDENCE_CHARACTERS:
                raise ReadingPackError(
                    f"generated Q&A response {index} evidence length is invalid"
                )
            facet_text = _flat(current.facets[facet_name].value).casefold()
            if snippet not in facet_text:
                raise ReadingPackError(
                    f"generated Q&A response {index} evidence is outside its planned facet"
                )
            occurrence = evidence_item.get("occurrence", 0)
            if (
                not isinstance(occurrence, int)
                or isinstance(occurrence, bool)
                or not 0 <= occurrence <= MAX_EVIDENCE_OCCURRENCE
            ):
                raise ReadingPackError(
                    f"generated Q&A response {index} evidence occurrence is invalid"
                )
            cursor = 0
            located = -1
            for _ in range(occurrence + 1):
                located = normalized_source.find(snippet, cursor)
                if located < 0:
                    break
                cursor = located + len(snippet)
            if source["format"] == "json":
                pointer = current.facets[facet_name].locator["start"]
                span = json_spans.get(pointer)
                in_planned_facet = (
                    span is not None
                    and span[0] <= located
                    and located + len(snippet) <= span[1]
                )
            else:
                facet_occurrences: set[int] = set()
                cursor = 0
                while True:
                    position = normalized_source.find(facet_text, cursor)
                    if position < 0:
                        break
                    facet_occurrences.add(position + facet_text.find(snippet))
                    cursor = position + len(facet_text)
                in_planned_facet = located in facet_occurrences
            if not in_planned_facet:
                raise ReadingPackError(
                    f"generated Q&A response {index} evidence occurrence is outside its planned facet"
                )
        if supported != facet_fields:
            raise ReadingPackError(
                f"generated Q&A response {index} must evidence all four Q&A facets"
            )
        normalized = deepcopy(dict(raw))
        if issue_field == "misreading":
            normalized["record"]["issue"] = normalized["record"].pop("misreading")
            for evidence_item in normalized["evidence"]:
                if evidence_item.get("supports_field") == "misreading":
                    evidence_item["supports_field"] = "issue"
        result.append(normalized)
    if seen != set(planned_by_id):
        raise ReadingPackError("generated Q&A responses do not cover the complete plan")
    return result


def create_qa_candidate_run(
    project: Path,
    *,
    language: str,
    plan: Mapping[str, Any],
    source_path: Path,
    run_directory: Path,
    run_id: str | None = None,
    generated_responses: Any | None = None,
) -> Path:
    """Convert one reviewed Q&A plan directly into a private candidate run."""

    # Local imports keep the body-free parser independent of canonical and
    # candidate implementation details.
    from .candidates import create_candidate_run
    from reading_pack.project import load_config, load_language_data
    from reading_pack.source_registry import registered_source, verify_registered_source

    project = Path(project).resolve()
    checked = validate_qa_plan(plan)
    source_id = checked["source"]["id"]
    registered = registered_source(project, source_id)
    if registered != checked["source"]:
        raise ReadingPackError("Q&A plan source is stale relative to the source registry")
    verify_registered_source(project, source_id, source_path, expected_role="author-qa")
    config = load_config(project)
    if language not in config.get("languages", []):
        raise ReadingPackError(f"language is not configured: {language}")
    if registered.get("language") not in {language, "und"}:
        raise ReadingPackError(
            "Q&A source language does not match the target pack language; use the translation workflow"
        )
    data_by_lang = {
        lang: load_language_data(project, lang)
        for lang in config.get("languages", [])
    }
    canonical = data_by_lang[language]
    responses = (
        qa_plan_to_candidate_responses(checked, source_path)
        if generated_responses is None
        else validate_generated_qa_responses(checked, generated_responses, source_path)
    )
    return create_candidate_run(
        Path(run_directory),
        source_path=Path(source_path),
        responses=responses,
        language=language,
        canonical_data=canonical,
        project_data_by_lang=data_by_lang,
        known_chapter_ids={
            item.get("id")
            for item in canonical.get("chapters", [])
            if isinstance(item, Mapping) and isinstance(item.get("id"), str)
        },
        run_id=run_id,
        generator={
            "adapter": (
                "author-qa-deterministic"
                if generated_responses is None
                else "author-qa-generated-json"
            ),
            "model": "",
            "revision": f"1:{checked['plan_id']}",
            "settings_hash": hashlib.sha256(
                json.dumps(
                    checked,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest(),
        },
        support_source=registered,
    )
