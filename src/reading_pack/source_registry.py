"""Body-free registry for every source used to build a Reading Pack.

The legacy language-pack ``source`` field remains the canonical manuscript
binding.  This registry complements it with explicitly typed support sources
such as author Q&A, errata, and bibliography files.  It stores no filesystem
paths and no source excerpts.
"""

from __future__ import annotations

import hashlib
import json
import re
import stat
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from .errors import EXIT_IO, ReadingPackError
from .importers import MAX_SOURCE_BYTES
from .project import project_lock, write_json
from .schema_validation import require_structure


REGISTRY_NAME = "sources.json"
REGISTRY_SCHEMA_VERSION = 1
SOURCE_PLAN_SCHEMA_VERSION = 1
MAX_REGISTRY_BYTES = 4 * 1024 * 1024
MAX_PLAN_BYTES = 1024 * 1024
MAX_SOURCES = 1_000
SOURCE_ROLES = {
    "primary-book",
    "author-qa",
    "author-canon",
    "author-data",
    "errata",
    "bibliography",
    "publisher-metadata",
    "translation",
}
SOURCE_FORMATS = {
    "markdown", "org", "json", "csv", "epub3", "pdf", "pdf-vertical", "text"
}
SOURCE_LANGUAGES = {"ja", "en", "und"}
_SAFE_NAME = re.compile(r"[^\x00-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]{1,500}")


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


def _bounded_json(path: Path, maximum: int, label: str) -> Any:
    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate object key: {key}")
            result[key] = value
        return result

    try:
        if path.stat().st_size > maximum:
            raise ReadingPackError(f"{label} exceeds {maximum} bytes", EXIT_IO)
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=object_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(
                ValueError("non-finite JSON number")
            ),
        )
    except ReadingPackError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReadingPackError(f"cannot read {label} {path}: {exc}", EXIT_IO) from exc


def fingerprint_source(path: Path) -> dict[str, Any]:
    """Return a race-checked file fingerprint without retaining its path."""

    path = Path(path).resolve()
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


def infer_source_format(path: Path, explicit_format: str | None = None) -> str:
    if explicit_format is not None:
        if explicit_format not in SOURCE_FORMATS:
            raise ReadingPackError(f"unsupported source format: {explicit_format}")
        return explicit_format
    suffix = Path(path).suffix.lower()
    formats = {
        ".md": "markdown",
        ".markdown": "markdown",
        ".org": "org",
        ".json": "json",
        ".csv": "csv",
        ".epub": "epub3",
        ".pdf": "pdf",
        ".txt": "text",
        ".text": "text",
    }
    try:
        return formats[suffix]
    except KeyError as exc:
        raise ReadingPackError(
            f"cannot infer source format from {Path(path).name}; pass an explicit format"
        ) from exc


def _validate_source_record(value: Any, label: str = "source") -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ReadingPackError(f"invalid {label}: must be an object")
    if value["id"] == "SRC-1" and value["role"] != "primary-book":
        raise ReadingPackError(f"invalid {label}: SRC-1 is reserved for the primary book")
    if value["id"] != "SRC-1" and value["role"] == "primary-book":
        raise ReadingPackError(f"invalid {label}: primary-book is reserved for SRC-1")
    name = value["name"]
    if (
        not isinstance(name, str)
        or not _SAFE_NAME.fullmatch(name)
        or Path(name).name != name
    ):
        raise ReadingPackError(f"invalid {label}: source name is invalid")
    return deepcopy(dict(value))


def validate_source_record(value: Any, label: str = "source") -> dict[str, Any]:
    """Validate and detach one body-free source identity record."""

    require_structure(
        "source-plan.schema.json",
        {"schema_version": 1, "plan_id": "SP-00000000000000000000", "source": value},
        label=label,
    )
    return _validate_source_record(value, label)


def _plan_id(plan: Mapping[str, Any]) -> str:
    body = {key: value for key, value in plan.items() if key != "plan_id"}
    payload = json.dumps(
        body, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return f"SP-{hashlib.sha256(payload).hexdigest()[:20].upper()}"


def create_source_plan(
    source_path: Path,
    *,
    source_id: str,
    role: str,
    language: str = "und",
    explicit_format: str | None = None,
) -> dict[str, Any]:
    """Create a deterministic source-registration plan containing no body text."""

    fingerprint = fingerprint_source(source_path)
    source = validate_source_record(
        {
            "id": source_id,
            "role": role,
            "language": language,
            "format": infer_source_format(source_path, explicit_format),
            **fingerprint,
        }
    )
    plan = {
        "schema_version": SOURCE_PLAN_SCHEMA_VERSION,
        "plan_id": "",
        "source": source,
    }
    plan["plan_id"] = _plan_id(plan)
    validate_source_plan(plan)
    return plan


def validate_source_plan(value: Any) -> dict[str, Any]:
    require_structure("source-plan.schema.json", value, label="source plan")
    if not isinstance(value, Mapping):
        raise ReadingPackError("invalid source plan: root must be an object")
    required = {"schema_version", "plan_id", "source"}
    _require_keys(value, required, required, "source plan")
    if value["schema_version"] != SOURCE_PLAN_SCHEMA_VERSION:
        raise ReadingPackError("invalid source plan: unsupported schema version")
    if not isinstance(value["plan_id"], str) or not re.fullmatch(
        r"SP-[A-F0-9]{20}", value["plan_id"]
    ):
        raise ReadingPackError("invalid source plan: plan ID is invalid")
    _validate_source_record(value["source"], "source plan source")
    if value["plan_id"] != _plan_id(value):
        raise ReadingPackError("source plan checksum does not match its contents")
    return deepcopy(dict(value))


def write_source_plan(path: Path, plan: Mapping[str, Any]) -> None:
    validate_source_plan(plan)
    path = Path(path).resolve()
    if path.exists():
        raise ReadingPackError(f"refusing to overwrite existing source plan: {path}", EXIT_IO)
    if path.name in {"reading-pack.toml", "quality-plan.json", "sources.json"} or (
        path.parent.name in {"data", "templates", "dist"}
    ):
        raise ReadingPackError(
            "refusing to write a source plan over a canonical or generated project path",
            EXIT_IO,
        )
    source_name = plan["source"]["name"]
    if path.name == source_name and path.parent == Path.cwd().resolve():
        raise ReadingPackError("source plan output must not overwrite its source", EXIT_IO)
    write_json(path, dict(plan))


def load_source_plan(path: Path) -> dict[str, Any]:
    return validate_source_plan(_bounded_json(Path(path), MAX_PLAN_BYTES, "source plan"))


def validate_source_registry(value: Any) -> dict[str, Any]:
    require_structure("source-registry.schema.json", value, label="source registry")
    if not isinstance(value, Mapping):
        raise ReadingPackError("invalid source registry: root must be an object")
    required = {"schema_version", "sources"}
    _require_keys(value, required, required, "source registry")
    if value["schema_version"] != REGISTRY_SCHEMA_VERSION:
        raise ReadingPackError("invalid source registry: unsupported schema version")
    raw_sources = value["sources"]
    if not isinstance(raw_sources, list) or len(raw_sources) > MAX_SOURCES:
        raise ReadingPackError(f"invalid source registry: sources must contain at most {MAX_SOURCES} items")
    sources = [
        _validate_source_record(item, f"source registry sources[{index}]")
        for index, item in enumerate(raw_sources)
    ]
    identifiers = [item["id"] for item in sources]
    if len(identifiers) != len(set(identifiers)):
        raise ReadingPackError("invalid source registry: duplicate source IDs")
    identities = [(item["name"], item["sha256"]) for item in sources]
    if len(identities) != len(set(identities)):
        raise ReadingPackError("invalid source registry: a source is registered more than once")
    return {"schema_version": REGISTRY_SCHEMA_VERSION, "sources": sources}


def load_source_registry(project: Path) -> dict[str, Any]:
    path = Path(project).resolve() / REGISTRY_NAME
    if not path.exists():
        return {"schema_version": REGISTRY_SCHEMA_VERSION, "sources": []}
    return validate_source_registry(
        _bounded_json(path, MAX_REGISTRY_BYTES, "source registry")
    )


def list_sources(project: Path) -> list[dict[str, Any]]:
    registry = load_source_registry(project)
    return deepcopy(sorted(registry["sources"], key=lambda item: item["id"]))


def apply_source_plan(
    project: Path, plan: Mapping[str, Any], source_path: Path
) -> dict[str, Any]:
    """Register or update one source after rechecking the exact source bytes."""

    project = Path(project).resolve()
    checked = validate_source_plan(plan)
    source = checked["source"]
    current = {
        **fingerprint_source(source_path),
        "format": infer_source_format(source_path, source["format"]),
    }
    expected = {
        key: source[key] for key in ("name", "sha256", "size_bytes", "format")
    }
    if current != expected:
        raise ReadingPackError("source does not match the reviewed source plan")
    if source["role"] == "primary-book":
        from .project import load_config, load_language_data

        config = load_config(project)
        language = config.get("primary_language")
        canonical_source = load_language_data(project, language).get("source", {})
        if (
            canonical_source.get("name") != source["name"]
            or canonical_source.get("sha256") != source["sha256"]
            or canonical_source.get("format") != source["format"]
        ):
            raise ReadingPackError(
                "primary-book registration does not match canonical source for "
                + str(language)
            )
    with project_lock(project):
        registry = load_source_registry(project)
        sources = registry["sources"]
        duplicate = next(
            (
                item
                for item in sources
                if item["id"] != source["id"]
                and item["name"] == source["name"]
                and item["sha256"] == source["sha256"]
            ),
            None,
        )
        if duplicate is not None:
            raise ReadingPackError(
                f"source is already registered as {duplicate['id']}"
            )
        updated = [item for item in sources if item["id"] != source["id"]]
        updated.append(deepcopy(source))
        registry = validate_source_registry(
            {"schema_version": REGISTRY_SCHEMA_VERSION, "sources": updated}
        )
        registry["sources"].sort(key=lambda item: item["id"])
        write_json(project / REGISTRY_NAME, registry)
    return deepcopy(source)


def registered_source(project: Path, source_id: str) -> dict[str, Any]:
    for source in list_sources(project):
        if source["id"] == source_id:
            return source
    raise ReadingPackError(f"source is not registered: {source_id}")


def verify_registered_source(
    project: Path,
    source_id: str,
    source_path: Path,
    *,
    expected_role: str | None = None,
) -> dict[str, Any]:
    """Revalidate a registered source against the file supplied for this run."""

    source = registered_source(project, source_id)
    if expected_role is not None and source["role"] != expected_role:
        raise ReadingPackError(
            f"source {source_id} has role {source['role']}, expected {expected_role}"
        )
    current = fingerprint_source(source_path)
    if any(current[key] != source[key] for key in ("name", "sha256", "size_bytes")):
        raise ReadingPackError(f"registered source is stale or mismatched: {source_id}")
    return source
