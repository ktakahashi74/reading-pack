"""Schema, reference, parity, freshness, and release-readiness validation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

from .companion import companion_findings
from .hashing import semantic_hash
from .profiles import validate_quality_plan
from .project import load_config, load_language_data
from .schema_validation import rp_structural_code, structural_findings
from .source_registry import load_source_registry


@dataclass(frozen=True)
class Issue:
    severity: str
    code: str
    path: str
    message: str

    def format(self) -> str:
        return f"{self.severity.upper()} {self.code} {self.path}: {self.message}"


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

OPTIONAL_LIST_FIELDS = {
    "chapters": {
        "contributors",
        "aliases",
        "learning_objectives",
        "prerequisites",
        "source_locations",
    },
    "certainty": {"source_locations"},
    "misreadings": {"claim_ids", "source_locations"},
    "claims": {"source_locations"},
    "policies": {"source_locations"},
    "names": {"aliases", "source_locations"},
    "glossary": {"aliases", "source_locations"},
    "references": {"source_locations"},
}

RENDERED_STRING_FIELDS = {
    "chapters": ("title", "pages", "summary"),
    "certainty": ("label", "definition"),
    "claims": (
        "kind", "statement", "falsifiability", "revision_conditions",
        "reader_note",
    ),
    "misreadings": (
        "issue", "misreading", "response", "impact", "remaining_uncertainty", "anchor",
    ),
    "policies": ("kind", "statement"),
    "names": ("name", "book_context"),
    "glossary": ("term", "book_meaning"),
    "references": ("label", "url"),
}
UNSAFE_RENDERED_TEXT = re.compile(
    r"[\x00-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]"
)
PROVENANCE_SOURCE_ID = re.compile(r"SRC-[A-Z0-9][A-Z0-9.-]{0,99}")
SHA256 = re.compile(r"[0-9a-f]{64}")
FORMULAIC_INDEX_CONTEXT = re.compile(
    r"同章の(?:[^。]{0,40}参照人物|議論を組み立てる概念・枠組み)"
)


def _issue(issues: list[Issue], code: str, path: str, message: str, severity: str = "error") -> None:
    issues.append(Issue(severity, code, path, message))


def _validate_config(config: dict[str, Any], issues: list[Issue]) -> None:
    for finding in structural_findings("project.schema.json", config):
        _issue(
            issues,
            rp_structural_code(finding),
            finding.dotted_path("reading-pack.toml"),
            finding.message,
        )
    languages = config.get("languages", [])
    if not isinstance(languages, list):
        languages = []
    if config.get("primary_language") not in languages:
        _issue(issues, "RP005", "reading-pack.toml.primary_language", "must occur in languages")
    book = config.get("book", {})
    if not isinstance(book, dict):
        book = {}
    for field in (
        "title", "author", "publisher", "isbn", "official_url",
        "contents_note", "copyright_holder", "pack_license",
    ):
        value = book.get(field)
        if isinstance(value, str) and UNSAFE_RENDERED_TEXT.search(value):
            _issue(
                issues,
                "RP012",
                f"reading-pack.toml.book.{field}",
                "must be one safe output line without control or bidi-control characters",
            )


def _validate_record_semantics(
    collection: str,
    index: int,
    record: Any,
    issues: list[Issue],
) -> None:
    path = f"data.{collection}[{index}]"
    if not isinstance(record, dict):
        return
    provenance_id = record.get("provenance_source_id")
    provenance_hash = record.get("provenance_source_hash")
    if (provenance_id is None) != (provenance_hash is None):
        _issue(
            issues,
            "RP127",
            path,
            "provenance_source_id and provenance_source_hash must occur together",
        )
    elif provenance_id is not None and (
        not isinstance(provenance_id, str)
        or PROVENANCE_SOURCE_ID.fullmatch(provenance_id) is None
        or not isinstance(provenance_hash, str)
        or SHA256.fullmatch(provenance_hash) is None
    ):
        _issue(
            issues,
            "RP127",
            path,
            "support-source provenance is invalid",
        )
    for field in OPTIONAL_LIST_FIELDS.get(collection, set()):
        if field in record and isinstance(record.get(field), list) and any(
            not item or UNSAFE_RENDERED_TEXT.search(item)
            for item in record.get(field, [])
        ):
            _issue(
                issues,
                "RP123",
                f"{path}.{field}",
                "must contain safe non-empty output lines",
            )
    for field in RENDERED_STRING_FIELDS[collection]:
        value = record.get(field)
        if value is not None and not isinstance(value, str):
            _issue(
                issues,
                "RP123",
                f"{path}.{field}",
                "must be a string",
            )
        elif isinstance(value, str) and UNSAFE_RENDERED_TEXT.search(value):
            _issue(
                issues,
                "RP123",
                f"{path}.{field}",
                "must be one safe output line without control or bidi-control characters",
            )
        elif field in {"book_context", "book_meaning"} and isinstance(value, str) and len(value) > 500:
            _issue(
                issues,
                "RP123",
                f"{path}.{field}",
                "must not exceed 500 characters",
            )
    if collection in {"chapters", "names", "glossary"}:
        fields = (
            "sections",
            "terms",
            "contributors",
            "aliases",
            "learning_objectives",
            "prerequisites",
        ) if collection == "chapters" else ("aliases",)
        for field in fields:
            values = record.get(field, [])
            if not isinstance(values, list):
                continue
            for position, value in enumerate(values):
                if isinstance(value, str) and UNSAFE_RENDERED_TEXT.search(value):
                    _issue(
                        issues,
                        "RP123",
                        f"{path}.{field}[{position}]",
                        "must be one safe output line without control or bidi-control characters",
                    )
    if collection == "claims":
        if record.get("layer") == "descriptive" and record.get("revision_conditions"):
            _issue(issues, "RP107", path, "descriptive claims must not use revision_conditions")
        if record.get("layer") == "normative" and record.get("falsifiability"):
            _issue(issues, "RP108", path, "normative claims must not use falsifiability")
    if collection == "references":
        url = record.get("url", "")
        parsed = urlparse(url) if isinstance(url, str) else urlparse("")
        if parsed.scheme not in {"https", "http"} or not parsed.netloc:
            _issue(issues, "RP109", f"{path}.url", "must be an absolute HTTP(S) URL")


def _validate_data(lang: str, data: dict[str, Any], config: dict[str, Any], issues: list[Issue]) -> None:
    base = f"data/pack.{lang}.json"
    for finding in structural_findings("language-pack.schema.json", data):
        if finding.path and finding.path[-1] in {
            "provenance_source_id",
            "provenance_source_hash",
        }:
            continue
        _issue(
            issues,
            rp_structural_code(finding),
            finding.dotted_path(base),
            finding.message,
        )
    if data.get("language") != lang:
        _issue(issues, "RP111", f"{base}.language", f"must equal {lang}")
    book = data.get("book", {}) if isinstance(data.get("book"), dict) else {}
    config_book = config.get("book", {}) if isinstance(config.get("book"), dict) else {}
    if book.get("author") != config_book.get("author"):
        _issue(issues, "RP113", f"{base}.book.author", "must match project configuration")
    if lang == config.get("primary_language") and book.get("title") != config_book.get("title"):
        _issue(issues, "RP122", f"{base}.book.title", "primary title must match project configuration")
    for field in (
        "title", "author", "display_author", "publisher", "publication_date", "isbn",
        "official_url", "contents_note",
    ):
        value = book.get(field)
        if isinstance(value, str) and UNSAFE_RENDERED_TEXT.search(value):
            _issue(
                issues,
                "RP124",
                f"{base}.book.{field}",
                "must be one safe output line without control or bidi-control characters",
            )
    all_ids: dict[str, str] = {}
    chapter_ids: set[str] = set()
    certainty_ids: set[str] = set()
    for collection in COLLECTIONS:
        records = data.get(collection, [])
        if not isinstance(records, list):
            continue
        for index, record in enumerate(records):
            _validate_record_semantics(collection, index, record, issues)
            if not isinstance(record, dict):
                continue
            identifier = record.get("id")
            if identifier in all_ids:
                _issue(issues, "RP115", f"{base}.{collection}[{index}].id", f"duplicates ID used in {all_ids[identifier]}")
            elif identifier:
                all_ids[identifier] = collection
        if collection == "chapters":
            chapter_ids = {r.get("id") for r in records if isinstance(r, dict)}
        if collection == "certainty":
            certainty_ids = {r.get("id") for r in records if isinstance(r, dict)}
    max_summary = config.get("limits", {}).get("max_summary_characters", 500)
    for index, chapter in enumerate(data.get("chapters", [])):
        if isinstance(chapter, dict) and len(chapter.get("summary", "")) > max_summary:
            _issue(issues, "RP117", f"{base}.chapters[{index}].summary", f"exceeds {max_summary} characters")
    for collection in ("claims", "misreadings"):
        for index, record in enumerate(data.get(collection, [])):
            for chapter_id in record.get("chapter_ids", []) if isinstance(record, dict) else []:
                if chapter_id not in chapter_ids:
                    _issue(issues, "RP118", f"{base}.{collection}[{index}]", f"broken chapter reference: {chapter_id}")
    for collection in ("names", "glossary"):
        for index, record in enumerate(data.get(collection, [])):
            if isinstance(record, dict) and record.get("chapter_id") not in chapter_ids:
                _issue(issues, "RP119", f"{base}.{collection}[{index}]", f"broken chapter reference: {record.get('chapter_id')}")
    for index, claim in enumerate(data.get("claims", [])):
        certainty = claim.get("certainty_id") if isinstance(claim, dict) else None
        if certainty and certainty not in certainty_ids:
            _issue(issues, "RP120", f"{base}.claims[{index}].certainty_id", f"broken certainty reference: {certainty}")
    claim_ids = {
        claim.get("id")
        for claim in data.get("claims", [])
        if isinstance(claim, dict)
    }
    for index, item in enumerate(data.get("misreadings", [])):
        for claim_id in item.get("claim_ids", []) if isinstance(item, dict) else []:
            if claim_id not in claim_ids:
                _issue(
                    issues,
                    "RP121",
                    f"{base}.misreadings[{index}].claim_ids",
                    f"broken claim reference: {claim_id}",
                )
    references = data.get("references", [])
    if isinstance(references, list):
        for finding in companion_findings(references):
            code = {
                "declaration": "RP129",
                "url": "RP130",
                "collection": "RP131",
            }[finding.kind]
            path = f"{base}.references"
            if finding.index is not None:
                path += f"[{finding.index}].{finding.field}"
            _issue(issues, code, path, finding.message)


def _validate_bilingual(config: dict[str, Any], data_by_lang: dict[str, dict], issues: list[Issue]) -> None:
    languages = config.get("languages", [])
    if len(languages) < 2:
        return
    primary_lang = config["primary_language"]
    primary = data_by_lang[primary_lang]
    for lang in languages:
        if lang == primary_lang:
            continue
        translated = data_by_lang[lang]
        for collection in COLLECTIONS:
            primary_records = primary.get(collection, [])
            translated_records = translated.get(collection, [])
            primary_ids = [record.get("id") for record in primary_records]
            translated_ids = [record.get("id") for record in translated_records]
            if primary_ids != translated_ids:
                _issue(
                    issues,
                    "RP200",
                    f"data/pack.{lang}.json.{collection}",
                    f"ID/order parity mismatch; primary={primary_ids}, translation={translated_ids}",
                )
            primary_by_id = {record.get("id"): record for record in primary_records}
            for index, record in enumerate(translated_records):
                identifier = record.get("id")
                source = primary_by_id.get(identifier)
                if source is None:
                    continue
                if record.get("source_id") != identifier:
                    _issue(issues, "RP201", f"data/pack.{lang}.json.{collection}[{index}].source_id", f"must equal {identifier}")
                expected = semantic_hash(source)
                if record.get("source_hash") != expected:
                    _issue(
                        issues,
                        "RP202",
                        f"data/pack.{lang}.json.{collection}[{index}].source_hash",
                        f"stale translation; expected {expected}",
                    )
                if record.get("translation_status") not in {"draft", "reviewed", "approved"}:
                    _issue(
                        issues,
                        "RP203",
                        f"data/pack.{lang}.json.{collection}[{index}].translation_status",
                        "must be draft, reviewed, or approved",
                    )


def release_issues(config: dict[str, Any], data_by_lang: dict[str, dict]) -> list[Issue]:
    issues: list[Issue] = []
    workflow = config.get("workflow", {})
    required = {
        "design_constraints": {"approved"},
        "rights_review": {"approved"},
        "author_review": {"approved"},
        "publisher_review": {"approved", "not_required"},
        "reconstruction_review": {"approved"},
        "publication_decision": {"approved"},
    }
    for field, accepted in required.items():
        if workflow.get(field) not in accepted:
            _issue(issues, "RP300", f"reading-pack.toml.workflow.{field}", f"release requires one of {sorted(accepted)}")
    if config.get("book", {}).get("pack_license") == "rights-holder decision pending":
        _issue(
            issues,
            "RP302",
            "reading-pack.toml.book.pack_license",
            "release requires explicit terms chosen by the rights holder",
        )
    for lang, data in data_by_lang.items():
        for collection in COLLECTIONS:
            for index, record in enumerate(data.get(collection, [])):
                if record.get("status") != "approved":
                    _issue(issues, "RP301", f"data/pack.{lang}.json.{collection}[{index}].status", "release requires approved")
        for collection, field in (
            ("names", "book_context"),
            ("glossary", "book_meaning"),
        ):
            for index, record in enumerate(data.get(collection, [])):
                if not isinstance(record.get(field), str) or not record[field].strip():
                    _issue(
                        issues,
                        "RP303",
                        f"data/pack.{lang}.json.{collection}[{index}].{field}",
                        "release requires a concise source-grounded account of how the book treats this entry",
                    )
                elif (
                    len(record[field].strip()) < 16
                    or FORMULAIC_INDEX_CONTEXT.search(record[field])
                ):
                    _issue(
                        issues,
                        "RP304",
                        f"data/pack.{lang}.json.{collection}[{index}].{field}",
                        "release rejects placeholder index context; identify a specific section, concept, action, or definition",
                    )
    return issues


def validate_language_data(
    config: dict[str, Any], language: str, data: dict[str, Any]
) -> list[Issue]:
    """Validate one prospective canonical language document before mutation."""

    issues: list[Issue] = []
    _validate_data(language, data, config, issues)
    return issues


def validate_data_set(
    config: dict[str, Any], data_by_lang: dict[str, dict]
) -> list[Issue]:
    """Validate a prospective complete canonical data set without filesystem I/O."""

    issues: list[Issue] = []
    configured = config.get("languages", [])
    if not isinstance(configured, list) or set(data_by_lang) != set(configured):
        _issue(
            issues,
            "RP121",
            "data",
            "prospective data set must contain every configured language exactly once",
        )
        return issues
    for language in configured:
        _validate_data(language, data_by_lang[language], config, issues)
    _validate_bilingual(config, data_by_lang, issues)
    return issues


def validate_project(project: Path, *, release: bool = False) -> tuple[dict[str, Any], dict[str, dict], list[Issue]]:
    issues: list[Issue] = []
    config = load_config(project)
    _validate_config(config, issues)
    data_by_lang: dict[str, dict] = {}
    for lang in config.get("languages", []):
        try:
            data = load_language_data(project, lang)
        except Exception as exc:
            _issue(issues, "RP121", f"data/pack.{lang}.json", str(exc))
            continue
        data_by_lang[lang] = data
        _validate_data(lang, data, config, issues)
    if set(data_by_lang) == set(config.get("languages", [])):
        try:
            registry = load_source_registry(project)
        except Exception as exc:
            _issue(issues, "RP128", "sources.json", str(exc))
            registry = {"sources": []}
        registered = {
            item.get("id"): item
            for item in registry.get("sources", [])
            if isinstance(item, dict)
        }
        primary = registered.get("SRC-1")
        if primary is not None:
            lang = config.get("primary_language")
            source = data_by_lang.get(lang, {}).get("source", {})
            if any(
                source.get(key) != primary.get(key)
                for key in ("name", "sha256", "format")
            ):
                _issue(
                    issues,
                    "RP128",
                    f"data/pack.{lang}.json.source",
                    "does not match registered SRC-1 primary source",
                )
        for lang, data in data_by_lang.items():
            for collection in COLLECTIONS:
                for index, record in enumerate(data.get(collection, [])):
                    if not isinstance(record, dict) or "provenance_source_id" not in record:
                        continue
                    source = registered.get(record.get("provenance_source_id"))
                    if (
                        source is None
                        or source.get("sha256") != record.get("provenance_source_hash")
                        or source.get("role") == "primary-book"
                    ):
                        _issue(
                            issues,
                            "RP128",
                            f"data/pack.{lang}.json.{collection}[{index}]",
                            "support-source provenance is absent or stale in sources.json",
                        )
        _validate_bilingual(config, data_by_lang, issues)
        try:
            from reading_pack_review.author_review import load_author_review_state

            review_state = load_author_review_state(project)
        except Exception as exc:
            _issue(issues, "RP506", "author-review-state.json", str(exc))
            review_state = {"schema_version": 1, "reviews": []}
        try:
            from reading_pack_review.author_input import (
                author_input_consistency_findings,
                load_author_input_state,
            )
            from reading_pack_review.author_review import author_review_consistency_findings

            author_state = load_author_input_state(project, config)
            for code, path, message in author_input_consistency_findings(
                data_by_lang, author_state, registered, review_state
            ):
                _issue(issues, code, path, message)
            for code, path, message in author_review_consistency_findings(
                data_by_lang, review_state, author_state
            ):
                _issue(issues, code, path, message)
        except Exception as exc:
            _issue(issues, "RP503", "author-input-state.json", str(exc))
        issues.extend(
            validate_quality_plan(
                project,
                data_by_lang,
                release=release,
                project_level=config.get("level"),
            )
        )
        if release:
            issues.extend(release_issues(config, data_by_lang))
    return config, data_by_lang, issues


def errors(issues: Iterable[Issue]) -> list[Issue]:
    return [issue for issue in issues if issue.severity == "error"]
