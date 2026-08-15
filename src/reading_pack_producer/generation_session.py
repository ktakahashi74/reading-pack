"""Resumable, model-neutral orchestration for bounded generation work.

The session stores only bindings and response hashes.  Private response files
may temporarily contain short evidence snippets; finalization feeds them into
the existing candidate-run verifier and removes them after the excerpt-free
candidate manifest and reconciled work ledger are durable.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import stat
import tempfile
from importlib import resources
from pathlib import Path
from typing import Any, Mapping, Sequence

from reading_pack.errors import EXIT_IO, ReadingPackError
from reading_pack.hashing import file_hash
from reading_pack.importers import read_regular_source_bytes
from reading_pack.project import (
    atomic_write_text,
    find_project,
    load_config,
    load_language_data,
)
from reading_pack.schema_validation import require_structure, schema_document
from reading_pack_review.author_input import load_author_input_state

from .candidates import (
    LeakPolicy,
    _SourceCopyIndex,
    _copy_risk,
    _source_text_snapshot,
    create_candidate_run,
    load_candidate_run,
    normalize_text,
    run_local_adapter,
)
from .catalog_extraction import _validated_chapter_spans, load_chapter_spans
from .work_ledger import (
    CANDIDATE_COLLECTIONS,
    MODULES,
    artifact_hash,
    create_work_ledger,
    reconcile_work_results,
    validate_work_ledger,
    write_work_ledger,
)


SESSION_SCHEMA_VERSION = 1
RESPONSE_SCHEMA_VERSION = 1
SESSION_NAME = "session.json"
LEDGER_NAME = "ledger.json"
RESPONSES_DIRECTORY = "responses"
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_RECORDS_PER_RESPONSE = 500
MAX_GENERATED_GLOSSARY_MEANING_CHARACTERS = 500
AUTO_MODULES = (
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
SESSION_MODULES = (*AUTO_MODULES, "misreadings")
COVERAGE_MODULES = (
    "summaries",
    "chapter_terms",
    "claims",
    "names",
    "glossary",
)
COVERAGE_RUBRIC = {
    "policy": "whole_book_gap_audit_v1",
    "modules": {
        "summaries": [
            "central_question",
            "argument_or_position",
            "mechanism_or_derivation",
            "material_qualification",
        ],
        "chapter_terms": [
            "retrieval_concepts",
            "distinctive_source_terms",
        ],
        "claims": [
            "descriptive_and_normative",
            "mechanism_and_conditions",
            "attribution_and_uncertainty",
        ],
        "names": [
            "material_people",
            "book_specific_context",
            "alias_deduplication",
        ],
        "glossary": [
            "argument_bearing_concepts",
            "book_specific_meaning",
            "abstractive_bounded_meaning",
            "acronym_and_alias_deduplication",
        ],
    },
}

_SESSION_ID = re.compile(r"GS-[A-F0-9]{20}")
_REASON_CODE = re.compile(r"[a-z][a-z0-9_]{0,99}")
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]")


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _strict_json_loads(raw: bytes, label: str) -> Any:
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


def _read_regular_bounded(path: Path, maximum: int, label: str) -> bytes:
    path = path.resolve()
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            raise ReadingPackError(f"{label} must be a regular file", EXIT_IO)
        if info.st_size > maximum:
            raise ReadingPackError(f"{label} exceeds {maximum} bytes", EXIT_IO)
        raw = path.read_bytes()
    except ReadingPackError:
        raise
    except OSError as exc:
        raise ReadingPackError(f"cannot read {label} {path}: {exc}", EXIT_IO) from exc
    if len(raw) != info.st_size:
        raise ReadingPackError(f"{label} changed while it was read", EXIT_IO)
    return raw


def _integrity(value: Mapping[str, Any]) -> str:
    projection = {key: item for key, item in value.items() if key != "integrity_sha256"}
    return artifact_hash(projection)


def _session_summary(items: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    ingested = sum(item.get("response") is not None for item in items)
    return {
        "total": len(items),
        "awaiting_response": len(items) - ingested,
        "ingested": ingested,
    }


def _safe_generator(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) - {"adapter", "model", "revision"}:
        raise ReadingPackError("generation response generator metadata is invalid")
    if set(value) < {"adapter", "model"}:
        raise ReadingPackError("generation response generator metadata is incomplete")
    result: dict[str, str] = {}
    for key in ("adapter", "model", "revision"):
        if key not in value:
            continue
        item = value[key]
        if (
            not isinstance(item, str)
            or len(item) > 500
            or _CONTROL.search(item) is not None
            or (key == "adapter" and not item)
        ):
            raise ReadingPackError("generation response generator metadata is invalid")
        result[key] = item
    return result


def _prompt_text(*, purpose: str = "initial") -> str:
    prompt_name = (
        "generation-coverage.md" if purpose == "coverage" else "generation-work.md"
    )
    try:
        return (
            resources.files("reading_pack")
            .joinpath(f"defaults/{prompt_name}")
            .read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError) as exc:
        raise ReadingPackError(f"generation work prompt is unavailable: {exc}") from exc


def _module_is_unfilled(module: str, data: Mapping[str, Any]) -> bool:
    chapters = data.get("chapters", [])
    if module == "chapters":
        return not isinstance(chapters, list) or not chapters
    if module == "summaries":
        return not isinstance(chapters, list) or any(
            not isinstance(item, Mapping) or not str(item.get("summary", "")).strip()
            for item in chapters
        )
    if module == "chapter_terms":
        return not isinstance(chapters, list) or any(
            not isinstance(item, Mapping) or not item.get("terms") for item in chapters
        )
    collection = CANDIDATE_COLLECTIONS[module]
    records = data.get(collection)
    return not isinstance(records, list) or not records


def generation_modules_for_project(
    project: Path,
    language: str,
    data: Mapping[str, Any] | None = None,
    *,
    purpose: str = "initial",
) -> list[str]:
    """Select initial generation gaps or the fixed post-draft coverage surface."""

    project = find_project(project)
    config = load_config(project)
    canonical = data if data is not None else load_language_data(project, language)
    if purpose == "coverage":
        # Coverage is deliberately post-draft: provided or already-filled
        # modules are exactly what it audits.  Reusing the initial-generation
        # gap filter here would make the documented default invocation fail
        # whenever an AIP supplied all five coverage modules.
        return list(COVERAGE_MODULES)
    if purpose != "initial":
        raise ReadingPackError(f"unsupported generation session purpose: {purpose}")
    state = load_author_input_state(project, config)
    language_state = state["languages"].get(language)
    if not isinstance(language_state, Mapping):
        raise ReadingPackError(f"author input state has no language {language}")
    declarations = language_state.get("modules", {})
    selected: list[str] = []
    for module in AUTO_MODULES:
        declaration = declarations.get(module)
        mode = declaration.get("mode") if isinstance(declaration, Mapping) else None
        if mode in {"generate", "augment"} or (
            mode not in {"provided", "omit"} and _module_is_unfilled(module, canonical)
        ):
            selected.append(module)
    if not selected:
        raise ReadingPackError("no AIP-generated or unfilled modules require generation")
    return selected


def _chapter_range(item: Mapping[str, Any], chapters: Mapping[str, Mapping[str, Any]]) -> dict[str, str]:
    scope = item["scope"]
    if scope.get("kind") == "book":
        return {"kind": "book"}
    chapter_id = scope["chapter_id"]
    chapter = chapters.get(chapter_id)
    if chapter is None:
        raise ReadingPackError(f"work item references missing chapter: {chapter_id}")
    pages = chapter.get("pages", "")
    if not isinstance(pages, str) or len(pages) > 100:
        raise ReadingPackError(f"chapter {chapter_id} has an invalid page range")
    return {"kind": "chapter", "chapter_id": chapter_id, "pages": pages}


def _session_projection(session: Mapping[str, Any]) -> dict[str, Any]:
    projection = {
        "project": session["project"],
        "language": session["language"],
        "source": session["source"],
        "canonical_data_sha256": session["canonical_data_sha256"],
        "ledger": session["ledger"],
        "items": [
            {
                "work_id": item["work_id"],
                "module": item["module"],
                "scope": item["scope"],
                "chapter_range": item["chapter_range"],
            }
            for item in session["items"]
        ],
    }
    # Keep the projection for pre-coverage sessions byte-for-byte compatible.
    if "purpose" in session:
        projection["purpose"] = session["purpose"]
        projection["coverage_rubric"] = session["coverage_rubric"]
    if "chapter_spans" in session:
        projection["chapter_spans"] = session["chapter_spans"]
    return projection


def _session_id(session: Mapping[str, Any]) -> str:
    return f"GS-{artifact_hash(_session_projection(session))[:20].upper()}"


def validate_generation_session(
    session: Mapping[str, Any], ledger: Mapping[str, Any]
) -> None:
    require_structure("generation-session.schema.json", session, label="generation session")
    validate_work_ledger(ledger)
    if session.get("schema_version") != SESSION_SCHEMA_VERSION:
        raise ReadingPackError("generation session schema_version is unsupported")
    purpose = session.get("purpose", "initial")
    if purpose == "coverage":
        if session.get("coverage_rubric") != COVERAGE_RUBRIC:
            raise ReadingPackError("generation session coverage rubric is unsupported")
        modules = {item.get("module") for item in session.get("items", [])}
        if not modules or not modules <= set(COVERAGE_MODULES):
            raise ReadingPackError("coverage session contains an unsupported module")
    elif purpose != "initial" or "coverage_rubric" in session:
        raise ReadingPackError("generation session purpose is unsupported")
    chapter_spans = session.get("chapter_spans")
    if chapter_spans is not None:
        seen_spans: set[str] = set()
        previous_end = 0
        for index, span in enumerate(chapter_spans):
            chapter_id = span.get("chapter_id")
            start = span.get("char_start")
            end = span.get("char_end")
            if (
                chapter_id in seen_spans
                or not isinstance(start, int)
                or isinstance(start, bool)
                or not isinstance(end, int)
                or isinstance(end, bool)
                or start < previous_end
                or end <= start
            ):
                raise ReadingPackError(
                    f"generation session chapter span {index} is invalid or unordered"
                )
            seen_spans.add(chapter_id)
            previous_end = end
        span_ids = {
            span.get("chapter_id")
            for span in chapter_spans
            if isinstance(span, Mapping)
        }
        scoped_ids = {
            item.get("scope", {}).get("chapter_id")
            for item in session.get("items", [])
            if item.get("scope", {}).get("kind") == "chapter"
        }
        if None in span_ids or not scoped_ids <= span_ids:
            raise ReadingPackError(
                "generation session chapter map does not cover every chapter-scoped item"
            )
    if session.get("session_id") != _session_id(session):
        raise ReadingPackError("generation session ID binding check failed")
    if session.get("language") != ledger.get("language"):
        raise ReadingPackError("generation session language does not match its ledger")
    if session.get("canonical_data_sha256") != ledger.get("canonical_data_sha256"):
        raise ReadingPackError("generation session canonical hash does not match its ledger")
    expected_ledger = {
        "plan_id": ledger.get("plan_id"),
        "integrity_sha256": ledger.get("integrity_sha256"),
    }
    if session.get("state") == "open" and session.get("ledger") != expected_ledger:
        raise ReadingPackError("open generation session ledger binding is stale")
    if session.get("state") == "finalized":
        final = session.get("finalization")
        if not isinstance(final, Mapping) or final.get("ledger_integrity_sha256") != ledger.get(
            "integrity_sha256"
        ):
            raise ReadingPackError("finalized generation session ledger binding is stale")
        if ledger.get("run") != {
            "run_id": final.get("run_id"),
            "integrity_sha256": final.get("run_integrity_sha256"),
        }:
            raise ReadingPackError("finalized generation session run binding is stale")
    raw_items = session.get("items")
    if not isinstance(raw_items, list) or len(raw_items) != len(ledger["items"]):
        raise ReadingPackError("generation session item set does not match its ledger")
    for expected, actual in zip(ledger["items"], raw_items):
        for key in ("work_id", "module", "scope"):
            if actual.get(key) != expected.get(key):
                raise ReadingPackError("generation session work binding does not match its ledger")
        response = actual.get("response")
        if response is not None:
            _safe_generator(response.get("generator"))
    if session.get("summary") != _session_summary(raw_items):
        raise ReadingPackError("generation session summary does not match its items")
    if session.get("integrity_sha256") != _integrity(session):
        raise ReadingPackError("generation session integrity check failed")


def _write_session(path: Path, session: Mapping[str, Any]) -> None:
    encoded = _json_bytes(session)
    atomic_write_text(path, encoded.decode("utf-8"))
    try:
        path.chmod(0o600)
    except OSError as exc:
        raise ReadingPackError(f"cannot restrict generation session permissions: {exc}") from exc


def create_generation_session(
    session_directory: Path,
    *,
    project: Path,
    language: str,
    source_path: Path,
    modules: Sequence[str] | None = None,
    purpose: str = "initial",
    chapter_map: Path | None = None,
) -> dict[str, Any]:
    project = find_project(project)
    config = load_config(project)
    canonical = load_language_data(project, language)
    source_path = source_path.resolve()
    canonical_source = canonical.get("source")
    if not isinstance(canonical_source, Mapping) or not canonical_source.get("sha256"):
        raise ReadingPackError("canonical source is not imported; apply a reviewed import plan first")
    source_digest = file_hash(read_regular_source_bytes(source_path))
    if (
        source_path.name != canonical_source.get("name")
        or source_digest != canonical_source.get("sha256")
    ):
        raise ReadingPackError("generation source does not match the imported canonical source")
    if purpose not in {"initial", "coverage"}:
        raise ReadingPackError(f"unsupported generation session purpose: {purpose}")
    selected = list(modules) if modules is not None else generation_modules_for_project(
        project, language, canonical, purpose=purpose
    )
    unknown = set(selected) - set(SESSION_MODULES)
    if unknown:
        raise ReadingPackError(f"unsupported generation session module(s): {', '.join(sorted(unknown))}")
    if purpose == "coverage":
        unsupported = set(selected) - set(COVERAGE_MODULES)
        if unsupported:
            raise ReadingPackError(
                "coverage sessions support only summaries, chapter_terms, claims, "
                "names, and glossary"
            )
    ledger = create_work_ledger(language=language, canonical_data=canonical, modules=selected)
    chapters = {
        item["id"]: item
        for item in canonical.get("chapters", [])
        if isinstance(item, Mapping) and isinstance(item.get("id"), str)
    }
    items = [
        {
            "work_id": item["work_id"],
            "module": item["module"],
            "scope": copy.deepcopy(item["scope"]),
            "chapter_range": _chapter_range(item, chapters),
            "response": None,
        }
        for item in ledger["items"]
    ]
    chapter_spans: list[dict[str, Any]] | None = None
    if chapter_map is not None:
        _, source_text = _source_text_snapshot(
            source_path, source_format=str(canonical_source.get("format", ""))
        )
        chapter_spans = _validated_chapter_spans(
            load_chapter_spans(chapter_map),
            normalized_source=normalize_text(source_text),
            chapter_ids=set(chapters),
        )
        covered = {span["chapter_id"] for span in chapter_spans}
        required = {
            item["scope"]["chapter_id"]
            for item in items
            if item["scope"].get("kind") == "chapter"
        }
        missing = sorted(required - covered)
        if missing:
            raise ReadingPackError(
                "generation chapter map is missing work scope(s): " + ", ".join(missing)
            )
    session: dict[str, Any] = {
        "schema_version": SESSION_SCHEMA_VERSION,
        "session_id": "",
        "state": "open",
        "project": {
            "slug": config["slug"],
            "config_sha256": artifact_hash(config),
        },
        "language": language,
        "source": {
            "name": canonical_source["name"],
            "format": canonical_source.get("format", ""),
            "sha256": canonical_source["sha256"],
        },
        "canonical_data_sha256": artifact_hash(canonical),
        "ledger": {
            "plan_id": ledger["plan_id"],
            "integrity_sha256": ledger["integrity_sha256"],
        },
        "items": items,
        "finalization": None,
        "summary": _session_summary(items),
        "integrity_sha256": "",
    }
    if purpose == "coverage":
        session["purpose"] = purpose
        session["coverage_rubric"] = copy.deepcopy(COVERAGE_RUBRIC)
    if chapter_spans is not None:
        session["chapter_spans"] = chapter_spans
    session["session_id"] = _session_id(session)
    session["integrity_sha256"] = _integrity(session)
    validate_generation_session(session, ledger)
    root = session_directory.resolve()
    try:
        root.mkdir(parents=True, exist_ok=False)
        root.chmod(0o700)
        (root / RESPONSES_DIRECTORY).mkdir(mode=0o700)
        write_work_ledger(root / LEDGER_NAME, ledger)
        _write_session(root / SESSION_NAME, session)
    except FileExistsError as exc:
        raise ReadingPackError(f"refusing to overwrite generation session: {root}", EXIT_IO) from exc
    return session


def load_generation_session(session_directory: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    root = session_directory.resolve()
    raw = _read_regular_bounded(root / SESSION_NAME, 16 * 1024 * 1024, "generation session")
    session = _strict_json_loads(raw, "generation session")
    from .work_ledger import load_work_ledger

    ledger = load_work_ledger(root / LEDGER_NAME)
    if not isinstance(session, Mapping):
        raise ReadingPackError("invalid generation session: root must be an object")
    validate_generation_session(session, ledger)
    return dict(session), ledger


def _verify_current_bindings(
    session: Mapping[str, Any], *, project: Path, source_path: Path
) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    project = find_project(project)
    config = load_config(project)
    canonical = load_language_data(project, session["language"])
    if session["project"] != {
        "slug": config["slug"],
        "config_sha256": artifact_hash(config),
    }:
        raise ReadingPackError("generation session belongs to another project or configuration")
    if artifact_hash(canonical) != session["canonical_data_sha256"]:
        raise ReadingPackError("generation session canonical snapshot is stale")
    source_path = source_path.resolve()
    try:
        source_digest = file_hash(read_regular_source_bytes(source_path))
    except ReadingPackError:
        raise
    except OSError as exc:
        raise ReadingPackError(f"cannot read generation source: {exc}", EXIT_IO) from exc
    if source_path.name != session["source"]["name"] or source_digest != session["source"]["sha256"]:
        raise ReadingPackError("generation source binding is stale or belongs to another source")
    return project, config, canonical


def next_generation_request(
    session_directory: Path, *, project: Path, source_path: Path
) -> dict[str, Any]:
    session, _ = load_generation_session(session_directory)
    if session["state"] != "open":
        raise ReadingPackError("generation session is already finalized")
    project, _, canonical = _verify_current_bindings(
        session, project=project, source_path=source_path
    )
    item = next((item for item in session["items"] if item["response"] is None), None)
    if item is None:
        return {
            "schema_version": 1,
            "session_id": session["session_id"],
            "state": "ready_to_finalize",
        }
    purpose = session.get("purpose", "initial")
    request = {
        "schema_version": 1,
        "session_id": session["session_id"],
        "state": "work_available",
        "binding": {
            "work_id": item["work_id"],
            "project": session["project"],
            "language": session["language"],
            "source_sha256": session["source"]["sha256"],
            "canonical_data_sha256": session["canonical_data_sha256"],
            "module": item["module"],
            "scope": item["scope"],
            "chapter_range": item["chapter_range"],
        },
        "source_locator": {
            "local_path": str(source_path.resolve()),
            "name": session["source"]["name"],
            "format": session["source"]["format"],
            "chapter_range": item["chapter_range"],
        },
        "prompt": _prompt_text(purpose=purpose),
        "response_schema": schema_document("generation-response.schema.json"),
    }
    if item["scope"].get("kind") == "chapter" and "chapter_spans" in session:
        chapter_id = item["scope"]["chapter_id"]
        request["source_locator"]["chapter_span"] = copy.deepcopy(
            next(
                span
                for span in session["chapter_spans"]
                if span["chapter_id"] == chapter_id
            )
        )
    if purpose == "coverage":
        request["purpose"] = purpose
        request["canonical_locator"] = {
            "local_path": str((project / "data" / f"pack.{session['language']}.json").resolve()),
            "sha256": session["canonical_data_sha256"],
            "treat_as": "untrusted_baseline_data",
        }
        request["coverage"] = {
            "rubric": copy.deepcopy(session["coverage_rubric"]),
            "baseline": _coverage_baseline(canonical, item),
        }
    return request


def _coverage_baseline(
    canonical: Mapping[str, Any], item: Mapping[str, Any]
) -> dict[str, Any]:
    """Return an excerpt-free inventory for one coverage-audit work item."""

    scope = item["scope"]
    chapter_id = scope.get("chapter_id") if scope.get("kind") == "chapter" else None
    chapters = [
        chapter
        for chapter in canonical.get("chapters", [])
        if isinstance(chapter, Mapping)
        and (chapter_id is None or chapter.get("id") == chapter_id)
    ]
    baseline: dict[str, Any] = {
        "chapters": [
            {
                "id": chapter.get("id", ""),
                "title": chapter.get("title", ""),
                "section_count": len(chapter.get("sections", []))
                if isinstance(chapter.get("sections"), list)
                else 0,
                "summary_present": bool(str(chapter.get("summary", "")).strip()),
                "summary_characters": len(str(chapter.get("summary", "")).strip()),
                "term_count": len(chapter.get("terms", []))
                if isinstance(chapter.get("terms"), list)
                else 0,
            }
            for chapter in chapters
        ],
        "existing_records": [],
    }
    module = item["module"]
    if module == "chapter_terms":
        baseline["existing_records"] = [
            {"chapter_id": chapter.get("id", ""), "terms": list(chapter.get("terms", []))}
            for chapter in chapters
            if isinstance(chapter.get("terms"), list)
        ]
    elif module == "claims":
        baseline["existing_records"] = [
            {
                "id": record.get("id", ""),
                "layer": record.get("layer", ""),
                "kind": record.get("kind", ""),
                "chapter_ids": list(record.get("chapter_ids", [])),
            }
            for record in canonical.get("claims", [])
            if isinstance(record, Mapping)
            and (
                chapter_id is None
                or chapter_id in record.get("chapter_ids", [])
            )
        ]
    elif module in {"names", "glossary"}:
        value_field = "name" if module == "names" else "term"
        baseline["existing_records"] = [
            {
                "id": record.get("id", ""),
                value_field: record.get(value_field, ""),
                "chapter_id": record.get("chapter_id", ""),
            }
            for record in canonical.get(module, [])
            if isinstance(record, Mapping)
            and (chapter_id is None or record.get("chapter_id") == chapter_id)
        ]
    return baseline


def _response_file(root: Path, work_id: str) -> Path:
    if not re.fullmatch(r"WORK-[A-F0-9]{20}", work_id):
        raise ReadingPackError("generation response work_id is invalid")
    return root / RESPONSES_DIRECTORY / f"{work_id}.json"


def _validate_scope_records(
    module: str, scope: Mapping[str, Any], records: Sequence[Mapping[str, Any]]
) -> None:
    chapter_id = scope.get("chapter_id") if scope.get("kind") == "chapter" else None
    seen: set[str] = set()
    for index, wrapper in enumerate(records):
        record = wrapper.get("record")
        if not isinstance(record, Mapping):
            raise ReadingPackError(f"generation response records[{index}].record must be an object")
        identifier: Any
        if module == "summaries":
            if set(record) != {"chapter_id", "summary"} or record.get("chapter_id") != chapter_id:
                raise ReadingPackError("summary response is outside its chapter work scope")
            if not isinstance(record.get("summary"), str) or not record["summary"].strip():
                raise ReadingPackError("summary response must contain a non-empty summary")
            identifier = record["chapter_id"]
        elif module == "chapter_terms":
            if set(record) != {"chapter_id", "terms"} or record.get("chapter_id") != chapter_id:
                raise ReadingPackError("chapter terms response is outside its chapter work scope")
            terms = record.get("terms")
            if not isinstance(terms, list) or not terms or not all(
                isinstance(term, str) and term.strip() for term in terms
            ):
                raise ReadingPackError("chapter terms response must contain non-empty string terms")
            identifier = record["chapter_id"]
        elif module == "chapters":
            if record.get("id") != chapter_id:
                raise ReadingPackError("chapter response is outside its chapter work scope")
            identifier = record.get("id")
        elif module in {"claims", "qa", "misreadings"}:
            expected = [chapter_id] if chapter_id is not None else []
            if record.get("chapter_ids") != expected:
                raise ReadingPackError(f"{module} response is outside its declared chapter range")
            identifier = record.get("id")
        elif module in {"names", "glossary"}:
            if record.get("chapter_id") != chapter_id:
                raise ReadingPackError(f"{module} response is outside its chapter work scope")
            if module == "glossary" and (
                not isinstance(record.get("book_meaning"), str)
                or not record["book_meaning"].strip()
                or len(record["book_meaning"])
                > MAX_GENERATED_GLOSSARY_MEANING_CHARACTERS
            ):
                raise ReadingPackError(
                    "glossary response book_meaning must be a non-empty summary "
                    f"of at most {MAX_GENERATED_GLOSSARY_MEANING_CHARACTERS} characters"
                )
            identifier = record.get("id")
        else:
            if scope.get("kind") != "book":
                raise ReadingPackError(f"{module} response must be book-scoped")
            identifier = record.get("id")
        if not isinstance(identifier, str) or not identifier:
            raise ReadingPackError(f"generation response records[{index}] has no record identity")
        if identifier in seen:
            raise ReadingPackError(f"generation response contains duplicate record: {identifier}")
        seen.add(identifier)


def validate_generation_response(
    value: Any, *, session: Mapping[str, Any], item: Mapping[str, Any]
) -> dict[str, Any]:
    require_structure("generation-response.schema.json", value, label="generation response")
    if not isinstance(value, Mapping):
        raise ReadingPackError("generation response root must be an object")
    expected = {
        "schema_version": RESPONSE_SCHEMA_VERSION,
        "session_id": session["session_id"],
        "work_id": item["work_id"],
        "project": session["project"],
        "language": session["language"],
        "source_sha256": session["source"]["sha256"],
        "canonical_data_sha256": session["canonical_data_sha256"],
        "module": item["module"],
        "scope": item["scope"],
        "chapter_range": item["chapter_range"],
    }
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            raise ReadingPackError(f"generation response {key} binding is stale or out of scope")
    outcome = value.get("outcome")
    records = value.get("records")
    if not isinstance(outcome, Mapping) or not isinstance(records, list):
        raise ReadingPackError("generation response outcome or records are invalid")
    status = outcome.get("status")
    reason = outcome.get("reason_code")
    if status == "complete":
        if reason or not records:
            raise ReadingPackError("complete generation response requires records and no reason")
    else:
        if not isinstance(reason, str) or _REASON_CODE.fullmatch(reason) is None or records:
            raise ReadingPackError("terminal zero-result response requires one reason and no records")
    if item["module"] == "qa" and status == "complete":
        raise ReadingPackError(
            "QA generation requires the independent author-Q&A workflow, not the primary-book session"
        )
    _validate_scope_records(item["module"], item["scope"], records)
    clean = copy.deepcopy(dict(value))
    clean["generator"] = _safe_generator(clean.get("generator"))
    return clean


def _validate_chapter_scoped_evidence(
    response: Mapping[str, Any],
    *,
    item: Mapping[str, Any],
    session: Mapping[str, Any],
    source_path: Path,
) -> None:
    """Reject evidence whose normalized source occurrence is outside its scope."""

    if (
        response.get("outcome", {}).get("status") != "complete"
        or item.get("scope", {}).get("kind") != "chapter"
        or "chapter_spans" not in session
    ):
        return
    chapter_id = item["scope"]["chapter_id"]
    span = next(
        value
        for value in session["chapter_spans"]
        if value["chapter_id"] == chapter_id
    )
    _, source_text = _source_text_snapshot(
        source_path, source_format=session["source"]["format"]
    )
    normalized_source = normalize_text(source_text)
    for record_index, wrapper in enumerate(response["records"]):
        for evidence_index, evidence in enumerate(wrapper["evidence"]):
            snippet = normalize_text(evidence["snippet"])
            occurrence = evidence.get("occurrence", 0)
            cursor = 0
            start = -1
            for _ in range(occurrence + 1):
                start = normalized_source.find(snippet, cursor)
                if start < 0:
                    break
                cursor = start + len(snippet)
            if start < 0:
                raise ReadingPackError(
                    "generation response evidence is not in the bound source: "
                    f"records[{record_index}].evidence[{evidence_index}]"
                )
            end = start + len(snippet)
            if not span["char_start"] <= start < end <= span["char_end"]:
                raise ReadingPackError(
                    "generation response evidence is outside its chapter span: "
                    f"records[{record_index}].evidence[{evidence_index}]"
                )


def _validate_abstractive_generation(
    response: Mapping[str, Any],
    *,
    item: Mapping[str, Any],
    session: Mapping[str, Any],
    project: Path,
    source_path: Path,
) -> None:
    """Reject copied prose and keep coverage risks open until replaced.

    Candidate finalization rechecks the same source-copy policy. Performing the
    check at ingest gives a resumable agent an immediate, work-item-local error
    instead of turning a completed response into a quarantined candidate later.
    Coverage sessions also apply the check to existing glossary meanings, which
    brings authority-provided AIP data back through the same correction path.
    """

    status = response.get("outcome", {}).get("status")
    module = item["module"]
    needs_existing_audit = (
        session.get("purpose") == "coverage" and module == "glossary"
    )
    if status != "complete" and not needs_existing_audit:
        return

    _, source_text = _source_text_snapshot(
        source_path, source_format=session["source"]["format"]
    )
    normalized_source = normalize_text(source_text)
    policy = LeakPolicy()
    source_index = _SourceCopyIndex(normalized_source, policy)
    collection = CANDIDATE_COLLECTIONS[module]

    if status == "complete":
        for index, wrapper in enumerate(response["records"]):
            record = wrapper["record"]
            if _copy_risk(collection, record, source_index, policy):
                raise ReadingPackError(
                    "generation response copies source prose; summarize the "
                    f"record within its field limit and retry: records[{index}]"
                )

    if not needs_existing_audit:
        return
    canonical = load_language_data(project, session["language"])
    chapter_id = (
        item["scope"].get("chapter_id")
        if item["scope"].get("kind") == "chapter"
        else None
    )
    risky_ids = {
        record["id"]
        for record in canonical.get("glossary", [])
        if isinstance(record, Mapping)
        and (chapter_id is None or record.get("chapter_id") == chapter_id)
        and _copy_risk("glossary", record, source_index, policy)
    }
    replacement_ids = (
        {
            wrapper["record"].get("id")
            for wrapper in response["records"]
            if isinstance(wrapper.get("record"), Mapping)
        }
        if status == "complete"
        else set()
    )
    unresolved = sorted(risky_ids - replacement_ids)
    if unresolved:
        raise ReadingPackError(
            "coverage glossary scope contains source-copy risk; provide "
            "abstractive replacements for " + ", ".join(unresolved)
        )


def ingest_generation_response(
    session_directory: Path,
    response: Any,
    *,
    project: Path,
    source_path: Path,
) -> dict[str, Any]:
    root = session_directory.resolve()
    session, ledger = load_generation_session(root)
    if session["state"] != "open":
        raise ReadingPackError("generation session is already finalized")
    _verify_current_bindings(session, project=project, source_path=source_path)
    if not isinstance(response, Mapping):
        raise ReadingPackError("generation response root must be an object")
    work_id = response.get("work_id")
    item = next((entry for entry in session["items"] if entry["work_id"] == work_id), None)
    if item is None:
        raise ReadingPackError("generation response references an unknown or foreign work_id")
    if item["response"] is not None or _response_file(root, work_id).exists():
        raise ReadingPackError(f"duplicate generation response for work item {work_id}")
    clean = validate_generation_response(response, session=session, item=item)
    _validate_chapter_scoped_evidence(
        clean,
        item=item,
        session=session,
        source_path=source_path,
    )
    _validate_abstractive_generation(
        clean,
        item=item,
        session=session,
        project=project,
        source_path=source_path,
    )
    encoded = _json_bytes(clean)
    if len(encoded) > MAX_RESPONSE_BYTES:
        raise ReadingPackError(f"generation response exceeds {MAX_RESPONSE_BYTES} bytes", EXIT_IO)
    path = _response_file(root, work_id)
    atomic_write_text(path, encoded.decode("utf-8"))
    try:
        path.chmod(0o600)
    except OSError as exc:
        raise ReadingPackError(f"cannot restrict generation response permissions: {exc}") from exc
    item["response"] = {
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "size_bytes": len(encoded),
        "generator": clean["generator"],
    }
    session["summary"] = _session_summary(session["items"])
    session["integrity_sha256"] = _integrity(session)
    validate_generation_session(session, ledger)
    _write_session(root / SESSION_NAME, session)
    return {
        "session_id": session["session_id"],
        "work_id": work_id,
        "outcome": clean["outcome"],
        "summary": session["summary"],
    }


def ingest_generation_response_file(
    session_directory: Path,
    response_path: Path,
    *,
    project: Path,
    source_path: Path,
) -> dict[str, Any]:
    raw = _read_regular_bounded(response_path, MAX_RESPONSE_BYTES, "generation response")
    return ingest_generation_response(
        session_directory,
        _strict_json_loads(raw, "generation response"),
        project=project,
        source_path=source_path,
    )


def close_generation_work(
    session_directory: Path,
    *,
    project: Path,
    source_path: Path,
    outcome: str,
    reason_code: str,
) -> dict[str, Any]:
    """Record a validated zero-result outcome for the next bounded work item."""

    if outcome not in {"no_supported_candidate", "skipped"}:
        raise ReadingPackError(
            "work close outcome must be no_supported_candidate or skipped", 2
        )
    request = next_generation_request(
        session_directory, project=project, source_path=source_path
    )
    if request.get("state") != "work_available":
        raise ReadingPackError("generation session has no pending work to close")
    binding = request["binding"]
    response = {
        "schema_version": RESPONSE_SCHEMA_VERSION,
        "session_id": request["session_id"],
        "work_id": binding["work_id"],
        "project": binding["project"],
        "language": binding["language"],
        "source_sha256": binding["source_sha256"],
        "canonical_data_sha256": binding["canonical_data_sha256"],
        "module": binding["module"],
        "scope": binding["scope"],
        "chapter_range": binding["chapter_range"],
        "outcome": {"status": outcome, "reason_code": reason_code},
        "records": [],
        "generator": {
            "adapter": "reading-pack-work-close",
            "model": "",
            "revision": "builtin-terminal-outcome-v1",
        },
    }
    return ingest_generation_response(
        session_directory,
        response,
        project=project,
        source_path=source_path,
    )


def retry_generation_work(
    session_directory: Path,
    work_id: str,
    *,
    project: Path,
    source_path: Path,
) -> dict[str, Any]:
    """Explicitly return one ingested work item to the awaiting-response state."""

    root = session_directory.resolve()
    session, ledger = load_generation_session(root)
    if session["state"] != "open":
        raise ReadingPackError("generation session is already finalized")
    _verify_current_bindings(session, project=project, source_path=source_path)
    item = next((entry for entry in session["items"] if entry["work_id"] == work_id), None)
    if item is None:
        raise ReadingPackError("generation retry references an unknown or foreign work_id")
    path = _response_file(root, work_id)
    binding = item["response"]
    if binding is None and not path.exists():
        raise ReadingPackError(f"work item {work_id} has no ingested response to retry")
    raw = _read_regular_bounded(path, MAX_RESPONSE_BYTES, "stored generation response")
    if binding is not None and (
        len(raw) != binding["size_bytes"]
        or hashlib.sha256(raw).hexdigest() != binding["sha256"]
    ):
        raise ReadingPackError(f"stored generation response is stale: {work_id}")

    previous = copy.deepcopy(session)
    item["response"] = None
    session["summary"] = _session_summary(session["items"])
    session["integrity_sha256"] = _integrity(session)
    validate_generation_session(session, ledger)
    _write_session(root / SESSION_NAME, session)
    try:
        path.unlink()
    except OSError as exc:
        if binding is not None:
            _write_session(root / SESSION_NAME, previous)
        raise ReadingPackError(
            f"cannot remove retried generation response {path}: {exc}", EXIT_IO
        ) from exc
    return {
        "session_id": session["session_id"],
        "work_id": work_id,
        "state": "awaiting_response",
        "summary": session["summary"],
        "approval_granted": False,
    }


def run_generation_adapter(
    session_directory: Path,
    *,
    project: Path,
    source_path: Path,
    command: Sequence[str],
    timeout: float,
    max_output: int = MAX_RESPONSE_BYTES,
) -> dict[str, Any]:
    request = next_generation_request(
        session_directory, project=project, source_path=source_path
    )
    if request.get("state") != "work_available":
        raise ReadingPackError("generation session has no pending adapter work")
    response = run_local_adapter(command, request, timeout=timeout, max_output=max_output)
    return ingest_generation_response(
        session_directory,
        response,
        project=project,
        source_path=source_path,
    )


def _load_stored_responses(
    root: Path, session: Mapping[str, Any]
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    result: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for item in session["items"]:
        binding = item["response"]
        if binding is None:
            raise ReadingPackError(f"work item {item['work_id']} has no ingested response")
        path = _response_file(root, item["work_id"])
        raw = _read_regular_bounded(path, MAX_RESPONSE_BYTES, "stored generation response")
        if len(raw) != binding["size_bytes"] or hashlib.sha256(raw).hexdigest() != binding["sha256"]:
            raise ReadingPackError(f"stored generation response is stale: {item['work_id']}")
        value = _strict_json_loads(raw, "stored generation response")
        clean = validate_generation_response(value, session=session, item=item)
        result.append((item, clean))
    return result


def _candidate_payloads(
    responses: Sequence[tuple[Mapping[str, Any], Mapping[str, Any]]],
    canonical: Mapping[str, Any],
) -> list[dict[str, Any]]:
    chapters = {
        item["id"]: {
            key: copy.deepcopy(value)
            for key, value in item.items()
            if key not in {"source_id", "source_hash", "translation_status"}
        }
        for item in canonical.get("chapters", [])
        if isinstance(item, Mapping) and isinstance(item.get("id"), str)
    }
    chapter_evidence: dict[str, list[Any]] = {}
    touched_chapters: set[str] = set()
    direct: list[dict[str, Any]] = []
    seen_direct: set[tuple[str, str]] = set()
    for item, response in responses:
        if response["outcome"]["status"] != "complete":
            continue
        module = item["module"]
        for wrapper in response["records"]:
            record = copy.deepcopy(wrapper["record"])
            evidence = copy.deepcopy(wrapper["evidence"])
            if module in {"chapters", "summaries", "chapter_terms"}:
                chapter_id = record.get("id", record.get("chapter_id"))
                if chapter_id not in chapters:
                    raise ReadingPackError(f"chapter work references unknown chapter {chapter_id}")
                target = chapters[chapter_id]
                if module == "chapters":
                    target.update(record)
                elif module == "summaries":
                    target["summary"] = record["summary"]
                else:
                    target["terms"] = record["terms"]
                touched_chapters.add(chapter_id)
                chapter_evidence.setdefault(chapter_id, []).extend(evidence)
                continue
            collection = CANDIDATE_COLLECTIONS[module]
            identifier = record.get("id")
            key = (collection, identifier)
            if key in seen_direct:
                raise ReadingPackError(f"duplicate generated candidate record: {collection}/{identifier}")
            seen_direct.add(key)
            direct.append({"collection": collection, "record": record, "evidence": evidence})
    chapter_payloads = [
        {
            "collection": "chapters",
            "record": chapters[chapter_id],
            "evidence": chapter_evidence[chapter_id],
        }
        for chapter_id in chapters
        if chapter_id in touched_chapters
    ]
    payloads = chapter_payloads + direct
    if len(payloads) > 2_000:
        raise ReadingPackError("generation session exceeds the candidate-run record limit")
    return payloads


def finalize_generation_session(
    session_directory: Path,
    *,
    project: Path,
    source_path: Path,
    run_directory: Path,
) -> dict[str, Any]:
    root = session_directory.resolve()
    session, ledger = load_generation_session(root)
    if session["state"] != "open":
        raise ReadingPackError("generation session is already finalized")
    project, config, canonical = _verify_current_bindings(
        session, project=project, source_path=source_path
    )
    responses = _load_stored_responses(root, session)
    payloads = _candidate_payloads(responses, canonical)
    purpose = session.get("purpose", "initial")
    prompt_hash = hashlib.sha256(
        _prompt_text(purpose=purpose).encode("utf-8")
    ).hexdigest()
    run_id = f"generation-{session['session_id'].lower()}"
    languages = {
        language: load_language_data(project, language)
        for language in config["languages"]
    }
    final_run_directory = run_directory.resolve()
    if final_run_directory.exists():
        raise ReadingPackError(
            f"candidate run directory already exists: {final_run_directory}", EXIT_IO
        )
    try:
        final_run_directory.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ReadingPackError(
            f"cannot create candidate run parent {final_run_directory.parent}: {exc}",
            EXIT_IO,
        ) from exc

    # A quarantined or otherwise inconsistent candidate set must not occupy the
    # requested run path.  Build and reconcile it privately, then publish the
    # validated run directory with one atomic rename.
    try:
        staging_context = tempfile.TemporaryDirectory(
            prefix=f".{final_run_directory.name}.preparing-",
            dir=final_run_directory.parent,
        )
    except OSError as exc:
        raise ReadingPackError(
            f"cannot stage candidate run under {final_run_directory.parent}: {exc}",
            EXIT_IO,
        ) from exc
    with staging_context as temporary:
        staged_run_directory = Path(temporary) / final_run_directory.name
        staged_run_path = create_candidate_run(
            staged_run_directory,
            source_path=source_path,
            responses=payloads,
            language=session["language"],
            canonical_data=canonical,
            project_data_by_lang=languages,
            known_chapter_ids={item["id"] for item in canonical["chapters"]},
            run_id=run_id,
            generator={
                "adapter": "resumable-work-session",
                "model": "mixed-or-unspecified",
                "revision": str(SESSION_SCHEMA_VERSION),
                "prompt_hash": prompt_hash,
            },
        )
        run = load_candidate_run(staged_run_path)
        results: list[dict[str, Any]] = []
        for item, response in responses:
            outcome = response["outcome"]
            if outcome["status"] == "complete":
                candidate_ids = [
                    candidate["candidate_id"]
                    for candidate in run["candidates"]
                    if candidate.get("candidate_state") == "ready_for_review"
                    and _candidate_matches(candidate, item)
                ]
                if not candidate_ids:
                    quarantined = [
                        candidate["candidate_id"]
                        for candidate in run["candidates"]
                        if _candidate_matches(candidate, item, require_record=False)
                    ]
                    detail = f" ({', '.join(quarantined)})" if quarantined else ""
                    raise ReadingPackError(
                        "completed work item produced no reviewable candidate: "
                        f"{item['work_id']}{detail}"
                    )
                reason_code = ""
            else:
                candidate_ids = []
                reason_code = outcome["reason_code"]
            results.append(
                {
                    "work_id": item["work_id"],
                    "status": outcome["status"],
                    "reason_code": reason_code,
                    "candidate_ids": candidate_ids,
                }
            )
        result_document = {
            "schema_version": 1,
            "plan_id": ledger["plan_id"],
            "run_id": run["run_id"],
            "results": results,
        }
        reconciled = reconcile_work_results(ledger, result_document, run)
        try:
            os.replace(staged_run_directory, final_run_directory)
        except OSError as exc:
            raise ReadingPackError(
                f"cannot publish candidate run {final_run_directory}: {exc}", EXIT_IO
            ) from exc
    run_path = final_run_directory / "manifest.json"
    write_work_ledger(root / LEDGER_NAME, reconciled, overwrite=True)
    session["state"] = "finalized"
    session["finalization"] = {
        "run_id": run["run_id"],
        "run_integrity_sha256": run["integrity_sha256"],
        "ledger_integrity_sha256": reconciled["integrity_sha256"],
    }
    session["integrity_sha256"] = _integrity(session)
    validate_generation_session(session, reconciled)
    _write_session(root / SESSION_NAME, session)
    for item in session["items"]:
        try:
            _response_file(root, item["work_id"]).unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise ReadingPackError(
                f"candidate run finalized but transient response cleanup failed: {exc}", EXIT_IO
            ) from exc
    return {
        "session_id": session["session_id"],
        "state": session["state"],
        "run": str(run_path),
        "run_id": run["run_id"],
        "run_integrity_sha256": run["integrity_sha256"],
        "ledger": str(root / LEDGER_NAME),
        "coverage": reconciled["summary"],
        "candidate_summary": run["summary"],
        "approval_granted": False,
    }


def _candidate_matches(
    candidate: Mapping[str, Any], item: Mapping[str, Any], *, require_record: bool = True
) -> bool:
    if candidate.get("collection") != CANDIDATE_COLLECTIONS[item["module"]]:
        return False
    record = candidate.get("record")
    if not isinstance(record, Mapping):
        return not require_record and candidate.get("record_id") != ""
    scope = item["scope"]
    if item["module"] in {"chapters", "summaries", "chapter_terms"}:
        return candidate.get("record_id") == scope.get("chapter_id")
    if scope.get("kind") == "book":
        if item["module"] in {"claims", "qa", "misreadings"}:
            return record.get("chapter_ids") == []
        return True
    chapter_id = scope["chapter_id"]
    if item["module"] in {"claims", "qa", "misreadings"}:
        return record.get("chapter_ids") == [chapter_id]
    return record.get("chapter_id") == chapter_id


def generation_session_status(session_directory: Path) -> dict[str, Any]:
    session, ledger = load_generation_session(session_directory)
    next_item = next((item for item in session["items"] if item["response"] is None), None)
    return {
        "session_id": session["session_id"],
        "state": session["state"],
        "project": session["project"],
        "language": session["language"],
        "source": session["source"],
        "canonical_data_sha256": session["canonical_data_sha256"],
        "summary": session["summary"],
        "next_work_id": next_item["work_id"] if next_item is not None else "",
        "ledger_summary": ledger["summary"],
        "finalization": session["finalization"],
        "approval_granted": False,
    }
