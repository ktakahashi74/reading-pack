"""Staged, provenance-preserving author input packages.

An Author Input Package declares, per module, whether canonical content is
provided by an authority, augmented with generated content, generated from the
book, or omitted.  Package application is deliberately split into a body-free
plan and a stale-state-checked apply step.  Supplied files are fingerprinted and
registered, but their local paths and prose are not copied into the public
state ledger.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from copy import deepcopy
from datetime import date
from pathlib import Path
from typing import Any, Mapping

from reading_pack.artifact_transaction import (
    ArtifactChange,
    apply_artifact_transaction,
    json_hash as _json_hash,
    recover_artifact_transaction,
)
from reading_pack.companion import companion_findings
from reading_pack.errors import EXIT_IO, ReadingPackError
from reading_pack.hashing import semantic_hash
from reading_pack.importers import read_regular_source_bytes
from reading_pack.project import (
    load_config,
    load_language_data,
    project_lock,
    write_json,
)
from reading_pack.schema_validation import require_structure
from reading_pack.source_registry import (
    REGISTRY_NAME,
    SOURCE_FORMATS,
    SOURCE_LANGUAGES,
    SOURCE_ROLES,
    fingerprint_source,
    infer_source_format,
    load_source_registry,
    validate_source_record,
    validate_source_registry,
)


MANIFEST_NAME = "author-input.json"
STATE_NAME = "author-input-state.json"
MANIFEST_SCHEMA_VERSION = 1
PLAN_SCHEMA_VERSION = 2
STATE_SCHEMA_VERSION = 1
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_PLAN_BYTES = 8 * 1024 * 1024
MAX_STATE_BYTES = 8 * 1024 * 1024
MAX_MODULE_BYTES = 16 * 1024 * 1024
MAX_MODULE_RECORDS = 20_000
MAX_HISTORY = 1_000

MODES = ("provided", "augment", "generate", "omit")
MODULES = (
    "chapters",
    "summaries",
    "chapter_terms",
    "certainty",
    "claims",
    "qa",
    "policy",
    "names",
    "glossary",
    "references",
)
LEGACY_MODULES = tuple(module for module in MODULES if module != "policy")
COLLECTION_MODULES = {
    "chapters": "chapters",
    "certainty": "certainty",
    "claims": "claims",
    "qa": "misreadings",
    "policy": "policies",
    "names": "names",
    "glossary": "glossary",
    "references": "references",
}
FIELD_MODULES = {
    "summaries": ("summary", ""),
    "chapter_terms": ("terms", []),
}
DEFAULT_SOURCE_ROLES = {
    "chapters": "author-data",
    "summaries": "author-data",
    "chapter_terms": "author-data",
    "certainty": "author-canon",
    "claims": "author-canon",
    "qa": "author-qa",
    "policy": "author-canon",
    "names": "author-data",
    "glossary": "author-data",
    "references": "bibliography",
}
AUTHORITY_TYPES = {"author", "editor", "publisher", "rights-holder"}
MODULE_FORMATS = {"json", "csv"}

_PACKAGE_ID = re.compile(r"AIP-[A-Z0-9][A-Z0-9.-]{0,99}")
_SOURCE_ID = re.compile(r"SRC-[A-Z0-9][A-Z0-9.-]{0,99}")
_SHA256 = re.compile(r"[a-f0-9]{64}")
_SAFE_LINE = re.compile(r"[^\x00-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]{1,500}")

_ALLOWED_RECORD_FIELDS = {
    "chapters": {
        "id", "kind", "title", "pages", "sections", "summary", "terms",
        "contributors", "aliases", "learning_objectives", "prerequisites",
        "spoiler_scope", "source_locations", "status", "review_notes",
    },
    "certainty": {
        "id", "label", "definition", "source_locations", "status",
        "review_notes",
    },
    "claims": {
        "id", "layer", "kind", "statement", "chapter_ids", "certainty_id",
        "falsifiability", "revision_conditions", "source_locations",
        "reader_note", "status", "review_notes",
    },
    "qa": {
        "id", "kind", "issue", "misreading", "response", "impact",
        "remaining_uncertainty", "chapter_ids", "claim_ids", "anchor",
        "source_locations", "status", "review_notes",
    },
    "policy": {
        "id", "kind", "statement", "source_locations", "status",
        "review_notes",
    },
    "names": {
        "id", "name", "aliases", "chapter_id", "book_context", "status",
        "source_locations", "review_notes",
    },
    "glossary": {
        "id", "term", "aliases", "chapter_id", "book_meaning", "status",
        "source_locations", "review_notes",
    },
    "references": {
        "id", "url", "label", "relation", "url_scope", "retrieval_policy",
        "source_locations", "status", "review_notes",
    },
    "summaries": {"chapter_id", "summary"},
    "chapter_terms": {"chapter_id", "terms"},
}

_LIST_FIELDS = {
    "chapters": {
        "sections", "terms", "contributors", "aliases", "learning_objectives",
        "prerequisites", "source_locations",
    },
    "certainty": {"source_locations"},
    "claims": {"chapter_ids", "source_locations"},
    "qa": {"chapter_ids", "claim_ids", "source_locations"},
    "policy": {"source_locations"},
    "names": {"aliases", "source_locations"},
    "glossary": {"aliases", "source_locations"},
    "references": {"source_locations"},
    "chapter_terms": {"terms"},
}

_REQUIRED_INPUT_FIELDS = {
    "chapters": {"id", "title", "sections", "summary", "terms"},
    "summaries": {"chapter_id", "summary"},
    "chapter_terms": {"chapter_id", "terms"},
    "certainty": {"id", "label", "definition"},
    "claims": {"id", "layer", "kind", "statement", "chapter_ids"},
    "qa": {"id", "response", "chapter_ids"},
    "policy": {"id", "kind", "statement"},
    "names": {"id", "name", "chapter_id"},
    "glossary": {"id", "term", "chapter_id"},
    "references": {"id", "url", "label"},
}

_CSV_FIELDS = {
    "chapters": (
        "id", "kind", "title", "pages", "sections", "summary", "terms",
        "contributors", "aliases", "learning_objectives", "prerequisites",
        "spoiler_scope", "source_locations",
    ),
    "summaries": ("chapter_id", "summary"),
    "chapter_terms": ("chapter_id", "terms"),
    "certainty": ("id", "label", "definition", "source_locations"),
    "claims": (
        "id", "layer", "kind", "statement", "chapter_ids", "certainty_id",
        "falsifiability", "revision_conditions", "source_locations",
        "reader_note",
    ),
    "qa": (
        "id", "kind", "issue", "response", "impact",
        "remaining_uncertainty", "chapter_ids", "claim_ids", "anchor",
        "source_locations",
    ),
    "policy": ("id", "kind", "statement", "source_locations"),
    "names": (
        "id", "name", "aliases", "chapter_id", "book_context",
        "source_locations",
    ),
    "glossary": (
        "id", "term", "aliases", "chapter_id", "book_meaning",
        "source_locations",
    ),
    "references": (
        "id", "url", "label", "relation", "url_scope", "retrieval_policy",
        "source_locations",
    ),
}

_LEGACY_REFERENCE_CSV_FIELDS = ("id", "url", "label")
_LEGACY_QA_CSV_FIELDS = (
    "id", "kind", "misreading", "response", "impact",
    "remaining_uncertainty", "chapter_ids", "claim_ids", "anchor",
)


def _strict_json_bytes(raw: bytes, label: str) -> Any:
    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate object key: {key}")
            result[key] = value
        return result

    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=object_pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON number: {value}")
            ),
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ReadingPackError(f"invalid {label}: {exc}") from exc


def _bounded_json(path: Path, maximum: int, label: str) -> Any:
    raw = read_regular_source_bytes(path, maximum=maximum)
    return _strict_json_bytes(raw, label)


def _require_keys(
    value: Mapping[str, Any], required: set[str], allowed: set[str], label: str
) -> None:
    missing = required - set(value)
    unexpected = set(value) - allowed
    if missing:
        raise ReadingPackError(
            f"invalid {label}: missing fields {', '.join(sorted(missing))}"
        )
    if unexpected:
        raise ReadingPackError(
            f"invalid {label}: unexpected fields {', '.join(sorted(unexpected))}"
        )


def _safe_name(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or not _SAFE_LINE.fullmatch(value)
        or Path(value).name != value
    ):
        raise ReadingPackError(f"invalid {label}: must be one safe filename")
    return value


def _validate_authority(value: Any, label: str) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise ReadingPackError(f"invalid {label}: must be an object")
    _require_keys(
        value,
        {"type", "name", "supplied_at"},
        {"type", "name", "supplied_at"},
        label,
    )
    if not isinstance(value["type"], str) or value["type"] not in AUTHORITY_TYPES:
        raise ReadingPackError(f"invalid {label}: unsupported authority type")
    if not isinstance(value["name"], str) or not _SAFE_LINE.fullmatch(value["name"]):
        raise ReadingPackError(f"invalid {label}: authority name is unsafe")
    if not isinstance(value["supplied_at"], str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}", value["supplied_at"]
    ):
        raise ReadingPackError(f"invalid {label}: supplied_at must use YYYY-MM-DD")
    try:
        date.fromisoformat(value["supplied_at"])
    except ValueError as exc:
        raise ReadingPackError(f"invalid {label}: supplied_at is not a date") from exc
    return dict(value)


def _manifest_path(package: Path) -> Path:
    package = Path(package).resolve()
    return package / MANIFEST_NAME if package.is_dir() else package


def _package_file(package_root: Path, name: str, label: str) -> Path:
    safe = _safe_name(name, label)
    path = (package_root / safe).resolve()
    if path.parent != package_root.resolve():
        raise ReadingPackError(f"invalid {label}: file must be a direct package child")
    return path


def validate_author_input_manifest(value: Any) -> dict[str, Any]:
    require_structure(
        "author-input-manifest.schema.json", value, label="author input manifest"
    )
    if not isinstance(value, Mapping):
        raise ReadingPackError("invalid author input manifest: root must be an object")
    required = {
        "schema_version", "package_id", "language", "authority", "modules",
        "attachments",
    }
    _require_keys(value, required, required, "author input manifest")
    if value["schema_version"] != MANIFEST_SCHEMA_VERSION:
        raise ReadingPackError("invalid author input manifest: unsupported schema version")
    if not isinstance(value["package_id"], str) or not _PACKAGE_ID.fullmatch(
        value["package_id"]
    ):
        raise ReadingPackError("invalid author input manifest: package_id is invalid")
    if (
        not isinstance(value["language"], str)
        or value["language"] not in SOURCE_LANGUAGES - {"und"}
    ):
        raise ReadingPackError("invalid author input manifest: language must be ja or en")

    authority = _validate_authority(value["authority"], "author input authority")

    modules = value["modules"]
    if not isinstance(modules, Mapping) or frozenset(modules) not in {
        frozenset(LEGACY_MODULES), frozenset(MODULES)
    }:
        raise ReadingPackError(
            "invalid author input manifest: modules must declare every supported module exactly once"
        )
    modules = {**dict(modules), "policy": modules.get("policy", {"mode": "generate"})}
    clean_modules: dict[str, dict[str, Any]] = {}
    for module in MODULES:
        raw = modules[module]
        if not isinstance(raw, Mapping):
            raise ReadingPackError(f"invalid author input manifest: {module} must be an object")
        mode = raw.get("mode")
        if mode not in MODES:
            raise ReadingPackError(
                f"invalid author input manifest: {module}.mode must be one of {MODES}"
            )
        if module == "chapters" and mode == "omit":
            raise ReadingPackError("invalid author input manifest: chapters cannot be omitted")
        if mode in {"provided", "augment"}:
            _require_keys(
                raw,
                {"mode", "file", "format", "source_id"},
                {"mode", "file", "format", "source_id", "role"},
                f"author input module {module}",
            )
            if not isinstance(raw["format"], str) or raw["format"] not in MODULE_FORMATS:
                raise ReadingPackError(
                    f"invalid author input manifest: {module}.format must be json or csv"
                )
            _safe_name(raw["file"], f"author input module {module}.file")
            if not isinstance(raw["source_id"], str) or not _SOURCE_ID.fullmatch(
                raw["source_id"]
            ):
                raise ReadingPackError(
                    f"invalid author input manifest: {module}.source_id is invalid"
                )
            role = raw.get("role", DEFAULT_SOURCE_ROLES[module])
            if not isinstance(role, str) or role not in SOURCE_ROLES or role == "primary-book":
                raise ReadingPackError(
                    f"invalid author input manifest: {module}.role is unsupported"
                )
            clean_modules[module] = {**dict(raw), "role": role}
        else:
            _require_keys(raw, {"mode"}, {"mode"}, f"author input module {module}")
            clean_modules[module] = {"mode": mode}

    attachments = value["attachments"]
    if not isinstance(attachments, list) or len(attachments) > 1_000:
        raise ReadingPackError(
            "invalid author input manifest: attachments must be a bounded array"
        )
    clean_attachments: list[dict[str, Any]] = []
    seen_ids = {
        item["source_id"]
        for item in clean_modules.values()
        if "source_id" in item
    }
    for index, raw in enumerate(attachments):
        label = f"author input attachment[{index}]"
        if not isinstance(raw, Mapping):
            raise ReadingPackError(f"invalid {label}: must be an object")
        _require_keys(
            raw,
            {"source_id", "role", "language", "format", "file"},
            {"source_id", "role", "language", "format", "file"},
            label,
        )
        source_id = raw["source_id"]
        if (
            not isinstance(source_id, str)
            or not _SOURCE_ID.fullmatch(source_id)
            or source_id in seen_ids
        ):
            raise ReadingPackError(f"invalid {label}: source_id must be safe and unique")
        seen_ids.add(source_id)
        if (
            not isinstance(raw["role"], str)
            or raw["role"] not in SOURCE_ROLES
            or raw["role"] == "primary-book"
        ):
            raise ReadingPackError(f"invalid {label}: unsupported role")
        if not isinstance(raw["language"], str) or raw["language"] not in SOURCE_LANGUAGES:
            raise ReadingPackError(f"invalid {label}: unsupported language")
        if not isinstance(raw["format"], str) or raw["format"] not in SOURCE_FORMATS:
            raise ReadingPackError(f"invalid {label}: unsupported format")
        _safe_name(raw["file"], f"{label}.file")
        clean_attachments.append(dict(raw))

    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "package_id": value["package_id"],
        "language": value["language"],
        "authority": authority,
        "modules": clean_modules,
        "attachments": clean_attachments,
    }


def _parse_json_module(raw: bytes, module: str) -> list[dict[str, Any]]:
    value = _strict_json_bytes(raw, f"author input {module} JSON")
    require_structure(
        "author-input-module.schema.json", value, label=f"author input {module}"
    )
    if not isinstance(value, Mapping):
        raise ReadingPackError(f"invalid author input {module}: root must be an object")
    _require_keys(
        value,
        {"schema_version", "module", "records"},
        {"schema_version", "module", "records"},
        f"author input {module}",
    )
    if value["schema_version"] != 1 or value["module"] != module:
        raise ReadingPackError(
            f"invalid author input {module}: schema_version or module does not match"
        )
    records = value["records"]
    if not isinstance(records, list) or len(records) > MAX_MODULE_RECORDS:
        raise ReadingPackError(
            f"invalid author input {module}: records must contain at most {MAX_MODULE_RECORDS} objects"
        )
    return _validate_input_records(module, records)


def _parse_csv_module(raw: bytes, module: str) -> list[dict[str, Any]]:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeError as exc:
        raise ReadingPackError(f"invalid author input {module}: CSV must be UTF-8") from exc
    try:
        reader = csv.DictReader(io.StringIO(text, newline=""))
        if reader.fieldnames is None:
            raise ReadingPackError(f"invalid author input {module}: CSV header is missing")
        expected = _CSV_FIELDS[module]
        accepted = {expected}
        without_locations = tuple(
            field for field in expected if field != "source_locations"
        )
        accepted.add(without_locations)
        if module == "references":
            accepted.add(_LEGACY_REFERENCE_CSV_FIELDS)
        elif module == "qa":
            accepted.add(_LEGACY_QA_CSV_FIELDS)
        actual_fields = tuple(reader.fieldnames)
        if actual_fields not in accepted:
            raise ReadingPackError(
                f"invalid author input {module}: CSV fields must be "
                + " or ".join(", ".join(fields) for fields in sorted(accepted))
            )
        records: list[dict[str, Any]] = []
        for index, row in enumerate(reader):
            if index >= MAX_MODULE_RECORDS:
                raise ReadingPackError(
                    f"invalid author input {module}: too many CSV records"
                )
            if None in row or any(row.get(key) is None for key in actual_fields):
                raise ReadingPackError(
                    f"invalid author input {module}: CSV row {index + 2} has the wrong number of columns"
                )
            record: dict[str, Any] = {}
            for key in actual_fields:
                value = row.get(key, "")
                if key in _LIST_FIELDS.get(module, set()):
                    record[key] = [item.strip() for item in value.split("|") if item.strip()]
                elif value != "":
                    record[key] = value.strip()
            records.append(record)
    except csv.Error as exc:
        raise ReadingPackError(f"invalid author input {module} CSV: {exc}") from exc
    return _validate_input_records(module, records)


def _validate_input_records(module: str, records: list[Any]) -> list[dict[str, Any]]:
    clean: list[dict[str, Any]] = []
    identifier_field = "chapter_id" if module in FIELD_MODULES else "id"
    seen: set[str] = set()
    for index, raw in enumerate(records):
        label = f"author input {module} records[{index}]"
        if not isinstance(raw, Mapping):
            raise ReadingPackError(f"invalid {label}: must be an object")
        unexpected = set(raw) - _ALLOWED_RECORD_FIELDS[module]
        if unexpected:
            raise ReadingPackError(
                f"invalid {label}: unexpected fields {', '.join(sorted(unexpected))}"
            )
        missing = _REQUIRED_INPUT_FIELDS[module] - set(raw)
        if missing:
            raise ReadingPackError(
                f"invalid {label}: missing fields {', '.join(sorted(missing))}"
            )
        if module == "qa" and (("issue" in raw) == ("misreading" in raw)):
            raise ReadingPackError(
                f"invalid {label}: exactly one of issue or legacy misreading is required"
            )
        identifier = raw.get(identifier_field)
        if not isinstance(identifier, str) or not identifier or identifier in seen:
            raise ReadingPackError(
                f"invalid {label}: {identifier_field} must be a non-empty unique string"
            )
        seen.add(identifier)
        record = deepcopy(dict(raw))
        for field in _LIST_FIELDS.get(module, set()):
            if field in record and (
                not isinstance(record[field], list)
                or not all(isinstance(item, str) and item for item in record[field])
                or len(record[field]) != len(set(record[field]))
            ):
                raise ReadingPackError(
                    f"invalid {label}: {field} must be a unique string array"
                )
        clean.append(record)
    if module == "references":
        findings = companion_findings(clean)
        if findings:
            finding = findings[0]
            location = (
                f"records[{finding.index}].{finding.field}"
                if finding.index is not None
                else finding.field
            )
            raise ReadingPackError(
                f"invalid author input references {location}: {finding.message}"
            )
    return clean


def load_author_input_package(package: Path) -> dict[str, Any]:
    manifest_path = _manifest_path(package)
    package_root = manifest_path.parent.resolve()
    raw_manifest = read_regular_source_bytes(
        manifest_path, maximum=MAX_MANIFEST_BYTES
    )
    manifest = validate_author_input_manifest(
        _strict_json_bytes(raw_manifest, "author input manifest")
    )
    modules: dict[str, dict[str, Any]] = {}
    sources: list[dict[str, Any]] = []
    for module in MODULES:
        declaration = manifest["modules"][module]
        if declaration["mode"] not in {"provided", "augment"}:
            modules[module] = {"declaration": declaration, "records": []}
            continue
        path = _package_file(
            package_root, declaration["file"], f"author input module {module}"
        )
        raw = read_regular_source_bytes(path, maximum=MAX_MODULE_BYTES)
        records = (
            _parse_json_module(raw, module)
            if declaration["format"] == "json"
            else _parse_csv_module(raw, module)
        )
        source = validate_source_record(
            {
                "id": declaration["source_id"],
                "role": declaration["role"],
                "language": manifest["language"],
                "format": declaration["format"],
                **fingerprint_source(path),
            },
            f"author input module {module} source",
        )
        modules[module] = {
            "declaration": declaration,
            "records": records,
            "source": source,
        }
        sources.append(source)

    attachments: list[dict[str, Any]] = []
    for index, declaration in enumerate(manifest["attachments"]):
        path = _package_file(
            package_root,
            declaration["file"],
            f"author input attachment[{index}]",
        )
        source = validate_source_record(
            {
                "id": declaration["source_id"],
                "role": declaration["role"],
                "language": declaration["language"],
                "format": infer_source_format(path, declaration["format"]),
                **fingerprint_source(path),
            },
            f"author input attachment[{index}] source",
        )
        attachments.append(source)
        sources.append(source)

    identities = [(item["name"], item["sha256"]) for item in sources]
    if len(identities) != len(set(identities)):
        raise ReadingPackError(
            "invalid author input package: the same source file is declared more than once"
        )
    return {
        "root": package_root,
        "manifest": manifest,
        "manifest_sha256": hashlib.sha256(raw_manifest).hexdigest(),
        "modules": modules,
        "attachments": attachments,
        "sources": sources,
    }


def _record_id(module: str, record: Mapping[str, Any]) -> str:
    return str(record["chapter_id"] if module in FIELD_MODULES else record["id"])


def _prepared_record(
    module: str,
    record: Mapping[str, Any],
    source: Mapping[str, Any],
) -> dict[str, Any]:
    value = deepcopy(dict(record))
    for field in (
        "status", "review_notes", "source_id", "source_hash",
        "translation_status", "provenance_source_id", "provenance_source_hash",
    ):
        value.pop(field, None)
    if module == "qa" and "misreading" in value:
        value["issue"] = value.pop("misreading")
    value["status"] = "draft"
    value["provenance_source_id"] = source["id"]
    value["provenance_source_hash"] = source["sha256"]
    if module in {"names", "glossary"} and not value.get("aliases"):
        value.pop("aliases", None)
    return value


def _link_translation_records(
    config: Mapping[str, Any],
    language: str,
    collection: str,
    records: list[dict[str, Any]],
    changed_ids: set[str],
    primary: Mapping[str, Any],
) -> None:
    primary_language = config["primary_language"]
    if language == primary_language:
        return
    primary_by_id = {
        record["id"]: record for record in primary.get(collection, [])
    }
    for record in records:
        if record["id"] not in changed_ids:
            continue
        source = primary_by_id.get(record["id"])
        if source is None:
            continue
        record["source_id"] = source["id"]
        record["source_hash"] = semantic_hash(source)
        record["translation_status"] = "draft"


def _apply_package_to_data(
    config: Mapping[str, Any],
    current: Mapping[str, Any],
    package: Mapping[str, Any],
    primary: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    data = deepcopy(dict(current))
    data.setdefault("policies", [])
    summaries: dict[str, dict[str, Any]] = {}
    language = package["manifest"]["language"]

    for module in MODULES:
        item = package["modules"][module]
        mode = item["declaration"]["mode"]
        if module in COLLECTION_MODULES:
            collection = COLLECTION_MODULES[module]
            before = deepcopy(data[collection])
            before_by_id = {record["id"]: record for record in before}
            source = item.get("source")
            provided = (
                [_prepared_record(module, record, source) for record in item["records"]]
                if source is not None
                else []
            )
            provided_ids = [record["id"] for record in provided]
            provided_set = set(provided_ids)
            if mode == "provided":
                after = provided
            elif mode == "augment":
                provided_by_id = {record["id"]: record for record in provided}
                after = [
                    provided_by_id.get(record["id"], deepcopy(record))
                    for record in before
                ]
                after.extend(
                    record for record in provided if record["id"] not in before_by_id
                )
            elif mode == "omit":
                after = []
            else:
                after = before
            changed_ids = {
                record["id"]
                for record in after
                if record["id"] in provided_set
            }
            _link_translation_records(
                config, language, collection, after, changed_ids, primary
            )
            data[collection] = after
            after_ids = [record["id"] for record in after]
            before_ids = [record["id"] for record in before]
            summaries[module] = {
                "mode": mode,
                "source": deepcopy(source) if source is not None else None,
                "provided_record_ids": provided_ids,
                "provided_record_hashes": {
                    record["id"]: semantic_hash(record) for record in provided
                },
                "added_ids": [item for item in provided_ids if item not in before_by_id],
                "replaced_ids": [item for item in provided_ids if item in before_by_id],
                "removed_ids": [item for item in before_ids if item not in set(after_ids)],
                "preserved_ids": [
                    item for item in after_ids if item in before_by_id and item not in provided_set
                ],
                "canonical_count_after": len(after_ids),
            }
            continue

        field, empty = FIELD_MODULES[module]
        before_chapters = deepcopy(data["chapters"])
        by_id = {chapter["id"]: chapter for chapter in data["chapters"]}
        provided_ids = [_record_id(module, record) for record in item["records"]]
        source = item.get("source")
        if mode == "provided":
            for chapter in data["chapters"]:
                chapter[field] = deepcopy(empty)
                chapter["status"] = "draft"
        elif mode == "omit":
            for chapter in data["chapters"]:
                chapter[field] = deepcopy(empty)
                chapter["status"] = "draft"
        if mode in {"provided", "augment"}:
            for record in item["records"]:
                chapter = by_id.get(record["chapter_id"])
                if chapter is None:
                    raise ReadingPackError(
                        f"author input {module} references unknown chapter {record['chapter_id']}"
                    )
                chapter[field] = deepcopy(record[field])
                chapter["status"] = "draft"
                if language != config["primary_language"]:
                    primary_by_id = {record["id"]: record for record in primary["chapters"]}
                    primary_record = primary_by_id.get(chapter["id"])
                    if primary_record is not None:
                        chapter["source_id"] = primary_record["id"]
                        chapter["source_hash"] = semantic_hash(primary_record)
                        chapter["translation_status"] = "draft"
        changed = [
            chapter["id"]
            for chapter, old in zip(data["chapters"], before_chapters, strict=True)
            if chapter.get(field) != old.get(field)
        ]
        summaries[module] = {
            "mode": mode,
            "source": deepcopy(source) if source is not None else None,
            "provided_record_ids": provided_ids,
            "provided_record_hashes": {
                record["chapter_id"]: semantic_hash(
                    {"chapter_id": record["chapter_id"], field: record[field]}
                )
                for record in item["records"]
            },
            "added_ids": [],
            "replaced_ids": [item for item in provided_ids if item in by_id],
            "removed_ids": [
                chapter["id"]
                for chapter in before_chapters
                if chapter.get(field) != empty and chapter["id"] not in provided_ids
                and mode in {"provided", "omit"}
            ],
            "preserved_ids": [
                chapter["id"]
                for chapter in data["chapters"]
                if chapter["id"] not in changed and chapter.get(field) != empty
            ],
            "canonical_count_after": sum(
                chapter.get(field) != empty for chapter in data["chapters"]
            ),
        }
    return data, summaries


def _plan_id(value: Mapping[str, Any]) -> str:
    body = {key: item for key, item in value.items() if key != "plan_id"}
    return f"AIPLAN-{_json_hash(body)[:20].upper()}"


def _package_paths(value: Path | list[Path] | tuple[Path, ...]) -> list[Path]:
    if isinstance(value, (str, Path)):
        return [Path(value)]
    paths = [Path(item) for item in value]
    if not paths:
        raise ReadingPackError("at least one Author Input Package is required")
    return paths


def _load_author_input_packages(
    config: Mapping[str, Any],
    package_paths: Path | list[Path] | tuple[Path, ...],
) -> list[dict[str, Any]]:
    packages = [load_author_input_package(path) for path in _package_paths(package_paths)]
    configured = list(config.get("languages", []))
    languages = [package["manifest"]["language"] for package in packages]
    package_ids = [package["manifest"]["package_id"] for package in packages]
    if len(languages) != len(set(languages)):
        raise ReadingPackError("author input packages must use unique languages")
    if len(package_ids) != len(set(package_ids)):
        raise ReadingPackError("author input packages must use unique package IDs")
    unknown = set(languages) - set(configured)
    if unknown:
        raise ReadingPackError(
            f"author input language is not configured: {', '.join(sorted(unknown))}"
        )
    source_ids = [source["id"] for package in packages for source in package["sources"]]
    if len(source_ids) != len(set(source_ids)):
        raise ReadingPackError("author input packages must use unique source IDs")
    identities = [
        (source["name"], source["sha256"])
        for package in packages
        for source in package["sources"]
    ]
    if len(identities) != len(set(identities)):
        raise ReadingPackError(
            "author input packages declare the same source file more than once"
        )
    primary = config["primary_language"]
    order = [primary, *[language for language in configured if language != primary]]
    return sorted(packages, key=lambda package: order.index(package["manifest"]["language"]))


def _prospective_application(
    project: Path,
    config: Mapping[str, Any],
    packages: list[Mapping[str, Any]],
) -> tuple[
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, dict[str, dict[str, Any]]],
]:
    configured = list(config.get("languages", []))
    current = {
        language: load_language_data(project, language) for language in configured
    }
    prospective = deepcopy(current)
    summaries: dict[str, dict[str, dict[str, Any]]] = {}
    primary_language = config["primary_language"]
    for package in packages:
        language = package["manifest"]["language"]
        updated, language_summaries = _apply_package_to_data(
            config,
            prospective[language],
            package,
            prospective[primary_language],
        )
        prospective[language] = updated
        summaries[language] = language_summaries
    return current, prospective, summaries


def _build_author_input_plan(
    project: Path,
    config: Mapping[str, Any],
    packages: list[Mapping[str, Any]],
) -> tuple[
    dict[str, Any],
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, dict[str, dict[str, Any]]],
]:
    load_author_input_state(project, config)
    current, prospective, summaries = _prospective_application(
        project, config, packages
    )
    from reading_pack.validation import errors, validate_data_set

    issues = validate_data_set(dict(config), prospective)
    fatal = errors(issues)
    if fatal:
        first = fatal[0]
        raise ReadingPackError(
            f"author input would create invalid project data: {first.code} {first.path}: {first.message}"
        )
    # Reserve every declared source ID during planning. A package revision uses
    # a new ID so prior provenance never changes meaning retroactively.
    _prospective_registry(project, packages)
    plan = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "plan_id": "",
        "packages": [
            {
                "package_id": package["manifest"]["package_id"],
                "language": package["manifest"]["language"],
                "manifest_sha256": package["manifest_sha256"],
                "authority": deepcopy(package["manifest"]["authority"]),
            }
            for package in packages
        ],
        "base_canonical_sha256": _json_hash(current),
        "prospective_canonical_sha256": _json_hash(prospective),
        "languages": {
            package["manifest"]["language"]: {
                "modules": summaries[package["manifest"]["language"]]
            }
            for package in packages
        },
        "attachments": [
            deepcopy(source)
            for package in packages
            for source in package["attachments"]
        ],
    }
    plan["plan_id"] = _plan_id(plan)
    return validate_author_input_plan(plan), current, prospective, summaries


def create_author_input_plan(
    project: Path,
    package_paths: Path | list[Path] | tuple[Path, ...],
) -> dict[str, Any]:
    project = Path(project).resolve()
    config = load_config(project)
    packages = _load_author_input_packages(config, package_paths)
    plan, _, _, _ = _build_author_input_plan(project, config, packages)
    return plan


def validate_author_input_plan(value: Any) -> dict[str, Any]:
    require_structure("author-input-plan.schema.json", value, label="author input plan")
    if not isinstance(value, Mapping):
        raise ReadingPackError("invalid author input plan: root must be an object")
    required = {
        "schema_version", "plan_id", "packages", "base_canonical_sha256",
        "prospective_canonical_sha256", "languages", "attachments",
    }
    _require_keys(value, required, required, "author input plan")
    if value["schema_version"] != PLAN_SCHEMA_VERSION:
        raise ReadingPackError("invalid author input plan: unsupported schema version")
    if not isinstance(value["plan_id"], str) or not re.fullmatch(
        r"AIPLAN-[A-F0-9]{20}", value["plan_id"]
    ):
        raise ReadingPackError("invalid author input plan: plan_id is invalid")
    if value["plan_id"] != _plan_id(value):
        raise ReadingPackError("author input plan checksum does not match its contents")
    packages = value["packages"]
    if not isinstance(packages, list) or not packages:
        raise ReadingPackError("invalid author input plan: packages must be a non-empty array")
    package_ids: list[str] = []
    package_languages: list[str] = []
    for index, package in enumerate(packages):
        label = f"author input plan packages[{index}]"
        if not isinstance(package, Mapping):
            raise ReadingPackError(f"invalid {label}: must be an object")
        _require_keys(
            package,
            {"package_id", "language", "manifest_sha256", "authority"},
            {"package_id", "language", "manifest_sha256", "authority"},
            label,
        )
        if not isinstance(package["package_id"], str) or not _PACKAGE_ID.fullmatch(
            package["package_id"]
        ):
            raise ReadingPackError(f"invalid {label}: package_id is invalid")
        if (
            not isinstance(package["language"], str)
            or package["language"] not in SOURCE_LANGUAGES - {"und"}
        ):
            raise ReadingPackError(f"invalid {label}: language is invalid")
        if not isinstance(package["manifest_sha256"], str) or not _SHA256.fullmatch(
            package["manifest_sha256"]
        ):
            raise ReadingPackError(f"invalid {label}: manifest_sha256 is invalid")
        _validate_authority(package["authority"], f"{label} authority")
        package_ids.append(package["package_id"])
        package_languages.append(package["language"])
    if len(package_ids) != len(set(package_ids)):
        raise ReadingPackError("invalid author input plan: package IDs must be unique")
    if len(package_languages) != len(set(package_languages)):
        raise ReadingPackError("invalid author input plan: package languages must be unique")
    for field in ("base_canonical_sha256", "prospective_canonical_sha256"):
        target = value[field]
        if not isinstance(target, str) or not _SHA256.fullmatch(target):
            raise ReadingPackError(f"invalid author input plan: {field} is invalid")
    languages = value["languages"]
    if not isinstance(languages, Mapping) or set(languages) != set(package_languages):
        raise ReadingPackError(
            "invalid author input plan: languages must match package languages"
        )
    source_ids: list[str] = []
    for language, language_summary in languages.items():
        if not isinstance(language_summary, Mapping):
            raise ReadingPackError(
                f"invalid author input plan: {language} summary must be an object"
            )
        _require_keys(
            language_summary,
            {"modules"},
            {"modules"},
            f"author input plan language {language}",
        )
        modules = language_summary["modules"]
        if not isinstance(modules, Mapping) or set(modules) != set(MODULES):
            raise ReadingPackError(
                f"invalid author input plan: {language} module set is invalid"
            )
        for module, summary in modules.items():
            if not isinstance(summary, Mapping):
                raise ReadingPackError(
                    f"invalid author input plan: {language}.{module} summary is invalid"
                )
            fields = {
                "mode", "source", "provided_record_ids", "provided_record_hashes",
                "added_ids", "replaced_ids", "removed_ids", "preserved_ids",
                "canonical_count_after",
            }
            _require_keys(
                summary,
                fields,
                fields,
                f"author input plan module {language}.{module}",
            )
            if summary["mode"] not in MODES:
                raise ReadingPackError(
                    f"invalid author input plan: {language}.{module}.mode is invalid"
                )
            if summary["source"] is not None:
                validate_source_record(
                    summary["source"],
                    f"author input plan {language}.{module} source",
                )
                if summary["source"]["language"] != language:
                    raise ReadingPackError(
                        f"invalid author input plan: {language}.{module}.source language differs"
                    )
                source_ids.append(summary["source"]["id"])
            if (summary["mode"] in {"provided", "augment"}) != (
                summary["source"] is not None
            ):
                raise ReadingPackError(
                    f"invalid author input plan: {language}.{module}.source does not match mode"
                )
            for field in (
                "provided_record_ids", "added_ids", "replaced_ids",
                "removed_ids", "preserved_ids",
            ):
                ids = summary[field]
                if (
                    not isinstance(ids, list)
                    or not all(isinstance(item, str) for item in ids)
                    or len(ids) != len(set(ids))
                ):
                    raise ReadingPackError(
                        f"invalid author input plan: {language}.{module}.{field} is invalid"
                    )
            hashes = summary["provided_record_hashes"]
            if (
                not isinstance(hashes, Mapping)
                or set(hashes) != set(summary["provided_record_ids"])
                or not all(
                    isinstance(identifier, str)
                    and isinstance(digest, str)
                    and _SHA256.fullmatch(digest)
                    for identifier, digest in hashes.items()
                )
            ):
                raise ReadingPackError(
                    f"invalid author input plan: {language}.{module}.provided_record_hashes is invalid"
                )
            if summary["mode"] in {"generate", "omit"} and (
                summary["provided_record_ids"] or hashes
            ):
                raise ReadingPackError(
                    f"invalid author input plan: {language}.{module} has supplied records for {summary['mode']} mode"
                )
            if not isinstance(summary["canonical_count_after"], int) or isinstance(
                summary["canonical_count_after"], bool
            ) or summary["canonical_count_after"] < 0:
                raise ReadingPackError(
                    f"invalid author input plan: {language}.{module}.canonical_count_after is invalid"
                )
    attachments = value["attachments"]
    if not isinstance(attachments, list):
        raise ReadingPackError("invalid author input plan: attachments must be an array")
    for index, source in enumerate(attachments):
        validate_source_record(source, f"author input plan attachment[{index}]")
        source_ids.append(source["id"])
    if len(source_ids) != len(set(source_ids)):
        raise ReadingPackError("invalid author input plan: source IDs must be unique")
    return deepcopy(dict(value))


def write_author_input_plan(path: Path, plan: Mapping[str, Any]) -> None:
    checked = validate_author_input_plan(plan)
    path = Path(path).resolve()
    if path.exists():
        raise ReadingPackError(f"refusing to overwrite existing author input plan: {path}", EXIT_IO)
    if path.name in {
        "reading-pack.toml", "quality-plan.json", "sources.json", STATE_NAME,
        MANIFEST_NAME,
    } or path.parent.name in {"data", "templates", "dist"}:
        raise ReadingPackError(
            "refusing to write an author input plan over canonical or generated data",
            EXIT_IO,
        )
    write_json(path, checked)


def load_author_input_plan(path: Path) -> dict[str, Any]:
    return validate_author_input_plan(
        _bounded_json(Path(path).resolve(), MAX_PLAN_BYTES, "author input plan")
    )


def default_author_input_state(config: Mapping[str, Any]) -> dict[str, Any]:
    languages: dict[str, Any] = {}
    for language in config.get("languages", []):
        languages[language] = {
            "modules": {
                module: {
                    "mode": "generate",
                    "package_id": "",
                    "manifest_sha256": "",
                    "authority": None,
                    "source": None,
                    "provided_record_ids": [],
                    "provided_record_hashes": {},
                    "canonical_count_after": 0,
                }
                for module in MODULES
            },
            "attachments": [],
            "history": [],
        }
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "languages": languages,
    }


def _legacy_compatible_author_input_state(value: Any) -> Any:
    """Add the optional policy module to pre-policy provenance state."""

    if not isinstance(value, Mapping):
        return value
    normalized = deepcopy(dict(value))
    languages = normalized.get("languages")
    if not isinstance(languages, Mapping):
        return normalized
    for state in languages.values():
        if not isinstance(state, dict):
            continue
        modules = state.get("modules")
        if isinstance(modules, dict) and "policy" not in modules:
            modules["policy"] = {
                "mode": "generate",
                "package_id": "",
                "manifest_sha256": "",
                "authority": None,
                "source": None,
                "provided_record_ids": [],
                "provided_record_hashes": {},
                "canonical_count_after": 0,
            }
        history = state.get("history")
        if isinstance(history, list):
            for entry in history:
                modes = entry.get("modes") if isinstance(entry, dict) else None
                if isinstance(modes, dict):
                    modes.setdefault("policy", "generate")
    return normalized


def validate_author_input_state(
    value: Any, configured_languages: list[str] | None = None
) -> dict[str, Any]:
    value = _legacy_compatible_author_input_state(value)
    require_structure(
        "author-input-state.schema.json", value, label="author input state"
    )
    if not isinstance(value, Mapping):
        raise ReadingPackError("invalid author input state: root must be an object")
    _require_keys(
        value,
        {"schema_version", "languages"},
        {"schema_version", "languages"},
        "author input state",
    )
    if value["schema_version"] != STATE_SCHEMA_VERSION:
        raise ReadingPackError("invalid author input state: unsupported schema version")
    languages = value["languages"]
    if not isinstance(languages, Mapping):
        raise ReadingPackError("invalid author input state: languages must be an object")
    if configured_languages is not None and set(languages) != set(configured_languages):
        raise ReadingPackError("invalid author input state: configured language set differs")
    for language, state in languages.items():
        if language not in SOURCE_LANGUAGES - {"und"} or not isinstance(state, Mapping):
            raise ReadingPackError("invalid author input state: language entry is invalid")
        _require_keys(
            state,
            {"modules", "attachments", "history"},
            {"modules", "attachments", "history"},
            f"author input state {language}",
        )
        modules = state["modules"]
        if not isinstance(modules, Mapping) or set(modules) != set(MODULES):
            raise ReadingPackError(f"invalid author input state: {language} module set differs")
        for module, current in modules.items():
            if not isinstance(current, Mapping):
                raise ReadingPackError(f"invalid author input state: {language}.{module} is invalid")
            fields = {
                "mode", "package_id", "manifest_sha256", "authority", "source",
                "provided_record_ids", "provided_record_hashes",
                "canonical_count_after",
            }
            _require_keys(current, fields, fields, f"author input state {language}.{module}")
            if current["mode"] not in MODES:
                raise ReadingPackError(f"invalid author input state: {language}.{module}.mode")
            if not isinstance(current["package_id"], str) or (
                current["package_id"]
                and not _PACKAGE_ID.fullmatch(current["package_id"])
            ):
                raise ReadingPackError(f"invalid author input state: {language}.{module}.package_id")
            if not isinstance(current["manifest_sha256"], str) or (
                current["manifest_sha256"]
                and not _SHA256.fullmatch(current["manifest_sha256"])
            ):
                raise ReadingPackError(f"invalid author input state: {language}.{module}.manifest_sha256")
            if current["authority"] is not None:
                _validate_authority(
                    current["authority"],
                    f"author input state {language}.{module} authority",
                )
            if current["source"] is not None:
                validate_source_record(current["source"], f"author input state {language}.{module} source")
            if (current["mode"] in {"provided", "augment"}) != (current["source"] is not None):
                raise ReadingPackError(
                    f"invalid author input state: {language}.{module} source does not match mode"
                )
            ids = current["provided_record_ids"]
            if (
                not isinstance(ids, list)
                or not all(isinstance(item, str) for item in ids)
                or len(ids) != len(set(ids))
            ):
                raise ReadingPackError(f"invalid author input state: {language}.{module} IDs")
            hashes = current["provided_record_hashes"]
            if (
                not isinstance(hashes, Mapping)
                or set(hashes) != set(ids)
                or not all(
                    isinstance(identifier, str)
                    and isinstance(digest, str)
                    and _SHA256.fullmatch(digest)
                    for identifier, digest in hashes.items()
                )
            ):
                raise ReadingPackError(f"invalid author input state: {language}.{module} hashes")
            if current["mode"] in {"generate", "omit"} and (ids or hashes):
                raise ReadingPackError(
                    f"invalid author input state: {language}.{module} supplied records do not match mode"
                )
            has_package = bool(current["package_id"])
            if (
                bool(current["manifest_sha256"]) != has_package
                or (current["authority"] is not None) != has_package
            ):
                raise ReadingPackError(
                    f"invalid author input state: {language}.{module} package provenance is incomplete"
                )
            if not isinstance(current["canonical_count_after"], int) or isinstance(
                current["canonical_count_after"], bool
            ) or current["canonical_count_after"] < 0:
                raise ReadingPackError(f"invalid author input state: {language}.{module} count")
        if not isinstance(state["attachments"], list):
            raise ReadingPackError(f"invalid author input state: {language}.attachments")
        for index, source in enumerate(state["attachments"]):
            validate_source_record(source, f"author input state {language} attachment[{index}]")
        attachment_ids = [source["id"] for source in state["attachments"]]
        module_source_ids = [
            current["source"]["id"]
            for current in modules.values()
            if current["source"] is not None
        ]
        language_source_ids = module_source_ids + attachment_ids
        if len(language_source_ids) != len(set(language_source_ids)):
            raise ReadingPackError(
                f"invalid author input state: {language} contains duplicate source IDs"
            )
        if not isinstance(state["history"], list) or len(state["history"]) > MAX_HISTORY:
            raise ReadingPackError(f"invalid author input state: {language}.history")
        for index, entry in enumerate(state["history"]):
            label = f"author input state {language}.history[{index}]"
            if not isinstance(entry, Mapping):
                raise ReadingPackError(f"invalid {label}: must be an object")
            _require_keys(
                entry,
                {"package_id", "manifest_sha256", "authority", "modes"},
                {"package_id", "manifest_sha256", "authority", "modes"},
                label,
            )
            if not isinstance(entry["package_id"], str) or not _PACKAGE_ID.fullmatch(entry["package_id"]):
                raise ReadingPackError(f"invalid {label}: package_id")
            if not isinstance(entry["manifest_sha256"], str) or not _SHA256.fullmatch(entry["manifest_sha256"]):
                raise ReadingPackError(f"invalid {label}: manifest_sha256")
            _validate_authority(entry["authority"], f"{label} authority")
            if (
                not isinstance(entry["modes"], Mapping)
                or set(entry["modes"]) != set(MODULES)
                or any(mode not in MODES for mode in entry["modes"].values())
            ):
                raise ReadingPackError(f"invalid {label}: modes")
    return deepcopy(dict(value))


def load_author_input_state(project: Path, config: Mapping[str, Any] | None = None) -> dict[str, Any]:
    project = Path(project).resolve()
    config = config or load_config(project)
    path = project / STATE_NAME
    if not path.exists():
        return default_author_input_state(config)
    return validate_author_input_state(
        _bounded_json(path, MAX_STATE_BYTES, "author input state"),
        list(config.get("languages", [])),
    )


def _prospective_registry(
    project: Path, packages: list[Mapping[str, Any]]
) -> dict[str, Any]:
    registry = load_source_registry(project)
    updated = list(registry["sources"])
    for source in (
        source for package in packages for source in package["sources"]
    ):
        existing = next((item for item in updated if item["id"] == source["id"]), None)
        if existing is not None and existing != source:
            raise ReadingPackError(
                f"author input source ID {source['id']} is already registered with a different identity; use a new source ID"
            )
        duplicate = next(
            (
                item for item in updated
                if item["id"] != source["id"]
                and item["name"] == source["name"]
                and item["sha256"] == source["sha256"]
            ),
            None,
        )
        if duplicate is not None:
            raise ReadingPackError(
                f"author input source is already registered as {duplicate['id']}"
            )
        updated = [item for item in updated if item["id"] != source["id"]]
        updated.append(deepcopy(source))
    updated.sort(key=lambda item: item["id"])
    return validate_source_registry(
        {"schema_version": registry["schema_version"], "sources": updated}
    )


def _prospective_state(
    project: Path,
    config: Mapping[str, Any],
    packages: list[Mapping[str, Any]],
    module_summaries: Mapping[str, Mapping[str, Mapping[str, Any]]],
) -> dict[str, Any]:
    state = load_author_input_state(project, config)
    for package in packages:
        manifest = package["manifest"]
        language = manifest["language"]
        language_state = state["languages"][language]
        for module in MODULES:
            summary = module_summaries[language][module]
            language_state["modules"][module] = {
                "mode": summary["mode"],
                "package_id": manifest["package_id"],
                "manifest_sha256": package["manifest_sha256"],
                "authority": deepcopy(manifest["authority"]),
                "source": deepcopy(summary["source"]),
                "provided_record_ids": list(summary["provided_record_ids"]),
                "provided_record_hashes": deepcopy(summary["provided_record_hashes"]),
                "canonical_count_after": summary["canonical_count_after"],
            }
        language_state["attachments"] = deepcopy(package["attachments"])
        history_entry = {
            "package_id": manifest["package_id"],
            "manifest_sha256": package["manifest_sha256"],
            "authority": deepcopy(manifest["authority"]),
            "modes": {
                module: manifest["modules"][module]["mode"] for module in MODULES
            },
        }
        history = [
            item for item in language_state["history"]
            if item.get("package_id") != manifest["package_id"]
            or item.get("manifest_sha256") != package["manifest_sha256"]
        ]
        history.append(history_entry)
        if len(history) > MAX_HISTORY:
            history = history[-MAX_HISTORY:]
        language_state["history"] = history
    return validate_author_input_state(state, list(config.get("languages", [])))


def _author_input_transaction_path(path: str, kind: str) -> bool:
    return kind == "json" and (
        re.fullmatch(r"data/pack\.(?:ja|en)\.json", path) is not None
        or path in {REGISTRY_NAME, STATE_NAME}
    )


def _recover_prepared(project: Path) -> None:
    recover_artifact_transaction(
        project,
        prepared_name="author-input-prepared.json",
        path_policy=_author_input_transaction_path,
        label="author input",
        maximum_bytes=MAX_PLAN_BYTES * 4,
    )


def apply_author_input_plan(
    project: Path,
    plan: Mapping[str, Any],
    package_paths: Path | list[Path] | tuple[Path, ...],
) -> dict[str, Any]:
    project = Path(project).resolve()
    checked_plan = validate_author_input_plan(plan)
    with project_lock(project):
        _recover_prepared(project)
        config = load_config(project)
        packages = _load_author_input_packages(config, package_paths)
        current_plan, before_data, prospective_data, module_summaries = (
            _build_author_input_plan(project, config, packages)
        )
        if current_plan != checked_plan:
            raise ReadingPackError(
                "author input package or canonical data changed after planning; create a fresh plan"
            )
        if _json_hash(prospective_data) != checked_plan["prospective_canonical_sha256"]:
            raise ReadingPackError("author input prospective data changed during apply")
        prospective_registry = _prospective_registry(project, packages)
        prospective_state = _prospective_state(
            project, config, packages, module_summaries
        )
        before_registry = load_source_registry(project)
        before_state = load_author_input_state(project, config)
        changed_data = {
            (Path("data") / f"pack.{language}.json").as_posix(): before_data[language]
            for language in before_data
            if before_data[language] != prospective_data[language]
        }
        changes = [
            ArtifactChange(
                path=relative,
                kind="json",
                before=before,
                after=prospective_data[
                    relative.removeprefix("data/pack.").removesuffix(".json")
                ],
            )
            for relative, before in changed_data.items()
        ]
        changes.extend(
            [
                ArtifactChange(
                    REGISTRY_NAME,
                    "json",
                    before_registry,
                    prospective_registry,
                    before_exists=(project / REGISTRY_NAME).exists(),
                ),
                ArtifactChange(
                    STATE_NAME,
                    "json",
                    before_state,
                    prospective_state,
                    before_exists=(project / STATE_NAME).exists(),
                ),
            ]
        )
        apply_artifact_transaction(
            project,
            prepared_name="author-input-prepared.json",
            changes=changes,
            path_policy=_author_input_transaction_path,
            label="author input",
        )
    result = {
        "packages": [package["manifest"]["package_id"] for package in packages],
        "languages": [package["manifest"]["language"] for package in packages],
        "plan_id": checked_plan["plan_id"],
        "language_results": {
            package["manifest"]["language"]: {
                "modules": deepcopy(module_summaries[package["manifest"]["language"]]),
                "attachments": deepcopy(package["attachments"]),
            }
            for package in packages
        },
    }
    if len(packages) == 1:
        package = packages[0]
        language = package["manifest"]["language"]
        result.update({
            "package_id": package["manifest"]["package_id"],
            "language": language,
            "modules": deepcopy(module_summaries[language]),
            "attachments": deepcopy(package["attachments"]),
        })
    return result


def author_input_consistency_findings(
    data_by_language: Mapping[str, Mapping[str, Any]],
    state: Mapping[str, Any],
    registered_sources: Mapping[str, Mapping[str, Any]] | None = None,
    review_state: Mapping[str, Any] | None = None,
) -> list[tuple[str, str, str]]:
    """Return body-free state/canonical consistency findings.

    Tuples are ``(code, path, message)`` so the validation module can turn them
    into its public Issue type without creating an import cycle.
    """

    findings: list[tuple[str, str, str]] = []
    for language, language_state in state["languages"].items():
        data = data_by_language.get(language)
        if not isinstance(data, Mapping):
            continue
        for module, current in language_state["modules"].items():
            mode = current["mode"]
            expected = list(current["provided_record_ids"])
            expected_hashes = dict(current["provided_record_hashes"])
            if review_state is not None and mode in {"provided", "augment"}:
                from .author_review import review_overrides_for_author_input

                field_name = FIELD_MODULES[module][0] if module in FIELD_MODULES else None
                expected_hashes = review_overrides_for_author_input(
                    review_state,
                    language=language,
                    module=module,
                    module_state_sha256=_json_hash(current),
                    initial_hashes=expected_hashes,
                    field=field_name,
                )
                expected = [identifier for identifier in expected if identifier in expected_hashes]
            recorded_source = current["source"]
            if recorded_source is not None and registered_sources is not None:
                registered = registered_sources.get(recorded_source["id"])
                if (
                    registered is None
                    or registered.get("sha256") != recorded_source["sha256"]
                    or registered.get("role") == "primary-book"
                ):
                    findings.append((
                        "RP502",
                        f"{STATE_NAME}.languages.{language}.modules.{module}.source",
                        "author input source is absent or stale in sources.json",
                    ))
            if module in COLLECTION_MODULES:
                collection = COLLECTION_MODULES[module]
                records = data.get(collection, [])
                ids = [item.get("id") for item in records if isinstance(item, Mapping)]
                if mode == "omit" and ids:
                    findings.append((
                        "RP500",
                        f"{STATE_NAME}.languages.{language}.modules.{module}",
                        "omit mode requires an empty canonical module",
                    ))
                if mode == "provided" and ids != expected:
                    findings.append((
                        "RP501",
                        f"{STATE_NAME}.languages.{language}.modules.{module}",
                        "provided mode requires canonical IDs to match the supplied authoritative set",
                    ))
                if mode in {"provided", "augment"} and current["source"] is not None:
                    source = current["source"]
                    by_id = {item.get("id"): item for item in records if isinstance(item, Mapping)}
                    for identifier in expected:
                        record = by_id.get(identifier)
                        if (
                            record is None
                            or record.get("provenance_source_id") != source["id"]
                            or record.get("provenance_source_hash") != source["sha256"]
                            or semantic_hash(dict(record))
                            != expected_hashes.get(identifier)
                        ):
                            findings.append((
                                "RP502",
                                f"data/pack.{language}.json.{collection}.{identifier}",
                                "author-provided record provenance is absent or stale",
                            ))
                continue
            field, empty = FIELD_MODULES[module]
            chapters = data.get("chapters", [])
            populated = [
                item.get("id") for item in chapters
                if isinstance(item, Mapping) and item.get(field) != empty
            ]
            if mode == "omit" and populated:
                findings.append((
                    "RP500",
                    f"{STATE_NAME}.languages.{language}.modules.{module}",
                    f"omit mode requires every chapter {field} to be empty",
                ))
            if mode == "provided" and set(populated) != set(expected):
                findings.append((
                    "RP501",
                    f"{STATE_NAME}.languages.{language}.modules.{module}",
                    f"provided mode requires populated chapter IDs to match the supplied authoritative set",
                ))
            if mode in {"provided", "augment"}:
                by_id = {
                    item.get("id"): item
                    for item in chapters
                    if isinstance(item, Mapping)
                }
                for identifier in expected:
                    chapter = by_id.get(identifier)
                    actual = (
                        semantic_hash({"chapter_id": identifier, field: chapter.get(field)})
                        if chapter is not None
                        else ""
                    )
                    if actual != expected_hashes.get(identifier):
                        findings.append((
                            "RP502",
                            f"data/pack.{language}.json.chapters.{identifier}.{field}",
                            "author-provided chapter field is absent or differs from the recorded input",
                        ))
        if registered_sources is not None:
            for index, source in enumerate(language_state["attachments"]):
                registered = registered_sources.get(source["id"])
                if registered is None or registered.get("sha256") != source["sha256"]:
                    findings.append((
                        "RP502",
                        f"{STATE_NAME}.languages.{language}.attachments[{index}]",
                        "author input attachment is absent or stale in sources.json",
                    ))
    return findings


def create_author_input_template(
    directory: Path,
    *,
    language: str,
    authority_type: str,
    authority_name: str,
    supplied_at: str,
    package_id: str,
) -> Path:
    directory = Path(directory).resolve()
    if directory.exists():
        if not directory.is_dir() or any(directory.iterdir()):
            raise ReadingPackError(
                f"refusing to create an author input template in non-empty path: {directory}",
                EXIT_IO,
            )
    manifest = validate_author_input_manifest(
        {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "package_id": package_id,
            "language": language,
            "authority": {
                "type": authority_type,
                "name": authority_name,
                "supplied_at": supplied_at,
            },
            "modules": {module: {"mode": "generate"} for module in MODULES},
            "attachments": [],
        }
    )
    directory.mkdir(parents=True, exist_ok=True)
    write_json(directory / MANIFEST_NAME, manifest)
    for module in MODULES:
        write_json(
            directory / f"{module}.json",
            {"schema_version": 1, "module": module, "records": []},
        )
    readme = (
        "# Author Input Package\n\n"
        "Edit `author-input.json` per module. Use `provided` for a complete "
        "authoritative set, `augment` to preserve generated extras, `generate` "
        "when the workflow should create the module, or `omit` when it must stay "
        "empty. For `provided` or `augment`, add `file`, `format`, and `source_id` "
        "to that module declaration. JSON module files use the generated "
        "`records` envelope. CSV is also accepted; list cells use `|`. Files "
        "listed in `attachments` are hash-registered support sources and are not "
        "silently converted into claims or Q&A. Supplied content is always "
        "imported as draft; file presence is not approval.\n"
    )
    from reading_pack.project import atomic_write_text

    atomic_write_text(directory / "README.md", readme)
    return directory / MANIFEST_NAME
