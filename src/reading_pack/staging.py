"""Body-free staging plans between source extraction and canonical pack data.

The format adapters intentionally remain responsible only for recognizing a
source.  This module turns their small, structure-only result into a reviewable
plan, and applies a reviewed plan without allowing it to carry summaries,
approval states, or other authored canonical content.
"""

from __future__ import annotations

import hashlib
import json
import re
import stat
import unicodedata
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from .errors import EXIT_IO, ReadingPackError
from .hashing import semantic_hash
from .importers import MAX_SOURCE_BYTES, ExtractedBook, extract
from .project import (
    atomic_write_text,
    load_config,
    load_language_data,
    project_lock,
    write_json,
)
from .schema_validation import require_structure


PLAN_SCHEMA_VERSION = 1
MAX_HEADING_CHARACTERS = 500
MAX_PLAN_BYTES = 8 * 1024 * 1024
MAX_PLAN_UNITS = 20_000
MAX_METADATA_CANDIDATES = 64
MAX_DIAGNOSTICS = 20_000
MAX_CONFIDENCE_REASONS = 32
MAX_PROVENANCE_RECORDS = 8
MAX_LOCATORS = 8
MAX_MANUAL_CHAPTERS = 5_000
MAX_MANUAL_SECTIONS = 20_000
SOURCE_ID = "SRC-1"
SOURCE_FORMATS = {"markdown", "org", "epub3", "pdf", "pdf-vertical", "text"}
UNIT_KINDS = {
    "book",
    "frontmatter",
    "part",
    "chapter",
    "section",
    "afterword",
    "appendix",
    "notes",
    "bibliography",
    "glossary",
    "index",
    "colophon",
    "unknown",
}
CONFIDENCE_LEVELS = {"high", "medium", "low", "conflict"}
OUTCOMES = {"ready", "review_required", "blocked"}
DIAGNOSTIC_SEVERITIES = {"info", "warning", "error"}
_CHAPTER_ID = re.compile(r"^CH-[A-Z0-9][A-Z0-9.-]*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]")
_PLAIN_HEADING = re.compile(
    r"^(?:chapter\s+\d+|part\s+\d+|第\s*[^\s]{1,8}\s*章)(?:\b|\s|[:：.-])",
    re.IGNORECASE,
)


def _normalized(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value)).strip().casefold()


def _safe_heading(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    value = _CONTROL.sub("", value)
    value = re.sub(r"\s+", " ", value).strip()
    if not value or len(value) > MAX_HEADING_CHARACTERS:
        return ""
    return value


def _fingerprint(path: Path) -> dict[str, Any]:
    path = path.resolve()
    try:
        before = path.stat()
    except OSError as exc:
        raise ReadingPackError(f"cannot read source {path}: {exc}", EXIT_IO) from exc
    if not stat.S_ISREG(before.st_mode):
        raise ReadingPackError(f"source is not a regular file: {path}", EXIT_IO)
    if before.st_size > MAX_SOURCE_BYTES:
        raise ReadingPackError(
            f"source exceeds {MAX_SOURCE_BYTES} bytes: {path}", EXIT_IO
        )
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            total = 0
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                total += len(chunk)
                if total > MAX_SOURCE_BYTES:
                    raise ReadingPackError(
                        f"source exceeds {MAX_SOURCE_BYTES} bytes: {path}", EXIT_IO
                    )
                digest.update(chunk)
        after = path.stat()
    except OSError as exc:
        raise ReadingPackError(f"cannot read source {path}: {exc}", EXIT_IO) from exc
    identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    if identity_before != identity_after:
        raise ReadingPackError("source changed while it was being read", EXIT_IO)
    return {
        "name": path.name,
        "sha256": digest.hexdigest(),
        "size_bytes": before.st_size,
    }


def _confidence(source_format: str) -> dict[str, Any]:
    if source_format in {"markdown", "org", "epub3"}:
        return {
            "level": "high",
            "reasons": ["recognized from explicit document heading structure"],
        }
    if source_format in {"pdf", "pdf-vertical"}:
        return {
            "level": "medium",
            "reasons": [
                "inferred from conservative PDF table-of-contents heuristics"
                if source_format == "pdf"
                else "reconstructed from an untagged vertical PDF text layer; structure requires review"
            ],
        }
    return {
        "level": "low",
        "reasons": ["recognized from plain-text heading conventions"],
    }


def _provenance(source_hash: str, source_format: str, method: str) -> list[dict[str, str]]:
    return [
        {
            "source_id": SOURCE_ID,
            "source_sha256": source_hash,
            "method": f"reading_pack.importers.{source_format}.{method}",
        }
    ]


def _unit_id(
    *,
    kind: str,
    parent_id: str | None,
    title: str,
    occurrence: int,
    source_key: str = "",
) -> str:
    identity = json.dumps(
        [kind, parent_id or "", source_key or _normalized(title), occurrence],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"U-{hashlib.sha256(identity).hexdigest()[:20].upper()}"


def _classify(chapter: Mapping[str, Any]) -> str:
    identifier = str(chapter.get("id", "")).upper()
    title = _normalized(str(chapter.get("title", "")))
    if identifier in {"CH-PREFACE", "CH-FOREWORD"} or title in {
        "preface",
        "foreword",
        "まえがき",
        "序文",
    }:
        return "frontmatter"
    if identifier in {"CH-AFTERWORD", "CH-EPILOGUE"} or title in {
        "afterword",
        "あとがき",
    }:
        return "afterword"
    if identifier.startswith("CH-APP") or title.startswith(("appendix", "付録")):
        return "appendix"
    if identifier == "CH-NOTES" or title in {"notes", "endnotes", "注", "註"}:
        return "notes"
    return "chapter"


def _page_locator(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, str) or not value.strip():
        return []
    match = re.fullmatch(
        r"\s*([A-Za-z0-9]+)(?:\s*[-\u2012-\u2015]\s*([A-Za-z0-9]+))?\s*",
        unicodedata.normalize("NFKC", value),
    )
    if not match:
        return []
    locator = {"scheme": "printed-page", "start": match.group(1)}
    if match.group(2):
        locator["end"] = match.group(2)
    return [locator]


def _source_order_locator(value: str) -> dict[str, str]:
    return {"scheme": "outline-order", "start": value}


def _diagnostic(code: str, severity: str, message: str, unit_id: str = "") -> dict[str, str]:
    result = {"code": code, "severity": severity, "message": message}
    if unit_id:
        result["unit_id"] = unit_id
    return result


def _computed_plan_id(plan: Mapping[str, Any]) -> str:
    body = {key: value for key, value in plan.items() if key != "plan_id"}
    encoded = json.dumps(
        body, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return f"IP-{hashlib.sha256(encoded).hexdigest()[:20].upper()}"


def _safe_extracted_chapters(extracted: ExtractedBook) -> list[dict[str, Any]]:
    """Reject the plain-text importer's prose fallback at the staging boundary."""

    chapters = [chapter for chapter in extracted.chapters if isinstance(chapter, dict)]
    if extracted.source_format != "text":
        return chapters
    result = []
    for chapter in chapters:
        title = _safe_heading(chapter.get("title"))
        if title and _PLAIN_HEADING.match(unicodedata.normalize("NFKC", title)):
            result.append(chapter)
    return result


def _build_units(extracted: ExtractedBook, source_hash: str) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    diagnostics: list[dict[str, str]] = []
    title = _safe_heading(extracted.title)
    book_id = _unit_id(
        kind="book", parent_id=None, title="document-root", occurrence=1
    )
    confidence = _confidence(extracted.source_format)
    units: list[dict[str, Any]] = [
        {
            "staging_id": book_id,
            "kind": "book",
            "parent_id": None,
            "order": 0,
            "title": title,
            "source_key": "",
            "locators": [{"scheme": "document", "start": "0"}],
            "extraction_confidence": deepcopy(confidence),
            "provenance": _provenance(source_hash, extracted.source_format, "metadata"),
        }
    ]
    extracted_chapters = _safe_extracted_chapters(extracted)
    if len(extracted_chapters) + 1 > MAX_PLAN_UNITS:
        raise ReadingPackError(
            f"import plan would exceed {MAX_PLAN_UNITS} units", EXIT_IO
        )
    occurrence_by_key: dict[tuple[str, str], int] = {}
    top_level_by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for position, chapter in enumerate(extracted_chapters, start=1):
        chapter_title = _safe_heading(chapter.get("title"))
        if not chapter_title:
            diagnostics.append(
                _diagnostic(
                    "RPIP103", "warning", "an empty or unsafe heading candidate was omitted"
                )
            )
            continue
        kind = _classify(chapter)
        key = (kind, _normalized(chapter_title))
        occurrence_by_key[key] = occurrence_by_key.get(key, 0) + 1
        staging_id = _unit_id(
            kind=kind,
            parent_id=book_id,
            title=chapter_title,
            occurrence=occurrence_by_key[key],
        )
        locators = [_source_order_locator(str(position))]
        locators.extend(_page_locator(chapter.get("pages", "")))
        unit = {
            "staging_id": staging_id,
            "kind": kind,
            "parent_id": book_id,
            "order": position,
            "title": chapter_title,
            "source_key": _safe_heading(chapter.get("id")),
            "locators": locators,
            "extraction_confidence": deepcopy(confidence),
            "provenance": _provenance(source_hash, extracted.source_format, "heading"),
        }
        units.append(unit)
        top_level_by_key.setdefault(key, []).append(unit)

        raw_sections = chapter.get("sections", [])
        if not isinstance(raw_sections, list):
            raise ReadingPackError("extracted chapter sections must be an array")
        if len(raw_sections) > MAX_PLAN_UNITS:
            raise ReadingPackError(
                f"import plan section candidates exceed {MAX_PLAN_UNITS}", EXIT_IO
            )
        section_occurrences: dict[str, int] = {}
        for section_position, raw_section in enumerate(raw_sections, start=1):
            section_title = _safe_heading(raw_section)
            if not section_title:
                diagnostics.append(
                    _diagnostic(
                        "RPIP103",
                        "warning",
                        "an empty or unsafe section candidate was omitted",
                        staging_id,
                    )
                )
                continue
            section_key = _normalized(section_title)
            section_occurrences[section_key] = section_occurrences.get(section_key, 0) + 1
            section_id = _unit_id(
                kind="section",
                parent_id=staging_id,
                title=section_title,
                occurrence=section_occurrences[section_key],
            )
            if len(units) >= MAX_PLAN_UNITS:
                raise ReadingPackError(
                    f"import plan would exceed {MAX_PLAN_UNITS} units", EXIT_IO
                )
            units.append(
                {
                    "staging_id": section_id,
                    "kind": "section",
                    "parent_id": staging_id,
                    "order": section_position,
                    "title": section_title,
                    "source_key": "",
                    "locators": [
                        _source_order_locator(f"{position}.{section_position}")
                    ],
                    "extraction_confidence": deepcopy(confidence),
                    "provenance": _provenance(
                        source_hash, extracted.source_format, "heading"
                    ),
                }
            )

    for duplicate_units in top_level_by_key.values():
        if len(duplicate_units) < 2:
            continue
        for unit in duplicate_units:
            unit["extraction_confidence"] = {
                "level": "conflict",
                "reasons": ["another top-level unit has the same kind and normalized title"],
            }
            diagnostics.append(
                _diagnostic(
                    "RPIP201",
                    "error",
                    "duplicate top-level heading requires manual reconciliation",
                    unit["staging_id"],
                )
            )
    return units, diagnostics


def create_import_plan(source: Path, explicit_format: str | None = None) -> dict[str, Any]:
    """Extract a source into a deterministic, body-free staging plan."""

    source = Path(source).resolve()
    before = _fingerprint(source)
    extracted = extract(source, explicit_format)
    after = _fingerprint(source)
    if before != after:
        raise ReadingPackError("source changed during import planning", EXIT_IO)
    source_record = {
        "id": SOURCE_ID,
        **before,
        "format": extracted.source_format,
    }
    units, diagnostics = _build_units(extracted, before["sha256"])
    metadata_candidates: list[dict[str, Any]] = []
    title = _safe_heading(extracted.title)
    if title and title.lower() != "untitled book":
        metadata_candidates.append(
            {
                "field": "title",
                "value": title,
                "decision": "unresolved",
                "extraction_confidence": _confidence(extracted.source_format),
                "provenance": _provenance(
                    before["sha256"], extracted.source_format, "metadata"
                ),
            }
        )
        diagnostics.append(
            _diagnostic(
                "RPIP001",
                "info",
                "metadata candidates are advisory and are never applied automatically",
            )
        )

    navigable = [unit for unit in units if unit["kind"] not in {"book", "section"}]
    if not navigable:
        outcome = "blocked"
        diagnostics.append(
            _diagnostic(
                "RPIP200",
                "error",
                "no unambiguous top-level units were extracted; canonical data cannot be changed",
            )
        )
    elif any(item["severity"] == "error" for item in diagnostics):
        outcome = "blocked"
    elif extracted.source_format in {"pdf", "pdf-vertical", "text"}:
        outcome = "review_required"
        diagnostics.append(
            _diagnostic(
                "RPIP100",
                "warning",
                "heuristic structure must be reviewed before applying this plan",
            )
        )
    else:
        outcome = "ready"

    plan_body = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "source": source_record,
        "metadata_candidates": metadata_candidates,
        "units": units,
        "diagnostics": diagnostics,
        "outcome": outcome,
    }
    plan = {"plan_id": "", **plan_body}
    plan["plan_id"] = _computed_plan_id(plan)
    validate_import_plan(plan)
    return plan


def apply_manual_outline(
    plan: Mapping[str, Any], sidecar: Mapping[str, Any]
) -> dict[str, Any]:
    """Replace extracted navigation with a review-attributed, body-free outline.

    This is the recovery path for scans, complex layout, or a blocked heuristic
    extraction.  It cannot carry summaries, terms, approval states, or body
    text, and it remains ``review_required`` until an explicit apply command.
    """

    validate_import_plan(plan)
    if (
        isinstance(sidecar, Mapping)
        and sidecar.get("source_sha256") != plan["source"]["sha256"]
    ):
        raise ReadingPackError("manual outline source hash does not match the import plan")
    require_structure("manual-outline.schema.json", sidecar, label="manual outline")
    if not isinstance(sidecar, Mapping):
        raise ReadingPackError("invalid manual outline: root must be an object")
    _require_keys(
        sidecar,
        {"format_version", "source_sha256", "reviewer", "reason", "chapters"},
        {"format_version", "source_sha256", "reviewer", "reason", "chapters"},
        "manual_outline",
    )
    if sidecar["format_version"] != 1:
        raise ReadingPackError("invalid manual outline: format_version must equal 1")
    reviewer = _safe_heading(sidecar["reviewer"])
    reason = _safe_heading(sidecar["reason"])
    if not reviewer or not reason:
        raise ReadingPackError("manual outline requires a safe reviewer and reason")
    chapters = sidecar["chapters"]
    if not isinstance(chapters, list) or not chapters:
        raise ReadingPackError("manual outline chapters must be a non-empty array")
    if len(chapters) > MAX_MANUAL_CHAPTERS:
        raise ReadingPackError(
            f"manual outline exceeds {MAX_MANUAL_CHAPTERS} chapters"
        )

    source_hash = plan["source"]["sha256"]
    book = next(unit for unit in plan["units"] if unit["kind"] == "book")
    book = deepcopy(book)
    units = [book]
    used_source_keys: set[str] = set()
    occurrences: dict[tuple[str, str], int] = {}
    method = f"manual-outline:{reviewer}"
    manual_provenance = [
        {
            "source_id": SOURCE_ID,
            "source_sha256": source_hash,
            "method": method,
        }
    ]
    confidence = {
        "level": "high",
        "reasons": ["supplied in a source-checked manual outline sidecar"],
    }
    total_sections = 0
    for position, chapter in enumerate(chapters, start=1):
        path = f"manual_outline.chapters[{position - 1}]"
        if not isinstance(chapter, Mapping):
            raise ReadingPackError(f"invalid manual outline: {path} must be an object")
        _require_keys(
            chapter,
            {"source_key", "kind", "title", "pages", "sections"},
            {"source_key", "kind", "title", "pages", "sections"},
            path,
        )
        source_key = _safe_heading(chapter["source_key"])
        kind = chapter["kind"]
        title = _safe_heading(chapter["title"])
        if not source_key or source_key in used_source_keys:
            raise ReadingPackError(
                f"invalid manual outline: {path}.source_key must be safe and unique"
            )
        used_source_keys.add(source_key)
        if kind not in UNIT_KINDS - {"book", "section"}:
            raise ReadingPackError(f"invalid manual outline: {path}.kind is unsupported")
        if not title:
            raise ReadingPackError(f"invalid manual outline: {path}.title is invalid")
        pages = chapter["pages"]
        if (
            not isinstance(pages, str)
            or len(pages) > 100
            or (pages and not _page_locator(pages))
        ):
            raise ReadingPackError(f"invalid manual outline: {path}.pages is invalid")
        sections = chapter["sections"]
        if not isinstance(sections, list) or not all(_safe_heading(item) for item in sections):
            raise ReadingPackError(f"invalid manual outline: {path}.sections is invalid")
        total_sections += len(sections)
        if total_sections > MAX_MANUAL_SECTIONS:
            raise ReadingPackError(
                f"invalid manual outline: total sections exceed {MAX_MANUAL_SECTIONS}"
            )
        if 1 + position + total_sections > MAX_PLAN_UNITS:
            raise ReadingPackError(
                f"manual outline would exceed {MAX_PLAN_UNITS} units"
            )

        key = (kind, _normalized(title))
        occurrences[key] = occurrences.get(key, 0) + 1
        chapter_id = _unit_id(
            kind=kind,
            parent_id=book["staging_id"],
            title=title,
            occurrence=occurrences[key],
            source_key=source_key,
        )
        locators = [_source_order_locator(str(position)), *_page_locator(pages)]
        units.append(
            {
                "staging_id": chapter_id,
                "kind": kind,
                "parent_id": book["staging_id"],
                "order": position,
                "title": title,
                "source_key": source_key,
                "locators": locators,
                "extraction_confidence": deepcopy(confidence),
                "provenance": deepcopy(manual_provenance),
            }
        )
        section_occurrences: dict[str, int] = {}
        for section_position, raw_section in enumerate(sections, start=1):
            section_title = _safe_heading(raw_section)
            normalized = _normalized(section_title)
            section_occurrences[normalized] = section_occurrences.get(normalized, 0) + 1
            section_id = _unit_id(
                kind="section",
                parent_id=chapter_id,
                title=section_title,
                occurrence=section_occurrences[normalized],
            )
            units.append(
                {
                    "staging_id": section_id,
                    "kind": "section",
                    "parent_id": chapter_id,
                    "order": section_position,
                    "title": section_title,
                    "source_key": "",
                    "locators": [_source_order_locator(f"{position}.{section_position}")],
                    "extraction_confidence": deepcopy(confidence),
                    "provenance": deepcopy(manual_provenance),
                }
            )

    reviewed_body = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "source": deepcopy(plan["source"]),
        "metadata_candidates": deepcopy(plan["metadata_candidates"]),
        "units": units,
        "diagnostics": [
            _diagnostic(
                "RPIP002",
                "info",
                f"manual outline supplied by {reviewer}; reason: {reason}",
            ),
            _diagnostic(
                "RPIP100",
                "warning",
                "manual outline must be reviewed before applying this plan",
            ),
        ],
        "outcome": "review_required",
    }
    reviewed = {"plan_id": "", **reviewed_body}
    reviewed["plan_id"] = _computed_plan_id(reviewed)
    validate_import_plan(reviewed)
    return reviewed


def _load_json_bounded(path: Path, label: str) -> Any:
    source = Path(path)
    try:
        with source.open("rb") as handle:
            encoded = handle.read(MAX_PLAN_BYTES + 1)
        if len(encoded) > MAX_PLAN_BYTES:
            raise ReadingPackError(
                f"{label} exceeds {MAX_PLAN_BYTES} bytes", EXIT_IO
            )
        value = json.loads(encoded.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReadingPackError(f"cannot read {label} {path}: {exc}", EXIT_IO) from exc
    return value


def load_manual_outline(path: Path) -> dict[str, Any]:
    value = _load_json_bounded(path, "manual outline")
    require_structure("manual-outline.schema.json", value, label="manual outline")
    return value


def _require_keys(value: Mapping[str, Any], required: set[str], allowed: set[str], path: str) -> None:
    missing = required - set(value)
    extra = set(value) - allowed
    if missing:
        raise ReadingPackError(f"invalid import plan: {path} is missing {sorted(missing)}")
    if extra:
        raise ReadingPackError(f"invalid import plan: {path} has unexpected fields {sorted(extra)}")


def _validate_confidence(value: Any, path: str) -> None:
    if not isinstance(value, dict):
        raise ReadingPackError(f"invalid import plan: {path} must be an object")
    _require_keys(value, {"level", "reasons"}, {"level", "reasons"}, path)
    if value["level"] not in CONFIDENCE_LEVELS:
        raise ReadingPackError(f"invalid import plan: {path}.level is unsupported")
    if (
        not isinstance(value["reasons"], list)
        or not value["reasons"]
        or len(value["reasons"]) > MAX_CONFIDENCE_REASONS
        or not all(
            isinstance(item, str) and 0 < len(item) <= MAX_HEADING_CHARACTERS
            for item in value["reasons"]
        )
    ):
        raise ReadingPackError(f"invalid import plan: {path}.reasons is invalid")


def _validate_provenance(value: Any, source_hash: str, path: str) -> None:
    if (
        not isinstance(value, list)
        or not value
        or len(value) > MAX_PROVENANCE_RECORDS
    ):
        raise ReadingPackError(f"invalid import plan: {path} must be a non-empty array")
    for index, record in enumerate(value):
        if not isinstance(record, dict):
            raise ReadingPackError(f"invalid import plan: {path}[{index}] must be an object")
        _require_keys(
            record,
            {"source_id", "source_sha256", "method"},
            {"source_id", "source_sha256", "method"},
            f"{path}[{index}]",
        )
        if record["source_id"] != SOURCE_ID or record["source_sha256"] != source_hash:
            raise ReadingPackError(f"invalid import plan: {path}[{index}] source does not match")
        if (
            not isinstance(record["method"], str)
            or not record["method"]
            or len(record["method"]) > MAX_HEADING_CHARACTERS
        ):
            raise ReadingPackError(f"invalid import plan: {path}[{index}].method is invalid")


def validate_import_plan(plan: Mapping[str, Any]) -> None:
    """Validate an import plan and reject fields capable of smuggling canonical prose."""

    require_structure("import-plan.schema.json", plan, label="import plan")
    if not isinstance(plan, Mapping):
        raise ReadingPackError("invalid import plan: root must be an object")
    _require_keys(
        plan,
        {
            "schema_version",
            "plan_id",
            "source",
            "metadata_candidates",
            "units",
            "diagnostics",
            "outcome",
        },
        {
            "schema_version",
            "plan_id",
            "source",
            "metadata_candidates",
            "units",
            "diagnostics",
            "outcome",
        },
        "root",
    )
    if plan["schema_version"] != PLAN_SCHEMA_VERSION:
        raise ReadingPackError("invalid import plan: unsupported schema_version")
    if not isinstance(plan["plan_id"], str) or not re.fullmatch(r"IP-[0-9A-F]{20}", plan["plan_id"]):
        raise ReadingPackError("invalid import plan: plan_id is invalid")
    source = plan["source"]
    if not isinstance(source, dict):
        raise ReadingPackError("invalid import plan: source must be an object")
    _require_keys(
        source,
        {"id", "name", "sha256", "size_bytes", "format"},
        {"id", "name", "sha256", "size_bytes", "format"},
        "source",
    )
    if source["id"] != SOURCE_ID:
        raise ReadingPackError("invalid import plan: source.id is invalid")
    if (
        not isinstance(source["name"], str)
        or not source["name"]
        or Path(source["name"]).name != source["name"]
        or "\\" in source["name"]
        or _CONTROL.search(source["name"])
    ):
        raise ReadingPackError("invalid import plan: source.name must be a safe basename")
    if not isinstance(source["sha256"], str) or not _SHA256.fullmatch(source["sha256"]):
        raise ReadingPackError("invalid import plan: source.sha256 is invalid")
    if not isinstance(source["size_bytes"], int) or source["size_bytes"] < 0:
        raise ReadingPackError("invalid import plan: source.size_bytes is invalid")
    if source["format"] not in SOURCE_FORMATS:
        raise ReadingPackError("invalid import plan: source.format is unsupported")

    candidates = plan["metadata_candidates"]
    if not isinstance(candidates, list):
        raise ReadingPackError("invalid import plan: metadata_candidates must be an array")
    if len(candidates) > MAX_METADATA_CANDIDATES:
        raise ReadingPackError(
            f"invalid import plan: metadata_candidates exceed {MAX_METADATA_CANDIDATES}"
        )
    for index, candidate in enumerate(candidates):
        path = f"metadata_candidates[{index}]"
        if not isinstance(candidate, dict):
            raise ReadingPackError(f"invalid import plan: {path} must be an object")
        _require_keys(
            candidate,
            {"field", "value", "decision", "extraction_confidence", "provenance"},
            {"field", "value", "decision", "extraction_confidence", "provenance"},
            path,
        )
        if candidate["field"] not in {"title", "author", "publisher", "isbn"}:
            raise ReadingPackError(f"invalid import plan: {path}.field is unsupported")
        if not _safe_heading(candidate["value"]):
            raise ReadingPackError(f"invalid import plan: {path}.value is invalid")
        if candidate["decision"] not in {"unresolved", "accepted", "rejected"}:
            raise ReadingPackError(f"invalid import plan: {path}.decision is invalid")
        _validate_confidence(candidate["extraction_confidence"], f"{path}.extraction_confidence")
        _validate_provenance(candidate["provenance"], source["sha256"], f"{path}.provenance")

    units = plan["units"]
    if not isinstance(units, list) or not units:
        raise ReadingPackError("invalid import plan: units must be a non-empty array")
    if len(units) > MAX_PLAN_UNITS:
        raise ReadingPackError(f"invalid import plan: units exceed {MAX_PLAN_UNITS}")
    ids: set[str] = set()
    parents: dict[str, str | None] = {}
    book_ids: list[str] = []
    sibling_orders: set[tuple[str | None, int]] = set()
    for index, unit in enumerate(units):
        path = f"units[{index}]"
        if not isinstance(unit, dict):
            raise ReadingPackError(f"invalid import plan: {path} must be an object")
        _require_keys(
            unit,
            {
                "staging_id",
                "kind",
                "parent_id",
                "order",
                "title",
                "source_key",
                "locators",
                "extraction_confidence",
                "provenance",
            },
            {
                "staging_id",
                "kind",
                "parent_id",
                "order",
                "title",
                "source_key",
                "locators",
                "extraction_confidence",
                "provenance",
            },
            path,
        )
        staging_id = unit["staging_id"]
        if not isinstance(staging_id, str) or not re.fullmatch(r"U-[0-9A-F]{20}", staging_id):
            raise ReadingPackError(f"invalid import plan: {path}.staging_id is invalid")
        if staging_id in ids:
            raise ReadingPackError(f"invalid import plan: duplicate staging_id {staging_id}")
        ids.add(staging_id)
        if unit["kind"] not in UNIT_KINDS:
            raise ReadingPackError(f"invalid import plan: {path}.kind is unsupported")
        parent = unit["parent_id"]
        if parent is not None and not isinstance(parent, str):
            raise ReadingPackError(f"invalid import plan: {path}.parent_id is invalid")
        parents[staging_id] = parent
        if unit["kind"] == "book":
            book_ids.append(staging_id)
            if parent is not None:
                raise ReadingPackError(f"invalid import plan: {path} book must be a root")
        elif not _safe_heading(unit["title"]):
            raise ReadingPackError(f"invalid import plan: {path}.title is invalid")
        elif parent is None:
            raise ReadingPackError(f"invalid import plan: {path} must have a parent")
        if not isinstance(unit["title"], str) or len(unit["title"]) > MAX_HEADING_CHARACTERS:
            raise ReadingPackError(f"invalid import plan: {path}.title is invalid")
        if not isinstance(unit["source_key"], str) or len(unit["source_key"]) > 100:
            raise ReadingPackError(f"invalid import plan: {path}.source_key is invalid")
        if not isinstance(unit["order"], int) or unit["order"] < 0:
            raise ReadingPackError(f"invalid import plan: {path}.order is invalid")
        order_key = (parent, unit["order"])
        if order_key in sibling_orders:
            raise ReadingPackError(f"invalid import plan: duplicate sibling order at {path}")
        sibling_orders.add(order_key)
        if (
            not isinstance(unit["locators"], list)
            or not unit["locators"]
            or len(unit["locators"]) > MAX_LOCATORS
        ):
            raise ReadingPackError(f"invalid import plan: {path}.locators must be non-empty")
        for locator_index, locator in enumerate(unit["locators"]):
            locator_path = f"{path}.locators[{locator_index}]"
            if not isinstance(locator, dict):
                raise ReadingPackError(f"invalid import plan: {locator_path} must be an object")
            _require_keys(locator, {"scheme", "start"}, {"scheme", "start", "end"}, locator_path)
            if locator["scheme"] not in {"document", "outline-order", "printed-page"}:
                raise ReadingPackError(f"invalid import plan: {locator_path}.scheme is unsupported")
            if not isinstance(locator["start"], str) or not locator["start"]:
                raise ReadingPackError(f"invalid import plan: {locator_path}.start is invalid")
            if "end" in locator and (not isinstance(locator["end"], str) or not locator["end"]):
                raise ReadingPackError(f"invalid import plan: {locator_path}.end is invalid")
        _validate_confidence(unit["extraction_confidence"], f"{path}.extraction_confidence")
        _validate_provenance(unit["provenance"], source["sha256"], f"{path}.provenance")

    if len(book_ids) != 1:
        raise ReadingPackError("invalid import plan: exactly one book root is required")
    for staging_id, parent in parents.items():
        if parent is not None and parent not in ids:
            raise ReadingPackError(f"invalid import plan: unknown parent_id for {staging_id}")

    # One color pass detects cycles in O(units), including a maximally deep
    # but valid hierarchy.  Rewalking every ancestor from every node is O(n^2).
    colors: dict[str, int] = {}
    for staging_id in parents:
        cursor: str | None = staging_id
        trail: list[str] = []
        while cursor is not None:
            color = colors.get(cursor, 0)
            if color == 2:
                break
            if color == 1:
                raise ReadingPackError("invalid import plan: unit hierarchy contains a cycle")
            colors[cursor] = 1
            trail.append(cursor)
            cursor = parents.get(cursor)
        for visited in trail:
            colors[visited] = 2

    diagnostics = plan["diagnostics"]
    if not isinstance(diagnostics, list):
        raise ReadingPackError("invalid import plan: diagnostics must be an array")
    if len(diagnostics) > MAX_DIAGNOSTICS:
        raise ReadingPackError(
            f"invalid import plan: diagnostics exceed {MAX_DIAGNOSTICS}"
        )
    for index, item in enumerate(diagnostics):
        path = f"diagnostics[{index}]"
        if not isinstance(item, dict):
            raise ReadingPackError(f"invalid import plan: {path} must be an object")
        _require_keys(item, {"code", "severity", "message"}, {"code", "severity", "message", "unit_id"}, path)
        if not isinstance(item["code"], str) or not re.fullmatch(r"RPIP\d{3}", item["code"]):
            raise ReadingPackError(f"invalid import plan: {path}.code is invalid")
        if item["severity"] not in DIAGNOSTIC_SEVERITIES:
            raise ReadingPackError(f"invalid import plan: {path}.severity is invalid")
        if not isinstance(item["message"], str) or not item["message"] or len(item["message"]) > 1000:
            raise ReadingPackError(f"invalid import plan: {path}.message is invalid")
        if "unit_id" in item and item["unit_id"] not in ids:
            raise ReadingPackError(f"invalid import plan: {path}.unit_id is unknown")
    if plan["outcome"] not in OUTCOMES:
        raise ReadingPackError("invalid import plan: outcome is unsupported")
    has_conflict = any(
        unit["extraction_confidence"]["level"] == "conflict" for unit in units
    )
    has_error = any(item["severity"] == "error" for item in diagnostics)
    if (has_conflict or has_error) and plan["outcome"] != "blocked":
        raise ReadingPackError("invalid import plan: conflicts and errors require blocked outcome")
    if plan["plan_id"] != _computed_plan_id(plan):
        raise ReadingPackError("invalid import plan: plan_id checksum check failed")


def write_plan(path: Path, plan: Mapping[str, Any]) -> None:
    """Validate and atomically write a staging plan."""

    validate_import_plan(plan)
    encoded = json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    if len(encoded.encode("utf-8")) > MAX_PLAN_BYTES:
        raise ReadingPackError(f"import plan exceeds {MAX_PLAN_BYTES} bytes", EXIT_IO)
    atomic_write_text(Path(path), encoded)


def load_plan(path: Path) -> dict[str, Any]:
    """Load and validate a staging plan from disk."""

    value = _load_json_bounded(path, "import plan")
    validate_import_plan(value)
    return value


def _printed_pages(unit: Mapping[str, Any]) -> str:
    for locator in unit["locators"]:
        if locator["scheme"] != "printed-page":
            continue
        return locator["start"] + (f"-{locator['end']}" if locator.get("end") else "")
    return ""


def _planned_records(plan: Mapping[str, Any]) -> list[dict[str, Any]]:
    units = plan["units"]
    children: dict[str, list[Mapping[str, Any]]] = {}
    for unit in units:
        if unit["parent_id"] is not None:
            children.setdefault(unit["parent_id"], []).append(unit)
    book = next(unit for unit in units if unit["kind"] == "book")
    top_level = sorted(children.get(book["staging_id"], []), key=lambda value: value["order"])
    records = []
    for unit in top_level:
        if unit["kind"] == "section":
            raise ReadingPackError("import plan has a section directly under the book root")
        sections = sorted(children.get(unit["staging_id"], []), key=lambda value: value["order"])
        if any(section["kind"] != "section" for section in sections):
            raise ReadingPackError(
                "import plan hierarchy cannot be projected to canonical schema version 1"
            )
        if any(children.get(section["staging_id"]) for section in sections):
            raise ReadingPackError(
                "nested sections cannot be projected to canonical schema version 1"
            )
        records.append(
            {
                "unit": unit,
                "kind": unit["kind"],
                "title": unit["title"],
                "pages": _printed_pages(unit),
                "sections": [section["title"] for section in sections],
            }
        )
    return records


def _existing_kind(record: Mapping[str, Any]) -> str:
    recorded = record.get("kind")
    if recorded in UNIT_KINDS - {"book", "section"}:
        return str(recorded)
    return _classify(record)


def _next_chapter_id(occupied: set[str], start: int = 1) -> tuple[str, int]:
    number = start
    while True:
        candidate = f"CH-{number:02d}"
        number += 1
        if candidate not in occupied:
            return candidate, number


def _all_ids(data: Mapping[str, Any]) -> set[str]:
    result: set[str] = set()
    for value in data.values():
        if isinstance(value, list):
            for record in value:
                if isinstance(record, dict) and isinstance(record.get("id"), str):
                    result.add(record["id"])
    return result


def apply_import_plan(
    project: Path,
    plan: Mapping[str, Any],
    lang: str,
    expected_source: Path,
) -> dict[str, Any]:
    """Serialize and atomically merge a reviewed plan into canonical data."""

    project = Path(project).resolve()
    with project_lock(project):
        return _apply_import_plan_unlocked(project, plan, lang, expected_source)


def _apply_import_plan_unlocked(
    project: Path,
    plan: Mapping[str, Any],
    lang: str,
    expected_source: Path,
) -> dict[str, Any]:
    """Atomically merge a reviewed plan into canonical chapter data.

    ``expected_source`` is mandatory: its basename, byte size, and SHA-256 must
    still match the plan.  Metadata candidates are deliberately ignored.
    Existing chapter-owned editorial fields and all linked collections are
    retained.  A plan cannot introduce approval states because it contains no
    such fields.
    """

    validate_import_plan(plan)
    if plan["outcome"] == "blocked":
        raise ReadingPackError("blocked import plan cannot be applied")
    if any(item["severity"] == "error" for item in plan["diagnostics"]):
        raise ReadingPackError("import plan contains unresolved errors")
    if any(
        unit["extraction_confidence"]["level"] == "conflict" for unit in plan["units"]
    ):
        raise ReadingPackError("import plan contains unresolved conflicts")

    actual_source = _fingerprint(Path(expected_source))
    expected = plan["source"]
    if (
        actual_source["name"] != expected["name"]
        or actual_source["sha256"] != expected["sha256"]
        or actual_source["size_bytes"] != expected["size_bytes"]
    ):
        raise ReadingPackError(
            "source does not match the import plan; canonical data was not changed",
            EXIT_IO,
        )

    config = load_config(project)
    if lang not in config.get("languages", []):
        raise ReadingPackError(f"language is not configured: {lang}")
    data_path = project / "data" / f"pack.{lang}.json"
    try:
        data_before = data_path.stat()
    except OSError as exc:
        raise ReadingPackError(f"cannot read canonical data {data_path}: {exc}", EXIT_IO) from exc
    data = load_language_data(project, lang)
    planned = _planned_records(plan)
    if not planned:
        raise ReadingPackError("import plan has no top-level units")

    existing = data.get("chapters", [])
    if not isinstance(existing, list):
        raise ReadingPackError("canonical chapters must be an array")
    occupied = _all_ids(data)
    used_existing: set[str] = set()
    result: list[dict[str, Any]] = []
    next_number = 1

    primary_records: list[dict[str, Any]] | None = None
    primary_language = config["primary_language"]
    if lang != primary_language:
        primary = load_language_data(project, primary_language)
        primary_records = primary.get("chapters", [])
        if len(primary_records) != len(planned):
            raise ReadingPackError(
                "translated import plan count does not match the primary language"
            )
        if existing:
            existing_titles = [
                _normalized(str(record.get("title", ""))) for record in existing
            ]
            planned_titles = [_normalized(record["title"]) for record in planned]
            if existing_titles != planned_titles:
                raise ReadingPackError(
                    "translated import plan order/title no longer matches existing canonical chapters; explicit manual reconciliation is required"
                )

    for position, proposed in enumerate(planned):
        unit = proposed["unit"]
        matched: dict[str, Any] | None = None
        target_id = ""
        if primary_records is not None:
            target_id = primary_records[position]["id"]
            candidates = [record for record in existing if record.get("id") == target_id]
        else:
            suggested = unit.get("source_key", "")
            manual_mapping = any(
                str(item.get("method", "")).startswith("manual-outline:")
                for item in unit.get("provenance", [])
                if isinstance(item, Mapping)
            )
            if manual_mapping and _CHAPTER_ID.fullmatch(suggested):
                by_id = [record for record in existing if record.get("id") == suggested]
            else:
                by_id = []
            if by_id:
                candidates = by_id
            else:
                key = (unit["kind"], _normalized(proposed["title"]))
                candidates = [
                    record
                    for record in existing
                    if (_existing_kind(record), _normalized(str(record.get("title", "")))) == key
                ]
        if len(candidates) > 1:
            raise ReadingPackError(
                f"canonical match is ambiguous for staged unit {unit['staging_id']}"
            )
        if candidates:
            matched = candidates[0]
            if matched["id"] in used_existing:
                raise ReadingPackError("multiple staged units match one canonical chapter")
            used_existing.add(matched["id"])
            target_id = matched["id"]
        elif primary_records is None:
            suggested = unit.get("source_key", "")
            if _CHAPTER_ID.fullmatch(suggested) and suggested not in occupied:
                target_id = suggested
            else:
                target_id, next_number = _next_chapter_id(occupied, next_number)

        if not target_id or (target_id in occupied and matched is None):
            if primary_records is not None:
                raise ReadingPackError(f"primary chapter ID collides in translated data: {target_id}")
            target_id, next_number = _next_chapter_id(occupied, next_number)
        occupied.add(target_id)

        if matched is not None:
            record = deepcopy(matched)
            old_structure = (
                matched.get("title", ""),
                matched.get("pages", ""),
                matched.get("sections", []),
                _existing_kind(matched),
            )
            new_structure = (
                proposed["title"],
                proposed["pages"],
                proposed["sections"],
                proposed["kind"],
            )
            record.update(
                {
                    "id": target_id,
                    "kind": proposed["kind"],
                    "title": proposed["title"],
                    "pages": proposed["pages"],
                    "sections": proposed["sections"],
                }
            )
            if data.get("source", {}).get("sha256") != expected["sha256"] or old_structure != new_structure:
                record["status"] = "draft"
                if "translation_status" in record:
                    record["translation_status"] = "draft"
        else:
            record = {
                "id": target_id,
                "kind": proposed["kind"],
                "title": proposed["title"],
                "pages": proposed["pages"],
                "sections": proposed["sections"],
                "summary": "",
                "terms": [],
                "status": "draft",
            }
            if primary_records is not None:
                primary_record = primary_records[position]
                record.update(
                    {
                        "source_id": target_id,
                        "source_hash": semantic_hash(primary_record),
                        "translation_status": "draft",
                    }
                )
        result.append(record)

    unmatched = [record["id"] for record in existing if record.get("id") not in used_existing]
    if unmatched:
        raise ReadingPackError(
            "existing canonical chapters were not matched; manual reconciliation is required: "
            + ", ".join(unmatched)
        )

    source_now = _fingerprint(Path(expected_source))
    if source_now != actual_source:
        raise ReadingPackError(
            "source changed while applying the import plan; canonical data was not changed",
            EXIT_IO,
        )

    updated = deepcopy(data)
    updated["source"] = {
        "format": expected["format"],
        "name": expected["name"],
        "sha256": expected["sha256"],
    }
    updated["chapters"] = result
    # The project configuration remains the metadata authority.  In particular,
    # a PDF or EPUB title candidate never overwrites data.book here.
    try:
        data_now = data_path.stat()
    except OSError as exc:
        raise ReadingPackError(f"cannot verify canonical data {data_path}: {exc}", EXIT_IO) from exc
    identity_before = (
        data_before.st_dev,
        data_before.st_ino,
        data_before.st_size,
        data_before.st_mtime_ns,
    )
    identity_now = (
        data_now.st_dev,
        data_now.st_ino,
        data_now.st_size,
        data_now.st_mtime_ns,
    )
    if identity_before != identity_now:
        raise ReadingPackError("canonical data changed while applying the plan", EXIT_IO)
    write_json(data_path, updated)
    return updated
