"""Hash-bound evidence and transactional application for author review.

The public review input is one human-editable Markdown file. This module keeps
its private snapshot evidence and applies expanded decisions only after the
review and current project state have both been revalidated.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from copy import deepcopy
from datetime import date
from pathlib import Path
from typing import Any, Callable, Mapping

from reading_pack.artifact_transaction import (
    ArtifactChange,
    apply_artifact_transaction,
    json_hash as _json_hash,
    recover_artifact_transaction,
    text_hash as _text_hash,
)
from reading_pack.errors import EXIT_IO, ReadingPackError
from reading_pack.hashing import canonical_data_hash, semantic_hash
from reading_pack.project import (
    load_config,
    load_language_data,
    project_lock,
    write_json,
)
from reading_pack.schema_validation import require_structure
from reading_pack.validation import errors, validate_data_set, validate_project


MANIFEST_NAME = "manifest.json"
STATE_NAME = "author-review-state.json"
PREPARED_NAME = "author-review-prepared.json"
MANIFEST_SCHEMA_VERSION = 2
PLAN_SCHEMA_VERSION = 1
STATE_SCHEMA_VERSION = 1
MAX_MANIFEST_BYTES = 16 * 1024 * 1024
MAX_PLAN_BYTES = 16 * 1024 * 1024
MAX_STATE_BYTES = 16 * 1024 * 1024
MAX_HISTORY = 1_000
MAX_COMMENT_CHARACTERS = 20_000

COLLECTIONS = (
    "chapters",
    "certainty",
    "claims",
    "misreadings",
    "policies",
    "names",
    "glossary",
    "references",
)
COLLECTION_MODULES = {
    "chapters": "chapters",
    "certainty": "certainty",
    "claims": "claims",
    "misreadings": "qa",
    "policies": "policy",
    "names": "names",
    "glossary": "glossary",
    "references": "references",
}
REVIEW_MODULES = tuple(dict.fromkeys(COLLECTION_MODULES.values()))
FIELD_MODULES = {"summary": "summaries", "terms": "chapter_terms"}
EDITABLE_FIELDS = {
    "chapters": (
        "kind", "title", "pages", "sections", "summary", "terms",
        "contributors", "aliases", "learning_objectives", "prerequisites",
        "spoiler_scope", "source_locations",
    ),
    "certainty": ("label", "definition", "source_locations"),
    "claims": (
        "layer", "kind", "statement", "chapter_ids", "certainty_id",
        "falsifiability", "revision_conditions", "source_locations",
        "reader_note",
    ),
    "misreadings": (
        "kind", "issue", "response", "impact", "remaining_uncertainty",
        "chapter_ids", "claim_ids", "anchor", "source_locations",
    ),
    "policies": ("kind", "statement", "source_locations"),
    "names": (
        "name", "aliases", "chapter_id", "book_context", "source_locations",
    ),
    "glossary": (
        "term", "aliases", "chapter_id", "book_meaning", "source_locations",
    ),
    "references": (
        "url", "label", "relation", "url_scope", "retrieval_policy",
        "source_locations",
    ),
}
LIST_FIELDS = {
    "sections", "terms", "contributors", "aliases", "learning_objectives",
    "prerequisites", "chapter_ids", "claim_ids", "source_locations",
}
LABEL_FIELDS = {
    "chapters": "title",
    "certainty": "label",
    "claims": "statement",
    "misreadings": "misreading",
    "policies": "statement",
    "names": "name",
    "glossary": "term",
    "references": "label",
}
_SHA256 = re.compile(r"[a-f0-9]{64}")
_SAFE_LINE = re.compile(r"[^\x00-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]{1,500}")
_UNIT_ID = re.compile(r"ARU-[0-9]{6}")
_REVIEW_ID = re.compile(r"AR-[A-F0-9]{20}")
_PLAN_ID = re.compile(r"ARPLAN-[A-F0-9]{20}")


def _file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise ReadingPackError(f"cannot read review input {path}: {exc}", EXIT_IO) from exc


def _strict_json(path: Path, maximum: int, label: str) -> Any:
    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in values:
            if key in result:
                raise ValueError(f"duplicate object key: {key}")
            result[key] = value
        return result

    try:
        if path.stat().st_size > maximum:
            raise ReadingPackError(f"{label} exceeds {maximum} bytes", EXIT_IO)
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON number: {value}")
            ),
        )
    except ReadingPackError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ReadingPackError(f"cannot read {label} {path}: {exc}", EXIT_IO) from exc


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


def _full_state_hash(data_by_lang: Mapping[str, Mapping[str, Any]]) -> str:
    return _json_hash(data_by_lang)


def _review_value(record: Mapping[str, Any]) -> dict[str, Any]:
    value = deepcopy(dict(record))
    for key in ("status", "translation_status", "review_notes"):
        value.pop(key, None)
    return value


def _review_record_hash(record: Mapping[str, Any]) -> str:
    return _json_hash(_review_value(record))


def _field_hash(record_id: str, field: str, value: Any) -> str:
    return semantic_hash({"chapter_id": record_id, field: value})


def _read_text(path: Path, maximum: int, label: str) -> str:
    try:
        if path.stat().st_size > maximum:
            raise ReadingPackError(f"{label} exceeds {maximum} bytes", EXIT_IO)
        return path.read_text(encoding="utf-8")
    except ReadingPackError:
        raise
    except (OSError, UnicodeError) as exc:
        raise ReadingPackError(f"cannot read {label} {path}: {exc}", EXIT_IO) from exc


def _author_input_state(project: Path, config: Mapping[str, Any]) -> dict[str, Any]:
    from .author_input import load_author_input_state

    return load_author_input_state(project, config)


def _module_binding(
    author_state: Mapping[str, Any], language: str, module: str
) -> str:
    return _json_hash(author_state["languages"][language]["modules"][module])


def default_author_review_state() -> dict[str, Any]:
    return {"schema_version": STATE_SCHEMA_VERSION, "reviews": []}


def validate_author_review_state(value: Any) -> dict[str, Any]:
    require_structure(
        "author-review-state.schema.json", value, label="author review state"
    )
    if not isinstance(value, Mapping):
        raise ReadingPackError("invalid author review state: root must be an object")
    _require_keys(
        value, {"schema_version", "reviews"}, {"schema_version", "reviews"},
        "author review state",
    )
    if value["schema_version"] != STATE_SCHEMA_VERSION:
        raise ReadingPackError("invalid author review state: unsupported schema version")
    reviews = value["reviews"]
    if not isinstance(reviews, list) or len(reviews) > MAX_HISTORY:
        raise ReadingPackError("invalid author review state: reviews must be a bounded array")
    seen_plans: set[str] = set()
    for index, review in enumerate(reviews):
        label = f"author review state review[{index}]"
        if not isinstance(review, Mapping):
            raise ReadingPackError(f"invalid {label}: must be an object")
        fields = {
            "review_id", "plan_id", "reviewer", "reviewed_at",
            "manifest_sha256", "decisions_sha256", "canonical_sha256_before",
            "canonical_sha256_after", "final_signoff", "actions",
            "field_overrides",
        }
        _require_keys(review, fields, fields, label)
        if not isinstance(review["review_id"], str) or not _REVIEW_ID.fullmatch(review["review_id"]):
            raise ReadingPackError(f"invalid {label}: review_id")
        if not isinstance(review["plan_id"], str) or not _PLAN_ID.fullmatch(review["plan_id"]):
            raise ReadingPackError(f"invalid {label}: plan_id")
        if review["plan_id"] in seen_plans:
            raise ReadingPackError(f"invalid {label}: duplicate plan_id")
        seen_plans.add(review["plan_id"])
        if not isinstance(review["reviewer"], str) or not _SAFE_LINE.fullmatch(review["reviewer"]):
            raise ReadingPackError(f"invalid {label}: reviewer")
        if not isinstance(review["reviewed_at"], str):
            raise ReadingPackError(f"invalid {label}: reviewed_at")
        try:
            date.fromisoformat(review["reviewed_at"])
        except ValueError as exc:
            raise ReadingPackError(f"invalid {label}: reviewed_at") from exc
        for field in (
            "manifest_sha256", "decisions_sha256", "canonical_sha256_before",
            "canonical_sha256_after",
        ):
            if not isinstance(review[field], str) or not _SHA256.fullmatch(review[field]):
                raise ReadingPackError(f"invalid {label}: {field}")
        if not isinstance(review["final_signoff"], bool):
            raise ReadingPackError(f"invalid {label}: final_signoff")
        for field, kind in (("actions", "action"), ("field_overrides", "field override")):
            items = review[field]
            if not isinstance(items, list) or len(items) > 100_000:
                raise ReadingPackError(f"invalid {label}: {field}")
            for item_index, item in enumerate(items):
                item_label = f"{label} {kind}[{item_index}]"
                if not isinstance(item, Mapping):
                    raise ReadingPackError(f"invalid {item_label}: must be an object")
                common = {
                    "language", "module", "module_state_sha256", "record_id",
                    "before_sha256", "after_sha256",
                }
                allowed = common | (
                    {"collection", "decision", "after_status", "after_translation_status"}
                    if field == "actions" else {"field"}
                )
                _require_keys(item, allowed, allowed, item_label)
                if item["language"] not in {"ja", "en"}:
                    raise ReadingPackError(f"invalid {item_label}: language")
                if item["module"] not in {
                    "chapters", "summaries", "chapter_terms", "certainty", "claims",
                    "qa", "policy", "names", "glossary", "references",
                }:
                    raise ReadingPackError(f"invalid {item_label}: module")
                if not isinstance(item["module_state_sha256"], str) or not _SHA256.fullmatch(item["module_state_sha256"]):
                    raise ReadingPackError(f"invalid {item_label}: module_state_sha256")
                if not isinstance(item["record_id"], str) or not item["record_id"]:
                    raise ReadingPackError(f"invalid {item_label}: record_id")
                for digest_field in ("before_sha256", "after_sha256"):
                    digest = item[digest_field]
                    if digest is not None and (
                        not isinstance(digest, str) or not _SHA256.fullmatch(digest)
                    ):
                        raise ReadingPackError(f"invalid {item_label}: {digest_field}")
                if field == "actions":
                    if item["collection"] not in COLLECTIONS or item["decision"] not in {
                        "approve", "revise", "exclude"
                    }:
                        raise ReadingPackError(f"invalid {item_label}: action fields")
                    if item["after_status"] not in {None, "draft", "approved"}:
                        raise ReadingPackError(f"invalid {item_label}: after_status")
                    if item["after_translation_status"] not in {None, "draft", "approved"}:
                        raise ReadingPackError(
                            f"invalid {item_label}: after_translation_status"
                        )
                elif item["field"] not in FIELD_MODULES:
                    raise ReadingPackError(f"invalid {item_label}: field")
    return deepcopy(dict(value))


def load_author_review_state(project: Path) -> dict[str, Any]:
    path = Path(project).resolve() / STATE_NAME
    if not path.exists():
        return default_author_review_state()
    return validate_author_review_state(
        _strict_json(path, MAX_STATE_BYTES, "author review state")
    )


def _snapshot(
    project: Path,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, Any]]:
    config, data_by_lang, issues = validate_project(project)
    fatal = errors(issues)
    if fatal:
        raise ReadingPackError(
            f"cannot create author review from invalid project ({len(fatal)} error(s))"
        )
    return config, data_by_lang, _author_input_state(project, config)


def _snapshot_hashes(
    project: Path, config: Mapping[str, Any], data_by_lang: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    template_hashes = {
        language: _file_sha256(project / "templates" / f"pack.{language}.md")
        for language in config["languages"]
    }
    return {
        "canonical_state_sha256": _full_state_hash(data_by_lang),
        "canonical_data_sha256": canonical_data_hash(dict(data_by_lang)),
        "config_sha256": _file_sha256(project / "reading-pack.toml"),
        "quality_plan_sha256": _file_sha256(project / "quality-plan.json"),
        "author_review_state_sha256": _json_hash(load_author_review_state(project)),
        "template_sha256": template_hashes,
    }


def _record_index(data_by_lang: Mapping[str, Mapping[str, Any]]) -> dict[tuple[str, str, str], dict[str, Any]]:
    result: dict[tuple[str, str, str], dict[str, Any]] = {}
    for language, data in data_by_lang.items():
        for collection in COLLECTIONS:
            for record in data.get(collection, []):
                result[(language, collection, record["id"])] = record
    return result


def _unit_records(
    config: Mapping[str, Any],
    data_by_lang: Mapping[str, Mapping[str, Any]],
    author_state: Mapping[str, Any],
    *,
    modules: tuple[str, ...] | None = None,
    record_ids: tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    sequence = 0
    for collection in COLLECTIONS:
        primary_order = [
            record["id"]
            for record in data_by_lang[config["primary_language"]].get(collection, [])
        ]
        known = set(primary_order)
        identifiers = primary_order + sorted({
            record["id"]
            for language in config["languages"]
            for record in data_by_lang[language].get(collection, [])
            if record["id"] not in known
        })
        by_language = {
            language: {
                record["id"]: record
                for record in data_by_lang[language].get(collection, [])
            }
            for language in config["languages"]
        }
        for identifier in identifiers:
            for language in config["languages"]:
                record = by_language[language].get(identifier)
                if record is None:
                    continue
                sequence += 1
                module = COLLECTION_MODULES[collection]
                if modules is not None and module not in modules:
                    continue
                if record_ids is not None and identifier not in record_ids:
                    continue
                records.append({
                    "unit_id": f"ARU-{sequence:06d}",
                    "language": language,
                    "collection": collection,
                    "module": module,
                    "module_state_sha256": _module_binding(author_state, language, module),
                    "record_id": identifier,
                    "record_sha256": _review_record_hash(record),
                    "semantic_sha256": semantic_hash(record),
                    "status": record.get("status"),
                    "translation_status": record.get("translation_status"),
                    "editable_fields": list(
                        (
                            tuple(
                                "misreading" if field == "issue" else field
                                for field in EDITABLE_FIELDS[collection]
                            )
                            if collection == "misreadings"
                            and "issue" not in record
                            else EDITABLE_FIELDS[collection]
                        )
                    ),
                })
    return records


def _render_manifest(
    project: Path,
    *,
    created_at: str,
    modules: tuple[str, ...] | None = None,
    record_ids: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    try:
        date.fromisoformat(created_at)
    except ValueError as exc:
        raise ReadingPackError("author review created_at must use YYYY-MM-DD") from exc
    config, data_by_lang, author_state = _snapshot(project)
    snapshot = _snapshot_hashes(project, config, data_by_lang)
    if modules is not None:
        unknown = set(modules) - set(REVIEW_MODULES)
        if unknown or not modules or len(set(modules)) != len(modules):
            raise ReadingPackError("author review modules are invalid")
        modules = tuple(module for module in REVIEW_MODULES if module in modules)
    if record_ids is not None:
        if (
            not record_ids
            or len(set(record_ids)) != len(record_ids)
            or any(not isinstance(record_id, str) or not record_id for record_id in record_ids)
        ):
            raise ReadingPackError("author review record IDs are invalid")
        record_ids = tuple(sorted(record_ids))
    records = _unit_records(
        config,
        data_by_lang,
        author_state,
        modules=modules,
        record_ids=record_ids,
    )
    if (modules is not None or record_ids is not None) and not records:
        raise ReadingPackError("author review scope selects no records")
    if record_ids is not None:
        selected_ids = {record["record_id"] for record in records}
        missing = sorted(set(record_ids) - selected_ids)
        if missing:
            raise ReadingPackError(
                "author review record scope contains unknown IDs: " + ",".join(missing)
            )
    review_projection = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "created_at": created_at,
        "slug": config["slug"],
        "version": config["version"],
        "primary_language": config["primary_language"],
        "languages": list(config["languages"]),
        "snapshot": snapshot,
        "records": records,
    }
    if modules is not None:
        review_projection["modules"] = list(modules)
    if record_ids is not None:
        review_projection["record_ids"] = list(record_ids)
    review_id = f"AR-{_json_hash(review_projection)[:20].upper()}"
    manifest: dict[str, Any] = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "review_id": review_id,
        "created_at": created_at,
        "slug": config["slug"],
        "version": config["version"],
        "primary_language": config["primary_language"],
        "languages": list(config["languages"]),
        "snapshot": snapshot,
        "records": records,
    }
    if modules is not None:
        manifest["modules"] = list(modules)
    if record_ids is not None:
        manifest["record_ids"] = list(record_ids)
    return manifest


def validate_author_review_manifest(value: Any) -> dict[str, Any]:
    require_structure(
        "author-review-manifest.schema.json", value, label="author review manifest"
    )
    if not isinstance(value, Mapping):
        raise ReadingPackError("invalid author review manifest: root must be an object")
    required_fields = {
        "schema_version", "review_id", "created_at", "slug", "version",
        "primary_language", "languages", "snapshot", "records",
    }
    _require_keys(
        value,
        required_fields,
        required_fields | {"modules", "record_ids"},
        "author review manifest",
    )
    if value["schema_version"] != MANIFEST_SCHEMA_VERSION:
        raise ReadingPackError("invalid author review manifest: unsupported schema version")
    if not isinstance(value["review_id"], str) or not _REVIEW_ID.fullmatch(value["review_id"]):
        raise ReadingPackError("invalid author review manifest: review_id")
    modules = value.get("modules")
    if modules is not None and (
        not isinstance(modules, list)
        or not modules
        or len(set(modules)) != len(modules)
        or any(module not in REVIEW_MODULES for module in modules)
        or modules != [module for module in REVIEW_MODULES if module in modules]
    ):
        raise ReadingPackError("invalid author review manifest: modules")
    if modules is not None and any(
        record.get("module") not in modules for record in value["records"]
    ):
        raise ReadingPackError("invalid author review manifest: record outside module scope")
    record_ids = value.get("record_ids")
    if record_ids is not None and (
        not isinstance(record_ids, list)
        or not record_ids
        or len(set(record_ids)) != len(record_ids)
        or record_ids != sorted(record_ids)
        or any(not isinstance(record_id, str) or not record_id for record_id in record_ids)
    ):
        raise ReadingPackError("invalid author review manifest: record_ids")
    if record_ids is not None:
        selected_ids = {record.get("record_id") for record in value["records"]}
        if selected_ids != set(record_ids):
            raise ReadingPackError("invalid author review manifest: record scope")
    try:
        date.fromisoformat(value["created_at"])
    except (TypeError, ValueError) as exc:
        raise ReadingPackError("invalid author review manifest: created_at") from exc
    if not isinstance(value["slug"], str) or not value["slug"]:
        raise ReadingPackError("invalid author review manifest: slug")
    if not isinstance(value["version"], str) or not value["version"]:
        raise ReadingPackError("invalid author review manifest: version")
    languages = value["languages"]
    if (
        not isinstance(languages, list)
        or not languages
        or len(languages) != len(set(languages))
        or any(language not in {"ja", "en"} for language in languages)
        or value["primary_language"] not in languages
    ):
        raise ReadingPackError("invalid author review manifest: languages")
    snapshot = value["snapshot"]
    snapshot_fields = {
        "canonical_state_sha256", "canonical_data_sha256", "config_sha256",
        "quality_plan_sha256", "author_review_state_sha256", "template_sha256",
    }
    if not isinstance(snapshot, Mapping):
        raise ReadingPackError("invalid author review manifest: snapshot")
    _require_keys(snapshot, snapshot_fields, snapshot_fields, "author review snapshot")
    for field in snapshot_fields - {"template_sha256"}:
        if not isinstance(snapshot[field], str) or not _SHA256.fullmatch(snapshot[field]):
            raise ReadingPackError(f"invalid author review manifest: snapshot {field}")
    if (
        not isinstance(snapshot["template_sha256"], Mapping)
        or set(snapshot["template_sha256"]) != set(languages)
        or any(
            not isinstance(digest, str) or not _SHA256.fullmatch(digest)
            for digest in snapshot["template_sha256"].values()
        )
    ):
        raise ReadingPackError("invalid author review manifest: template hashes")
    records = value["records"]
    if not isinstance(records, list) or len(records) > 100_000:
        raise ReadingPackError("invalid author review manifest: records")
    unit_ids: list[str] = []
    record_keys: list[tuple[str, str, str]] = []
    for index, record in enumerate(records):
        label = f"author review manifest record[{index}]"
        fields = {
            "unit_id", "language", "collection", "module", "module_state_sha256",
            "record_id", "record_sha256", "semantic_sha256", "status",
            "translation_status", "editable_fields",
        }
        if not isinstance(record, Mapping):
            raise ReadingPackError(f"invalid {label}: must be an object")
        _require_keys(record, fields, fields, label)
        if not isinstance(record["unit_id"], str) or not _UNIT_ID.fullmatch(record["unit_id"]):
            raise ReadingPackError(f"invalid {label}: unit_id")
        if record["language"] not in languages or record["collection"] not in COLLECTIONS:
            raise ReadingPackError(f"invalid {label}: language or collection")
        if record["module"] != COLLECTION_MODULES[record["collection"]]:
            raise ReadingPackError(f"invalid {label}: module")
        for field in ("module_state_sha256", "record_sha256", "semantic_sha256"):
            if not isinstance(record[field], str) or not _SHA256.fullmatch(record[field]):
                raise ReadingPackError(f"invalid {label}: {field}")
        if not isinstance(record["record_id"], str) or not record["record_id"]:
            raise ReadingPackError(f"invalid {label}: record_id")
        if record["status"] not in {"draft", "reviewed", "approved"}:
            raise ReadingPackError(f"invalid {label}: status")
        if record["translation_status"] not in {None, "draft", "reviewed", "approved"}:
            raise ReadingPackError(f"invalid {label}: translation_status")
        expected_fields = {tuple(EDITABLE_FIELDS[record["collection"]])}
        if record["collection"] == "misreadings":
            expected_fields.add(
                tuple(
                    "misreading" if field == "issue" else field
                    for field in EDITABLE_FIELDS["misreadings"]
                )
            )
        if tuple(record["editable_fields"]) not in expected_fields:
            raise ReadingPackError(f"invalid {label}: editable_fields")
        unit_ids.append(record["unit_id"])
        record_keys.append((record["language"], record["collection"], record["record_id"]))
    if len(unit_ids) != len(set(unit_ids)) or len(record_keys) != len(set(record_keys)):
        raise ReadingPackError("invalid author review manifest: duplicate record identity")
    return deepcopy(dict(value))


def _evidence_directory(project: Path, value: Path) -> Path:
    root = project / ".reading-pack" / "reviews"
    raw = Path(value)
    target = (root / raw) if not raw.is_absolute() and len(raw.parts) == 1 else raw
    resolved = target.resolve()
    if resolved.parent != root.resolve():
        raise ReadingPackError(
            "author review evidence must be a direct child of .reading-pack/reviews",
            EXIT_IO,
        )
    return resolved


def _review_regular_path(directory: Path, relative: str) -> Path:
    """Resolve an in-packet path without allowing a symlinked component."""

    path = directory / relative
    current = path
    while current != directory:
        if current.is_symlink():
            raise ReadingPackError(
                f"author review path must not be a symlink: {current}", EXIT_IO
            )
        current = current.parent
    return path


def export_author_review_evidence(
    project: Path,
    output: Path,
    *,
    created_at: str | None = None,
    modules: tuple[str, ...] | None = None,
    record_ids: tuple[str, ...] | None = None,
) -> Path:
    project = Path(project).resolve()
    manifest = _render_manifest(
        project,
        created_at=created_at or date.today().isoformat(),
        modules=modules,
        record_ids=record_ids,
    )
    destination = _evidence_directory(project, output)
    if destination.exists():
        raise ReadingPackError(
            f"refusing to overwrite author review evidence: {destination}", EXIT_IO
        )
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination.mkdir(mode=0o700)
    try:
        write_json(destination / MANIFEST_NAME, manifest)
        os.chmod(destination / MANIFEST_NAME, 0o600)
    except Exception:
        # Preserve partial output for diagnosis; never overwrite or recursively delete it.
        raise
    return destination


def _load_review_evidence(
    project: Path, review_directory: Path
) -> tuple[Path, dict[str, Any]]:
    directory = _evidence_directory(project, review_directory)
    if not directory.is_dir() or directory.is_symlink():
        raise ReadingPackError(
            f"author review evidence is not a regular directory: {directory}",
            EXIT_IO,
        )
    manifest_path = _review_regular_path(directory, MANIFEST_NAME)
    if not manifest_path.is_file():
        raise ReadingPackError(
            f"author review manifest is not a regular file: {manifest_path}", EXIT_IO
        )
    manifest = validate_author_review_manifest(
        _strict_json(manifest_path, MAX_MANIFEST_BYTES, "author review manifest")
    )
    expected_manifest = _render_manifest(
        project,
        created_at=manifest["created_at"],
        modules=(tuple(manifest["modules"]) if "modules" in manifest else None),
        record_ids=(
            tuple(manifest["record_ids"])
            if "record_ids" in manifest
            else None
        ),
    )
    if manifest != expected_manifest:
        raise ReadingPackError(
            "author review is stale or its manifest changed; export a fresh review"
        )
    expected_paths = {MANIFEST_NAME}
    actual_paths = {
        path.relative_to(directory).as_posix()
        for path in directory.rglob("*")
        if path.is_file()
    }
    unexpected = sorted(actual_paths - expected_paths)
    missing = sorted(expected_paths - actual_paths)
    if unexpected or missing:
        details = []
        if missing:
            details.append("missing=" + ",".join(missing))
        if unexpected:
            details.append("unexpected=" + ",".join(unexpected))
        raise ReadingPackError("author review file set changed: " + " ".join(details))
    return directory, manifest


def _apply_correction(record: dict[str, Any], field: str, change: Mapping[str, Any]) -> None:
    if change["operation"] == "remove":
        record.pop(field, None)
    else:
        record[field] = deepcopy(change["value"])


def _record_lookup(data_by_lang: Mapping[str, Mapping[str, Any]]) -> dict[tuple[str, str, str], dict[str, Any]]:
    return _record_index(data_by_lang)


def _config_with_author_review(project: Path, approved: bool) -> str:
    text = _read_text(project / "reading-pack.toml", 4 * 1024 * 1024, "project config")
    if not approved:
        return text
    lines = text.splitlines(keepends=True)
    in_workflow = False
    changed = 0
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_workflow = stripped == "[workflow]"
            continue
        if in_workflow and re.fullmatch(r'author_review\s*=\s*"[^"]*"\s*', stripped):
            newline = "\n" if line.endswith("\n") else ""
            lines[index] = 'author_review = "approved"' + newline
            changed += 1
    if changed != 1:
        raise ReadingPackError("reading-pack.toml must contain exactly one workflow.author_review")
    return "".join(lines)


def _plan_id(plan: Mapping[str, Any]) -> str:
    return f"ARPLAN-{_json_hash({key: value for key, value in plan.items() if key != 'plan_id'})[:20].upper()}"


def _decision_projection(parsed: Mapping[str, Any]) -> dict[str, Any]:
    projection = {
        "decisions": [
            {
                "unit_id": item["unit_id"],
                "decision": item["decision"],
                "corrections": item["corrections"],
                "comment": item["comment"],
            }
            for item in parsed["decisions"]
        ],
        "reviewer": parsed["reviewer"],
        "reviewed_at": parsed["reviewed_at"],
        "final_signoff": parsed["final_signoff"],
    }
    if "attestations" in parsed:
        projection["attestations"] = deepcopy(parsed["attestations"])
    return projection


def _build_author_review_plan_from_parsed(
    project: Path,
    manifest: Mapping[str, Any],
    parsed: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, Any], str]:
    project = Path(project).resolve()
    actions_input = [
        item for item in parsed["decisions"]
        if item["decision"] in {
            "approve", "revise", "revise_approve", "exclude"
        }
    ]
    if (actions_input or parsed["final_signoff"]) and (
        not parsed["reviewer"] or not parsed["reviewed_at"]
    ):
        raise ReadingPackError(
            "author review plan with actions requires reviewer and reviewed_at in 99-final-signoff.md"
        )
    config = load_config(project)
    current = {
        language: load_language_data(project, language)
        for language in config["languages"]
    }
    prospective = deepcopy(current)
    current_lookup = _record_lookup(current)
    decision_by_key = {
        (item["language"], item["collection"], item["record_id"]): item
        for item in parsed["decisions"]
    }
    # Deletion must preserve multilingual parity and is deliberately explicit.
    for item in actions_input:
        if item["decision"] != "exclude":
            continue
        for language in config["languages"]:
            paired = decision_by_key.get((language, item["collection"], item["record_id"]))
            if paired is None or paired["decision"] != "exclude":
                raise ReadingPackError(
                    f"exclude {item['collection']}.{item['record_id']} in every configured language"
                )
    # Apply field corrections and removals in memory before linking translations.
    for item in actions_input:
        key = (item["language"], item["collection"], item["record_id"])
        record = _record_lookup(prospective).get(key)
        if record is None:
            raise ReadingPackError(f"author review record disappeared: {key}")
        if item["decision"] == "exclude":
            prospective[item["language"]][item["collection"]] = [
                candidate for candidate in prospective[item["language"]][item["collection"]]
                if candidate["id"] != item["record_id"]
            ]
            continue
        for field, change in item["corrections"].items():
            _apply_correction(record, field, change)
        if item["decision"] in {"approve", "revise_approve"}:
            record["status"] = "approved"
            if item["language"] != config["primary_language"]:
                record["translation_status"] = "approved"
        else:
            record["status"] = "draft"
            if item["language"] != config["primary_language"]:
                record["translation_status"] = "draft"
    # A substantive primary edit requires an explicit decision on every translation.
    primary = config["primary_language"]
    before_lookup = _record_lookup(current)
    after_lookup = _record_lookup(prospective)
    for item in actions_input:
        if (
            item["language"] != primary
            or item["decision"] not in {"revise", "revise_approve"}
        ):
            continue
        key = (primary, item["collection"], item["record_id"])
        before = before_lookup[key]
        after = after_lookup[key]
        if _review_record_hash(before) == _review_record_hash(after):
            continue
        for language in config["languages"]:
            if language == primary:
                continue
            paired = decision_by_key.get((language, item["collection"], item["record_id"]))
            if paired is None or paired["decision"] not in {
                "approve", "revise", "revise_approve"
            }:
                raise ReadingPackError(
                    f"primary correction {item['collection']}.{item['record_id']} "
                    f"requires approve or revise in {language} "
                    "(revise_approve is also accepted)"
                )
    # Explicit translation decisions bind the translated record to the prospective primary.
    after_lookup = _record_lookup(prospective)
    for item in actions_input:
        if item["language"] == primary or item["decision"] == "exclude":
            continue
        source = after_lookup.get((primary, item["collection"], item["record_id"]))
        target = after_lookup.get((item["language"], item["collection"], item["record_id"]))
        if source is None or target is None:
            raise ReadingPackError(
                f"translation review cannot find primary pair for {item['collection']}.{item['record_id']}"
            )
        target["source_id"] = source["id"]
        target["source_hash"] = semantic_hash(source)
        target["translation_status"] = (
            "approved"
            if item["decision"] in {"approve", "revise_approve"}
            else "draft"
        )
    issues = validate_data_set(config, prospective)
    fatal = errors(issues)
    if fatal:
        first = fatal[0]
        raise ReadingPackError(
            f"author review changes would invalidate canonical data: {first.code} {first.path}: {first.message}"
        )
    actions: list[dict[str, Any]] = []
    field_overrides: list[dict[str, Any]] = []
    before_lookup = _record_lookup(current)
    after_lookup = _record_lookup(prospective)
    for item in actions_input:
        key = (item["language"], item["collection"], item["record_id"])
        before = before_lookup[key]
        after = after_lookup.get(key)
        action = {
            "unit_id": item["unit_id"],
            "language": item["language"],
            "collection": item["collection"],
            "module": item["module"],
            "module_state_sha256": item["module_state_sha256"],
            "record_id": item["record_id"],
            # A signed exact revision is stored as an approval action with
            # non-empty changed_fields.  This keeps the body-free public plan
            # schema backward compatible while recording the final state.
            "decision": (
                "approve"
                if item["decision"] == "revise_approve"
                else item["decision"]
            ),
            "before_sha256": semantic_hash(before),
            "after_sha256": semantic_hash(after) if after is not None else None,
            "before_status": before.get("status"),
            "after_status": after.get("status") if after is not None else None,
            "before_translation_status": before.get("translation_status"),
            "after_translation_status": after.get("translation_status") if after is not None else None,
            "changed_fields": sorted(item["corrections"]),
            "comment_sha256": _text_hash(item["comment"]),
        }
        actions.append(action)
        if item["collection"] == "chapters":
            for field, module in FIELD_MODULES.items():
                before_value = before.get(field, [] if field == "terms" else "")
                after_value = (
                    after.get(field, [] if field == "terms" else "")
                    if after is not None else None
                )
                before_digest = _field_hash(item["record_id"], field, before_value)
                after_digest = (
                    _field_hash(item["record_id"], field, after_value)
                    if after is not None else None
                )
                if before_digest != after_digest:
                    author_state = _author_input_state(project, config)
                    field_overrides.append({
                        "language": item["language"],
                        "module": module,
                        "module_state_sha256": _module_binding(
                            author_state, item["language"], module
                        ),
                        "record_id": item["record_id"],
                        "field": field,
                        "before_sha256": before_digest,
                        "after_sha256": after_digest,
                    })
    if parsed["final_signoff"]:
        unresolved = [
            item for item in parsed["decisions"]
            if item["decision"] not in {
                "approve", "revise_approve", "exclude"
            }
        ]
        if unresolved:
            raise ReadingPackError(
                "final signoff requires approve or exclude for every review record"
            )
        if any(action["decision"] == "revise" for action in actions):
            raise ReadingPackError("final signoff cannot include revisions")
        for language, data in prospective.items():
            for collection in COLLECTIONS:
                for record in data.get(collection, []):
                    if record.get("status") != "approved":
                        raise ReadingPackError(
                            f"final signoff leaves {language}.{collection}.{record['id']} unapproved"
                        )
                    if language != primary and record.get("translation_status") != "approved":
                        raise ReadingPackError(
                            f"final signoff leaves {language}.{collection}.{record['id']} translation unapproved"
                        )
    config_text_after = _config_with_author_review(project, parsed["final_signoff"])
    config_text_before = _read_text(
        project / "reading-pack.toml", 4 * 1024 * 1024, "project config"
    )
    if not actions and config_text_after == config_text_before:
        raise ReadingPackError("author review plan contains no applicable decisions")
    plan: dict[str, Any] = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "plan_id": "",
        "review_id": manifest["review_id"],
        "manifest_sha256": _json_hash(manifest),
        "decisions_sha256": _json_hash(_decision_projection(parsed)),
        "slug": config["slug"],
        "reviewer": parsed["reviewer"],
        "reviewed_at": parsed["reviewed_at"],
        "canonical_state_sha256_before": _full_state_hash(current),
        "canonical_data_sha256_before": canonical_data_hash(current),
        "canonical_state_sha256_after": _full_state_hash(prospective),
        "canonical_data_sha256_after": canonical_data_hash(prospective),
        "config_sha256_before": _text_hash(config_text_before),
        "config_sha256_after": _text_hash(config_text_after),
        "final_signoff": parsed["final_signoff"],
        "actions": actions,
        "field_overrides": field_overrides,
        "summary": {
            "approve": sum(action["decision"] == "approve" for action in actions),
            "revise": sum(action["decision"] == "revise" for action in actions),
            "exclude": sum(action["decision"] == "exclude" for action in actions),
            "unchanged": len(parsed["decisions"]) - len(actions),
        },
    }
    plan["plan_id"] = _plan_id(plan)
    state_entry = {
        "review_id": plan["review_id"],
        "plan_id": plan["plan_id"],
        "reviewer": plan["reviewer"],
        "reviewed_at": plan["reviewed_at"],
        "manifest_sha256": plan["manifest_sha256"],
        "decisions_sha256": plan["decisions_sha256"],
        "canonical_sha256_before": plan["canonical_data_sha256_before"],
        "canonical_sha256_after": plan["canonical_data_sha256_after"],
        "final_signoff": plan["final_signoff"],
        "actions": [
            {
                "language": action["language"],
                "collection": action["collection"],
                "module": action["module"],
                "module_state_sha256": action["module_state_sha256"],
                "record_id": action["record_id"],
                "decision": action["decision"],
                "before_sha256": action["before_sha256"],
                "after_sha256": action["after_sha256"],
                "after_status": action["after_status"],
                "after_translation_status": action["after_translation_status"],
            }
            for action in actions
        ],
        "field_overrides": deepcopy(field_overrides),
    }
    return plan, prospective, state_entry, config_text_after


def validate_author_review_plan(value: Any) -> dict[str, Any]:
    require_structure("author-review-plan.schema.json", value, label="author review plan")
    if not isinstance(value, Mapping):
        raise ReadingPackError("invalid author review plan: root must be an object")
    fields = {
        "schema_version", "plan_id", "review_id", "manifest_sha256",
        "decisions_sha256", "slug", "reviewer", "reviewed_at",
        "canonical_state_sha256_before", "canonical_data_sha256_before",
        "canonical_state_sha256_after", "canonical_data_sha256_after",
        "config_sha256_before", "config_sha256_after", "final_signoff",
        "actions", "field_overrides", "summary",
    }
    _require_keys(value, fields, fields, "author review plan")
    if value["schema_version"] != PLAN_SCHEMA_VERSION:
        raise ReadingPackError("invalid author review plan: unsupported schema version")
    if not isinstance(value["plan_id"], str) or not _PLAN_ID.fullmatch(value["plan_id"]):
        raise ReadingPackError("invalid author review plan: plan_id")
    if not isinstance(value["review_id"], str) or not _REVIEW_ID.fullmatch(value["review_id"]):
        raise ReadingPackError("invalid author review plan: review_id")
    for field in (
        "manifest_sha256", "decisions_sha256", "canonical_state_sha256_before",
        "canonical_data_sha256_before", "canonical_state_sha256_after",
        "canonical_data_sha256_after", "config_sha256_before", "config_sha256_after",
    ):
        if not isinstance(value[field], str) or not _SHA256.fullmatch(value[field]):
            raise ReadingPackError(f"invalid author review plan: {field}")
    if not isinstance(value["slug"], str) or not value["slug"]:
        raise ReadingPackError("invalid author review plan: slug")
    if not isinstance(value["reviewer"], str) or not _SAFE_LINE.fullmatch(value["reviewer"]):
        raise ReadingPackError("invalid author review plan: reviewer")
    try:
        date.fromisoformat(value["reviewed_at"])
    except (TypeError, ValueError) as exc:
        raise ReadingPackError("invalid author review plan: reviewed_at") from exc
    if not isinstance(value["final_signoff"], bool):
        raise ReadingPackError("invalid author review plan: final_signoff")
    if not isinstance(value["actions"], list) or not isinstance(value["field_overrides"], list):
        raise ReadingPackError("invalid author review plan: action arrays")
    # Reuse state validation for the body-free action shapes.
    state_probe = {
        "schema_version": STATE_SCHEMA_VERSION,
        "reviews": [{
            "review_id": value["review_id"],
            "plan_id": value["plan_id"],
            "reviewer": value["reviewer"],
            "reviewed_at": value["reviewed_at"],
            "manifest_sha256": value["manifest_sha256"],
            "decisions_sha256": value["decisions_sha256"],
            "canonical_sha256_before": value["canonical_data_sha256_before"],
            "canonical_sha256_after": value["canonical_data_sha256_after"],
            "final_signoff": value["final_signoff"],
            "actions": [
                {
                    key: action[key]
                    for key in (
                        "language", "collection", "module", "module_state_sha256",
                        "record_id", "decision", "before_sha256", "after_sha256",
                        "after_status", "after_translation_status",
                    )
                }
                for action in value["actions"]
            ],
            "field_overrides": value["field_overrides"],
        }],
    }
    validate_author_review_state(state_probe)
    action_fields = {
        "unit_id", "language", "collection", "module", "module_state_sha256",
        "record_id", "decision", "before_sha256", "after_sha256",
        "before_status", "after_status", "before_translation_status",
        "after_translation_status", "changed_fields", "comment_sha256",
    }
    for index, action in enumerate(value["actions"]):
        if not isinstance(action, Mapping):
            raise ReadingPackError(f"invalid author review plan action[{index}]")
        _require_keys(action, action_fields, action_fields, f"author review plan action[{index}]")
        if not isinstance(action["unit_id"], str) or not _UNIT_ID.fullmatch(action["unit_id"]):
            raise ReadingPackError(f"invalid author review plan action[{index}]: unit_id")
        if not isinstance(action["changed_fields"], list) or any(
            not isinstance(field, str) for field in action["changed_fields"]
        ):
            raise ReadingPackError(f"invalid author review plan action[{index}]: changed_fields")
        if not isinstance(action["comment_sha256"], str) or not _SHA256.fullmatch(action["comment_sha256"]):
            raise ReadingPackError(f"invalid author review plan action[{index}]: comment_sha256")
    summary = value["summary"]
    if not isinstance(summary, Mapping) or set(summary) != {"approve", "revise", "exclude", "unchanged"} or any(
        not isinstance(count, int) or isinstance(count, bool) or count < 0
        for count in summary.values()
    ):
        raise ReadingPackError("invalid author review plan: summary")
    checked = deepcopy(dict(value))
    if _plan_id(checked) != checked["plan_id"]:
        raise ReadingPackError("invalid author review plan: plan_id does not match content")
    return checked


def write_author_review_plan(path: Path, plan: Mapping[str, Any]) -> None:
    checked = validate_author_review_plan(plan)
    target = Path(path).resolve()
    if target.exists():
        raise ReadingPackError(f"refusing to overwrite author review plan: {target}", EXIT_IO)
    if target.name in {"reading-pack.toml", MANIFEST_NAME, STATE_NAME} or target.parent.name in {"data", "dist", "templates"}:
        raise ReadingPackError("refusing to write author review plan over project data", EXIT_IO)
    write_json(target, checked)


def load_author_review_plan(path: Path) -> dict[str, Any]:
    return validate_author_review_plan(
        _strict_json(Path(path).resolve(), MAX_PLAN_BYTES, "author review plan")
    )


def _review_transaction_path(path: str, kind: str) -> bool:
    return (
        (kind == "json" and re.fullmatch(r"data/pack\.(?:ja|en)\.json", path) is not None)
        or (kind == "json" and path == STATE_NAME)
        or (kind == "text" and path == "reading-pack.toml")
    )


def _apply_author_review_plan_with_builder(
    project: Path,
    plan: Mapping[str, Any],
    builder: Callable[
        [Path],
        tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, Any], str],
    ],
) -> dict[str, Any]:
    project = Path(project).resolve()
    checked = validate_author_review_plan(plan)
    with project_lock(project):
        recover_artifact_transaction(
            project,
            prepared_name=PREPARED_NAME,
            path_policy=_review_transaction_path,
            label="author review",
            maximum_bytes=MAX_STATE_BYTES * 8,
        )
        current_plan, prospective, state_entry, config_after = builder(project)
        if current_plan != checked:
            raise ReadingPackError(
                "author review, plan, or canonical data changed after planning; create a fresh plan"
            )
        config = load_config(project)
        before_data = {
            language: load_language_data(project, language)
            for language in config["languages"]
        }
        before_state = load_author_review_state(project)
        reviews = [
            item for item in before_state["reviews"]
            if item["plan_id"] != checked["plan_id"]
        ]
        reviews.append(state_entry)
        if len(reviews) > MAX_HISTORY:
            reviews = reviews[-MAX_HISTORY:]
        prospective_state = validate_author_review_state(
            {"schema_version": STATE_SCHEMA_VERSION, "reviews": reviews}
        )
        before_config = _read_text(
            project / "reading-pack.toml", 4 * 1024 * 1024, "project config"
        )
        changed_data = {
            f"data/pack.{language}.json": before_data[language]
            for language in config["languages"]
            if before_data[language] != prospective[language]
        }
        changes = [
            ArtifactChange(
                path=relative,
                kind="json",
                before=before,
                after=prospective[
                    relative.removeprefix("data/pack.").removesuffix(".json")
                ],
            )
            for relative, before in changed_data.items()
        ]
        changes.extend(
            [
                ArtifactChange(
                    STATE_NAME,
                    "json",
                    before_state,
                    prospective_state,
                    before_exists=(project / STATE_NAME).exists(),
                ),
                ArtifactChange(
                    "reading-pack.toml", "text", before_config, config_after
                ),
            ]
        )

        def validate_applied_review() -> None:
            _, _, issues = validate_project(project)
            fatal = errors(issues)
            if fatal:
                first = fatal[0]
                raise ReadingPackError(
                    f"author review apply failed validation: "
                    f"{first.code} {first.path}: {first.message}"
                )

        apply_artifact_transaction(
            project,
            prepared_name=PREPARED_NAME,
            changes=changes,
            path_policy=_review_transaction_path,
            label="author review",
            validate_after=validate_applied_review,
        )
    return {
        "review_id": checked["review_id"],
        "plan_id": checked["plan_id"],
        "summary": deepcopy(checked["summary"]),
        "final_signoff": checked["final_signoff"],
    }


def _effective_chain(
    reviews: list[Mapping[str, Any]],
    *,
    list_name: str,
    language: str,
    module: str,
    module_state_sha256: str,
    record_id: str,
    initial_sha256: str | None,
) -> tuple[str | None, Mapping[str, Any] | None]:
    current = initial_sha256
    latest: Mapping[str, Any] | None = None
    for review in reviews:
        for item in review[list_name]:
            if (
                item["language"] == language
                and item["module"] == module
                and item["module_state_sha256"] == module_state_sha256
                and item["record_id"] == record_id
                and item["before_sha256"] == current
            ):
                current = item["after_sha256"]
                latest = item
    return current, latest


def author_review_consistency_findings(
    data_by_language: Mapping[str, Mapping[str, Any]],
    review_state: Mapping[str, Any],
    author_input_state: Mapping[str, Any],
) -> list[tuple[str, str, str]]:
    """Validate that applicable review decisions still describe canonical data."""

    findings: list[tuple[str, str, str]] = []
    reviews = list(review_state["reviews"])
    for language, data in data_by_language.items():
        for collection in COLLECTIONS:
            module = COLLECTION_MODULES[collection]
            binding = _module_binding(author_input_state, language, module)
            actual = {
                record["id"]: record for record in data.get(collection, [])
            }
            keys = {
                item["record_id"]
                for review in reviews
                for item in review["actions"]
                if item["language"] == language
                and item["collection"] == collection
                and item["module_state_sha256"] == binding
            }
            for record_id in keys:
                # Start at the first applicable action's before hash. Subsequent
                # actions must form an unbroken chain for the same AIP baseline.
                candidates = [
                    item
                    for review in reviews
                    for item in review["actions"]
                    if item["language"] == language
                    and item["collection"] == collection
                    and item["module_state_sha256"] == binding
                    and item["record_id"] == record_id
                ]
                if not candidates:
                    continue
                expected, latest = _effective_chain(
                    reviews,
                    list_name="actions",
                    language=language,
                    module=module,
                    module_state_sha256=binding,
                    record_id=record_id,
                    initial_sha256=candidates[0]["before_sha256"],
                )
                record = actual.get(record_id)
                actual_hash = semantic_hash(record) if record is not None else None
                if expected != actual_hash:
                    findings.append((
                        "RP505",
                        f"{STATE_NAME}.{language}.{collection}.{record_id}",
                        "canonical record differs from its latest applicable author review",
                    ))
                    continue
                if latest is not None and record is not None:
                    if record.get("status") != latest["after_status"]:
                        findings.append((
                            "RP505",
                            f"data/pack.{language}.json.{collection}.{record_id}.status",
                            "record review status differs from its latest author decision",
                        ))
                    if latest["after_translation_status"] is not None and record.get("translation_status") != latest["after_translation_status"]:
                        findings.append((
                            "RP505",
                            f"data/pack.{language}.json.{collection}.{record_id}.translation_status",
                            "translation review status differs from its latest author decision",
                        ))
    return findings


def review_overrides_for_author_input(
    review_state: Mapping[str, Any],
    *,
    language: str,
    module: str,
    module_state_sha256: str,
    initial_hashes: Mapping[str, str],
    field: str | None = None,
) -> dict[str, str]:
    """Return AIP expected hashes after applicable, hash-chained author reviews."""

    current = dict(initial_hashes)
    list_name = "field_overrides" if field is not None else "actions"
    for review in review_state["reviews"]:
        for item in review[list_name]:
            if (
                item["language"] != language
                or item["module"] != module
                or item["module_state_sha256"] != module_state_sha256
                or (field is not None and item.get("field") != field)
            ):
                continue
            record_id = item["record_id"]
            if current.get(record_id) != item["before_sha256"]:
                continue
            if item["after_sha256"] is None:
                current.pop(record_id, None)
            else:
                current[record_id] = item["after_sha256"]
    return current
