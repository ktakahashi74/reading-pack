"""Body-free generation coverage ledgers and semantic-review records.

The toolkit does not call a model here.  It creates deterministic work items,
reconciles explicit worker outcomes against an integrity-checked candidate run,
and records semantic findings without storing source excerpts.  A completed
work item means generation produced at least one reviewable candidate; it does
not mean that the candidate is correct, accepted, or approved for publication.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from reading_pack.errors import EXIT_IO, ReadingPackError
from reading_pack.project import atomic_write_text
from reading_pack.schema_validation import require_structure


WORK_LEDGER_SCHEMA_VERSION = 1
SEMANTIC_REVIEW_SCHEMA_VERSION = 1
MAX_LEDGER_BYTES = 16 * 1024 * 1024
MAX_RESULTS_BYTES = 8 * 1024 * 1024
MAX_SEMANTIC_REVIEW_BYTES = 16 * 1024 * 1024
MAX_WORK_ITEMS = 20_000
MAX_FINDINGS = 20_000
MAX_CANDIDATES_PER_ITEM = 2_000
MAX_EVIDENCE_REFS_PER_FINDING = 100

MODULES = (
    "chapters",
    "summaries",
    "chapter_terms",
    "certainty",
    "claims",
    "qa",
    "misreadings",
    "policy",
    "names",
    "glossary",
    "references",
)
CANDIDATE_COLLECTIONS = {
    "chapters": "chapters",
    "summaries": "chapters",
    "chapter_terms": "chapters",
    "certainty": "certainty",
    "claims": "claims",
    "qa": "misreadings",
    "misreadings": "misreadings",
    "policy": "policies",
    "names": "names",
    "glossary": "glossary",
    "references": "references",
}
CHAPTER_SCOPED_MODULES = {
    "chapters",
    "summaries",
    "chapter_terms",
    "claims",
    "qa",
    "misreadings",
    "names",
    "glossary",
}
BOOK_FALLBACK_MODULES = {"claims", "qa", "misreadings"}
BOOK_SCOPED_MODULES = set(MODULES) - CHAPTER_SCOPED_MODULES
WORK_STATUSES = {
    "pending",
    "complete",
    "no_supported_candidate",
    "failed",
    "skipped",
}
TERMINAL_WORK_STATUSES = WORK_STATUSES - {"pending"}
SEMANTIC_CATEGORIES = {
    "unsupported",
    "overstated",
    "missing_qualifier",
    "misattributed",
    "contradictory",
    "non_atomic",
    "duplicate",
    "other",
}
SEMANTIC_SEVERITIES = {"warning", "error"}
ADJUDICATION_DECISIONS = {"pending", "confirmed", "dismissed", "accepted_risk"}
USABLE_CANDIDATE_STATES = {"ready_for_review", "accepted", "applying", "applied"}

_SHA256 = re.compile(r"[a-f0-9]{64}")
_WORK_ID = re.compile(r"WORK-[A-F0-9]{20}")
_PLAN_ID = re.compile(r"WP-[A-F0-9]{20}")
_REVIEW_ID = re.compile(r"SR-[A-F0-9]{20}")
_FINDING_ID = re.compile(r"SEM-[A-F0-9]{20}")
_CANDIDATE_ID = re.compile(r"CAND-[A-F0-9]{20}")
_EVIDENCE_ID = re.compile(r"EV-[A-F0-9]{16}")
_REASON_CODE = re.compile(r"[a-z][a-z0-9_]{0,99}")
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]")


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def artifact_hash(value: Any) -> str:
    """Return the stable full-artifact hash used by private candidate runs."""

    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _strict_json_loads(value: str) -> Any:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = item
        return result

    return json.loads(
        value,
        object_pairs_hook=reject_duplicates,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("non-finite JSON number")),
    )


def _load_json_bounded(path: Path, label: str, maximum: int) -> Any:
    try:
        if path.stat().st_size > maximum:
            raise ReadingPackError(f"{label} exceeds {maximum} bytes", EXIT_IO)
        return _strict_json_loads(path.read_text(encoding="utf-8"))
    except ReadingPackError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise ReadingPackError(f"cannot read {label} {path}: {exc}", EXIT_IO) from exc


def _require_exact_keys(value: Mapping[str, Any], expected: set[str], path: str) -> None:
    missing = expected - set(value)
    extra = set(value) - expected
    if missing or extra:
        details = []
        if missing:
            details.append(f"missing {sorted(missing)}")
        if extra:
            details.append(f"unexpected {sorted(extra)}")
        raise ReadingPackError(f"invalid {path}: {'; '.join(details)}")


def _safe_line(value: Any, maximum: int = 500) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and len(value) <= maximum
        and _CONTROL.search(value) is None
    )


def _work_id(module: str, scope: Mapping[str, Any]) -> str:
    payload = {"module": module, "scope": dict(scope)}
    return f"WORK-{artifact_hash(payload)[:20].upper()}"


def _plan_projection(ledger: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": ledger.get("schema_version"),
        "language": ledger.get("language"),
        "canonical_data_sha256": ledger.get("canonical_data_sha256"),
        "modules": ledger.get("modules"),
        "items": [
            {
                "work_id": item.get("work_id"),
                "module": item.get("module"),
                "scope": item.get("scope"),
            }
            for item in ledger.get("items", [])
            if isinstance(item, Mapping)
        ],
    }


def _plan_id(ledger: Mapping[str, Any]) -> str:
    return f"WP-{artifact_hash(_plan_projection(ledger))[:20].upper()}"


def _ledger_summary(items: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    counts = Counter(item.get("status") for item in items)
    return {
        "total": sum(counts.values()),
        "pending": counts["pending"],
        "complete": counts["complete"],
        "no_supported_candidate": counts["no_supported_candidate"],
        "failed": counts["failed"],
        "skipped": counts["skipped"],
    }


def _ledger_integrity(ledger: Mapping[str, Any]) -> str:
    return artifact_hash({key: value for key, value in ledger.items() if key != "integrity_sha256"})


def create_work_ledger(
    *,
    language: str,
    canonical_data: Mapping[str, Any],
    modules: Sequence[str],
) -> dict[str, Any]:
    """Create a deterministic, body-free work ledger for the current snapshot."""

    if language not in {"ja", "en"}:
        raise ReadingPackError("work ledger language must be ja or en")
    if not isinstance(canonical_data, Mapping) or canonical_data.get("language") != language:
        raise ReadingPackError("canonical data does not match the work ledger language")
    requested = set(modules)
    if not requested:
        raise ReadingPackError("at least one work module is required")
    unknown = requested - set(MODULES)
    if unknown:
        raise ReadingPackError(f"unknown work module(s): {', '.join(sorted(unknown))}")
    ordered_modules = [module for module in MODULES if module in requested]

    raw_chapters = canonical_data.get("chapters")
    if not isinstance(raw_chapters, list) or not raw_chapters:
        raise ReadingPackError("canonical data must contain at least one chapter")
    chapter_ids: list[str] = []
    seen_chapters: set[str] = set()
    for index, chapter in enumerate(raw_chapters):
        identifier = chapter.get("id") if isinstance(chapter, Mapping) else None
        if not _safe_line(identifier, 100) or identifier in seen_chapters:
            raise ReadingPackError(f"canonical chapter at index {index} has an invalid or duplicate id")
        seen_chapters.add(identifier)
        chapter_ids.append(identifier)

    items: list[dict[str, Any]] = []
    for module in ordered_modules:
        scopes: list[dict[str, str]]
        if module in CHAPTER_SCOPED_MODULES:
            scopes = [{"kind": "chapter", "chapter_id": chapter_id} for chapter_id in chapter_ids]
            if module in BOOK_FALLBACK_MODULES:
                scopes.append({"kind": "book"})
        else:
            scopes = [{"kind": "book"}]
        for scope in scopes:
            items.append(
                {
                    "work_id": _work_id(module, scope),
                    "module": module,
                    "scope": scope,
                    "status": "pending",
                    "reason_code": "",
                    "candidate_ids": [],
                }
            )

    ledger: dict[str, Any] = {
        "schema_version": WORK_LEDGER_SCHEMA_VERSION,
        "plan_id": "",
        "language": language,
        "canonical_data_sha256": artifact_hash(canonical_data),
        "run": None,
        "modules": ordered_modules,
        "items": items,
        "summary": _ledger_summary(items),
        "integrity_sha256": "",
    }
    ledger["plan_id"] = _plan_id(ledger)
    ledger["integrity_sha256"] = _ledger_integrity(ledger)
    validate_work_ledger(ledger)
    return ledger


def _validate_scope(scope: Any, module: str, path: str) -> None:
    if not isinstance(scope, Mapping):
        raise ReadingPackError(f"invalid work ledger: {path} must be an object")
    if module in CHAPTER_SCOPED_MODULES and scope.get("kind") != "book":
        _require_exact_keys(scope, {"kind", "chapter_id"}, f"work ledger {path}")
        if scope.get("kind") != "chapter" or not _safe_line(scope.get("chapter_id"), 100):
            raise ReadingPackError(f"invalid work ledger: {path} must identify a chapter")
    else:
        _require_exact_keys(scope, {"kind"}, f"work ledger {path}")
        if scope.get("kind") != "book" or (
            module in CHAPTER_SCOPED_MODULES and module not in BOOK_FALLBACK_MODULES
        ):
            raise ReadingPackError(f"invalid work ledger: {path} must identify the book")


def _validate_status_payload(item: Mapping[str, Any], path: str, *, allow_pending: bool) -> None:
    status = item.get("status")
    allowed = WORK_STATUSES if allow_pending else TERMINAL_WORK_STATUSES
    if status not in allowed:
        raise ReadingPackError(f"invalid {path}: unsupported status")
    reason = item.get("reason_code")
    candidates = item.get("candidate_ids")
    if not isinstance(reason, str) or (
        reason and _REASON_CODE.fullmatch(reason) is None
    ):
        raise ReadingPackError(f"invalid {path}: reason_code is invalid")
    if (
        not isinstance(candidates, list)
        or len(candidates) > MAX_CANDIDATES_PER_ITEM
        or len(candidates) != len(set(candidates))
        or not all(isinstance(value, str) and _CANDIDATE_ID.fullmatch(value) for value in candidates)
    ):
        raise ReadingPackError(f"invalid {path}: candidate_ids are invalid")
    if status == "complete":
        if not candidates or reason:
            raise ReadingPackError(
                f"invalid {path}: complete requires candidate_ids and an empty reason_code"
            )
    elif candidates:
        raise ReadingPackError(f"invalid {path}: only complete may contain candidate_ids")
    elif status in {"no_supported_candidate", "failed", "skipped"} and not reason:
        raise ReadingPackError(f"invalid {path}: {status} requires a reason_code")
    elif status == "pending" and reason:
        raise ReadingPackError(f"invalid {path}: pending requires an empty reason_code")


def validate_work_ledger(ledger: Mapping[str, Any]) -> None:
    require_structure("generation-ledger.schema.json", ledger, label="work ledger")
    if not isinstance(ledger, Mapping):
        raise ReadingPackError("invalid work ledger: root must be an object")
    expected = {
        "schema_version",
        "plan_id",
        "language",
        "canonical_data_sha256",
        "run",
        "modules",
        "items",
        "summary",
        "integrity_sha256",
    }
    _require_exact_keys(ledger, expected, "work ledger root")
    if ledger.get("schema_version") != WORK_LEDGER_SCHEMA_VERSION:
        raise ReadingPackError("invalid work ledger: unsupported schema_version")
    if ledger.get("language") not in {"ja", "en"}:
        raise ReadingPackError("invalid work ledger: language is invalid")
    if not isinstance(ledger.get("canonical_data_sha256"), str) or not _SHA256.fullmatch(
        ledger["canonical_data_sha256"]
    ):
        raise ReadingPackError("invalid work ledger: canonical_data_sha256 is invalid")
    run = ledger.get("run")
    if run is not None and (
        not isinstance(run, Mapping)
        or set(run) != {"run_id", "integrity_sha256"}
        or not _safe_line(run.get("run_id"), 200)
        or not isinstance(run.get("integrity_sha256"), str)
        or not _SHA256.fullmatch(run["integrity_sha256"])
    ):
        raise ReadingPackError("invalid work ledger: run binding is invalid")
    modules = ledger.get("modules")
    if (
        not isinstance(modules, list)
        or not modules
        or len(modules) != len(set(modules))
        or any(module not in MODULES for module in modules)
        or modules != [module for module in MODULES if module in set(modules)]
    ):
        raise ReadingPackError("invalid work ledger: modules are invalid or unordered")
    items = ledger.get("items")
    if not isinstance(items, list) or not items or len(items) > MAX_WORK_ITEMS:
        raise ReadingPackError("invalid work ledger: items must be a bounded non-empty array")
    seen_ids: set[str] = set()
    seen_scopes: set[tuple[str, str]] = set()
    for index, item in enumerate(items):
        path = f"items[{index}]"
        if not isinstance(item, Mapping):
            raise ReadingPackError(f"invalid work ledger: {path} must be an object")
        _require_exact_keys(
            item,
            {"work_id", "module", "scope", "status", "reason_code", "candidate_ids"},
            f"work ledger {path}",
        )
        module = item.get("module")
        if module not in modules:
            raise ReadingPackError(f"invalid work ledger: {path}.module is not declared")
        _validate_scope(item.get("scope"), module, f"{path}.scope")
        work_id = item.get("work_id")
        if (
            not isinstance(work_id, str)
            or not _WORK_ID.fullmatch(work_id)
            or work_id != _work_id(module, item["scope"])
            or work_id in seen_ids
        ):
            raise ReadingPackError(f"invalid work ledger: {path}.work_id is invalid or duplicate")
        seen_ids.add(work_id)
        scope_key = (module, json.dumps(item["scope"], sort_keys=True))
        if scope_key in seen_scopes:
            raise ReadingPackError(f"invalid work ledger: duplicate module scope at {path}")
        seen_scopes.add(scope_key)
        _validate_status_payload(item, f"work ledger {path}", allow_pending=True)
    if ledger.get("summary") != _ledger_summary(items):
        raise ReadingPackError("invalid work ledger: summary does not match items")
    if not isinstance(ledger.get("plan_id"), str) or not _PLAN_ID.fullmatch(ledger["plan_id"]):
        raise ReadingPackError("invalid work ledger: plan_id is invalid")
    if ledger["plan_id"] != _plan_id(ledger):
        raise ReadingPackError("invalid work ledger: plan_id binding check failed")
    if not isinstance(ledger.get("integrity_sha256"), str) or not _SHA256.fullmatch(
        ledger["integrity_sha256"]
    ):
        raise ReadingPackError("invalid work ledger: integrity_sha256 is invalid")
    if ledger["integrity_sha256"] != _ledger_integrity(ledger):
        raise ReadingPackError("work ledger integrity check failed")


def write_work_ledger(path: Path, ledger: Mapping[str, Any], *, overwrite: bool = False) -> None:
    validate_work_ledger(ledger)
    path = path.resolve()
    if path.exists() and not overwrite:
        raise ReadingPackError(f"refusing to overwrite existing work ledger: {path}", EXIT_IO)
    encoded = json.dumps(ledger, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    if len(encoded.encode("utf-8")) > MAX_LEDGER_BYTES:
        raise ReadingPackError(f"work ledger exceeds {MAX_LEDGER_BYTES} bytes", EXIT_IO)
    atomic_write_text(path, encoded)


def load_work_ledger(path: Path) -> dict[str, Any]:
    value = _load_json_bounded(path.resolve(), "work ledger", MAX_LEDGER_BYTES)
    validate_work_ledger(value)
    return value


def load_work_results(path: Path, *, ledger: Mapping[str, Any], run_id: str) -> dict[str, Any]:
    value = _load_json_bounded(path.resolve(), "work results", MAX_RESULTS_BYTES)
    require_structure("generation-results.schema.json", value, label="work results")
    if not isinstance(value, Mapping):
        raise ReadingPackError("invalid work results: root must be an object")
    _require_exact_keys(value, {"schema_version", "plan_id", "run_id", "results"}, "work results root")
    if value.get("schema_version") != 1:
        raise ReadingPackError("invalid work results: unsupported schema_version")
    if value.get("plan_id") != ledger.get("plan_id"):
        raise ReadingPackError("work results plan_id does not match the ledger")
    if value.get("run_id") != run_id:
        raise ReadingPackError("work results run_id does not match the candidate run")
    results = value.get("results")
    if not isinstance(results, list) or not results or len(results) > MAX_WORK_ITEMS:
        raise ReadingPackError("invalid work results: results must be a bounded non-empty array")
    seen: set[str] = set()
    for index, item in enumerate(results):
        path_text = f"work results results[{index}]"
        if not isinstance(item, Mapping):
            raise ReadingPackError(f"invalid {path_text}: must be an object")
        _require_exact_keys(
            item,
            {"work_id", "status", "reason_code", "candidate_ids"},
            path_text,
        )
        work_id = item.get("work_id")
        if not isinstance(work_id, str) or not _WORK_ID.fullmatch(work_id) or work_id in seen:
            raise ReadingPackError(f"invalid {path_text}: work_id is invalid or duplicate")
        seen.add(work_id)
        _validate_status_payload(item, path_text, allow_pending=False)
    return dict(value)


def _candidate_matches_item(candidate: Mapping[str, Any], item: Mapping[str, Any]) -> bool:
    module = item.get("module")
    if candidate.get("collection") != CANDIDATE_COLLECTIONS.get(module):
        return False
    scope = item["scope"]
    if scope.get("kind") == "book":
        record = candidate.get("record")
        if module in BOOK_FALLBACK_MODULES:
            return isinstance(record, Mapping) and record.get("chapter_ids") == []
        return True
    chapter_id = scope["chapter_id"]
    record = candidate.get("record")
    if not isinstance(record, Mapping):
        return False
    module = item["module"]
    if module in {"chapters", "summaries", "chapter_terms"}:
        return candidate.get("record_id") == chapter_id
    if module in {"claims", "qa", "misreadings"}:
        chapter_ids = record.get("chapter_ids")
        return isinstance(chapter_ids, list) and chapter_id in chapter_ids
    if module in {"names", "glossary"}:
        return record.get("chapter_id") == chapter_id
    return False


def reconcile_work_results(
    ledger: Mapping[str, Any],
    results: Mapping[str, Any],
    candidate_run: Mapping[str, Any],
) -> dict[str, Any]:
    """Apply explicit outcomes after checking every completed candidate binding."""

    validate_work_ledger(ledger)
    if results.get("plan_id") != ledger.get("plan_id"):
        raise ReadingPackError("work results plan_id does not match the ledger")
    if not isinstance(candidate_run, Mapping):
        raise ReadingPackError("candidate run must be an object")
    if results.get("run_id") != candidate_run.get("run_id"):
        raise ReadingPackError("work results run_id does not match the candidate run")
    run_binding = {
        "run_id": candidate_run.get("run_id"),
        "integrity_sha256": candidate_run.get("integrity_sha256"),
    }
    if (
        not _safe_line(run_binding["run_id"], 200)
        or not isinstance(run_binding["integrity_sha256"], str)
        or not _SHA256.fullmatch(run_binding["integrity_sha256"])
    ):
        raise ReadingPackError("candidate run binding is invalid")
    if ledger.get("run") is not None and ledger.get("run") != run_binding:
        raise ReadingPackError("candidate run does not match the work ledger run binding")
    if candidate_run.get("language") != ledger.get("language"):
        raise ReadingPackError("candidate run language does not match the work ledger")
    canonical = candidate_run.get("canonical")
    if not isinstance(canonical, Mapping) or canonical.get("data_sha256") != ledger.get(
        "canonical_data_sha256"
    ):
        raise ReadingPackError("candidate run canonical snapshot does not match the work ledger")
    raw_candidates = candidate_run.get("candidates")
    if not isinstance(raw_candidates, list):
        raise ReadingPackError("candidate run candidates are invalid")
    candidates = {
        candidate.get("candidate_id"): candidate
        for candidate in raw_candidates
        if isinstance(candidate, Mapping) and isinstance(candidate.get("candidate_id"), str)
    }
    items = {item["work_id"]: dict(item) for item in ledger["items"]}
    for result in results.get("results", []):
        work_id = result["work_id"]
        item = items.get(work_id)
        if item is None:
            raise ReadingPackError(f"work results reference unknown work_id: {work_id}")
        desired = {
            "status": result["status"],
            "reason_code": result["reason_code"],
            "candidate_ids": list(result["candidate_ids"]),
        }
        current = {key: item[key] for key in desired}
        if item["status"] != "pending":
            if current == desired:
                continue
            raise ReadingPackError(f"work item {work_id} already has a conflicting terminal outcome")
        if result["status"] == "complete":
            for candidate_id in result["candidate_ids"]:
                candidate = candidates.get(candidate_id)
                if candidate is None:
                    raise ReadingPackError(
                        f"work item {work_id} references unknown candidate_id: {candidate_id}"
                    )
                if candidate.get("candidate_state") not in USABLE_CANDIDATE_STATES:
                    raise ReadingPackError(
                        f"work item {work_id} references a non-reviewable candidate: {candidate_id}"
                    )
                if not _candidate_matches_item(candidate, item):
                    raise ReadingPackError(
                        f"candidate {candidate_id} does not match work item {work_id}"
                    )
        item.update(desired)

    reconciled = {
        **ledger,
        "run": run_binding,
        "items": [items[item["work_id"]] for item in ledger["items"]],
    }
    reconciled["summary"] = _ledger_summary(reconciled["items"])
    reconciled["integrity_sha256"] = _ledger_integrity(reconciled)
    validate_work_ledger(reconciled)
    return reconciled


def _module_summary(items: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for module in MODULES:
        selected = [item for item in items if item.get("module") == module]
        if not selected:
            continue
        counts = _ledger_summary(selected)
        result.append({"module": module, **counts})
    return result


def coverage_report(
    ledger: Mapping[str, Any],
    semantic_review: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return gate-like coverage states, never a quality score or approval."""

    validate_work_ledger(ledger)
    summary = ledger["summary"]
    if summary["failed"] or summary["pending"]:
        generation_state = "incomplete"
    elif summary["no_supported_candidate"] or summary["skipped"]:
        generation_state = "fully_accounted"
    else:
        generation_state = "fully_generated"
    semantic = {"state": "not_assessed", "summary": None}
    if semantic_review is not None:
        validate_semantic_review(semantic_review)
        if semantic_review.get("plan_id") != ledger.get("plan_id"):
            raise ReadingPackError("semantic review plan_id does not match the work ledger")
        if semantic_review.get("ledger_integrity_sha256") != ledger.get("integrity_sha256"):
            raise ReadingPackError("semantic review ledger binding does not match the work ledger")
        if ledger.get("run") is None or semantic_review.get("run") != ledger.get("run"):
            raise ReadingPackError("semantic review run does not match the work ledger")
        sem_summary = semantic_review["summary"]
        assessment = semantic_review["assessment"]
        if assessment["status"] != "complete":
            semantic_state = "review_required"
        elif sem_summary["blocking_errors"]:
            semantic_state = "blocked"
        elif sem_summary["pending"] or sem_summary["confirmed"]:
            semantic_state = "review_required"
        else:
            semantic_state = "clear"
        semantic = {"state": semantic_state, "summary": sem_summary}
    return {
        "plan_id": ledger["plan_id"],
        "language": ledger["language"],
        "canonical_data_sha256": ledger["canonical_data_sha256"],
        "generation_state": generation_state,
        "summary": summary,
        "modules": _module_summary(ledger["items"]),
        "semantic": semantic,
        "approval_granted": False,
    }


def load_semantic_findings(
    path: Path,
    *,
    ledger: Mapping[str, Any],
    run_id: str,
) -> dict[str, Any]:
    value = _load_json_bounded(path.resolve(), "semantic findings", MAX_RESULTS_BYTES)
    require_structure(
        "semantic-findings.schema.json", value, label="semantic findings"
    )
    if not isinstance(value, Mapping):
        raise ReadingPackError("invalid semantic findings: root must be an object")
    _require_exact_keys(
        value,
        {"schema_version", "plan_id", "run_id", "assessment", "findings"},
        "semantic findings root",
    )
    if value.get("schema_version") != 1:
        raise ReadingPackError("invalid semantic findings: unsupported schema_version")
    if value.get("plan_id") != ledger.get("plan_id"):
        raise ReadingPackError("semantic findings plan_id does not match the work ledger")
    if value.get("run_id") != run_id:
        raise ReadingPackError("semantic findings run_id does not match the candidate run")
    _validate_assessment(value.get("assessment"), "semantic findings assessment")
    findings = value.get("findings")
    if not isinstance(findings, list) or len(findings) > MAX_FINDINGS:
        raise ReadingPackError("invalid semantic findings: findings must be a bounded array")
    for index, finding in enumerate(findings):
        path_text = f"semantic findings findings[{index}]"
        if not isinstance(finding, Mapping):
            raise ReadingPackError(f"invalid {path_text}: must be an object")
        _require_exact_keys(
            finding,
            {
                "work_id",
                "candidate_id",
                "category",
                "severity",
                "reason_code",
                "evidence_ref_ids",
            },
            path_text,
        )
        if not isinstance(finding.get("work_id"), str) or not _WORK_ID.fullmatch(
            finding["work_id"]
        ):
            raise ReadingPackError(f"invalid {path_text}: work_id is invalid")
        if not isinstance(finding.get("candidate_id"), str) or not _CANDIDATE_ID.fullmatch(
            finding["candidate_id"]
        ):
            raise ReadingPackError(f"invalid {path_text}: candidate_id is invalid")
        if finding.get("category") not in SEMANTIC_CATEGORIES:
            raise ReadingPackError(f"invalid {path_text}: category is unsupported")
        if finding.get("severity") not in SEMANTIC_SEVERITIES:
            raise ReadingPackError(f"invalid {path_text}: severity is unsupported")
        if not isinstance(finding.get("reason_code"), str) or not _REASON_CODE.fullmatch(
            finding["reason_code"]
        ):
            raise ReadingPackError(f"invalid {path_text}: reason_code is invalid")
        refs = finding.get("evidence_ref_ids")
        if (
            not isinstance(refs, list)
            or len(refs) > MAX_EVIDENCE_REFS_PER_FINDING
            or len(refs) != len(set(refs))
            or not all(isinstance(ref, str) and _EVIDENCE_ID.fullmatch(ref) for ref in refs)
        ):
            raise ReadingPackError(f"invalid {path_text}: evidence_ref_ids are invalid")
    return dict(value)


def _finding_id(
    *,
    plan_id: str,
    run_id: str,
    finding: Mapping[str, Any],
) -> str:
    return f"SEM-{artifact_hash({'plan_id': plan_id, 'run_id': run_id, **finding})[:20].upper()}"


def _review_projection(review: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": review.get("schema_version"),
        "plan_id": review.get("plan_id"),
        "ledger_integrity_sha256": review.get("ledger_integrity_sha256"),
        "run": review.get("run"),
        "assessment": review.get("assessment"),
        "findings": [
            {key: value for key, value in finding.items() if key != "adjudication"}
            for finding in review.get("findings", [])
            if isinstance(finding, Mapping)
        ],
    }


def _review_id(review: Mapping[str, Any]) -> str:
    return f"SR-{artifact_hash(_review_projection(review))[:20].upper()}"


def _semantic_summary(findings: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    finding_list = list(findings)
    severities = Counter(item.get("severity") for item in finding_list)
    decisions = Counter(
        item.get("adjudication", {}).get("decision")
        for item in finding_list
        if isinstance(item.get("adjudication"), Mapping)
    )
    blocking_errors = sum(
        1
        for item in finding_list
        if item.get("severity") == "error"
        and item.get("adjudication", {}).get("decision") != "dismissed"
    )
    return {
        "total": len(finding_list),
        "warning": severities["warning"],
        "error": severities["error"],
        "pending": decisions["pending"],
        "confirmed": decisions["confirmed"],
        "dismissed": decisions["dismissed"],
        "accepted_risk": decisions["accepted_risk"],
        "blocking_errors": blocking_errors,
    }


def _review_integrity(review: Mapping[str, Any]) -> str:
    return artifact_hash({key: value for key, value in review.items() if key != "integrity_sha256"})


def _validate_assessment(value: Any, path: str) -> None:
    if not isinstance(value, Mapping):
        raise ReadingPackError(f"invalid {path}: must be an object")
    _require_exact_keys(
        value,
        {"status", "assessor", "assessed_candidate_ids"},
        path,
    )
    if value.get("status") not in {"partial", "complete"}:
        raise ReadingPackError(f"invalid {path}: status must be partial or complete")
    if not _safe_line(value.get("assessor"), 500):
        raise ReadingPackError(f"invalid {path}: assessor is required")
    identifiers = value.get("assessed_candidate_ids")
    if (
        not isinstance(identifiers, list)
        or len(identifiers) > MAX_WORK_ITEMS * MAX_CANDIDATES_PER_ITEM
        or len(identifiers) != len(set(identifiers))
        or not all(
            isinstance(identifier, str) and _CANDIDATE_ID.fullmatch(identifier)
            for identifier in identifiers
        )
    ):
        raise ReadingPackError(f"invalid {path}: assessed_candidate_ids are invalid")


def create_semantic_review(
    *,
    ledger: Mapping[str, Any],
    candidate_run: Mapping[str, Any],
    findings_input: Mapping[str, Any],
) -> dict[str, Any]:
    """Bind excerpt-free semantic findings to exact work and candidate artifacts."""

    validate_work_ledger(ledger)
    if findings_input.get("plan_id") != ledger.get("plan_id"):
        raise ReadingPackError("semantic findings plan_id does not match the work ledger")
    if findings_input.get("run_id") != candidate_run.get("run_id"):
        raise ReadingPackError("semantic findings run_id does not match the candidate run")
    if ledger.get("run") != {
        "run_id": candidate_run.get("run_id"),
        "integrity_sha256": candidate_run.get("integrity_sha256"),
    }:
        raise ReadingPackError("candidate run does not match the reconciled work ledger")
    if candidate_run.get("language") != ledger.get("language"):
        raise ReadingPackError("candidate run language does not match the work ledger")
    canonical = candidate_run.get("canonical")
    if not isinstance(canonical, Mapping) or canonical.get("data_sha256") != ledger.get(
        "canonical_data_sha256"
    ):
        raise ReadingPackError("candidate run canonical snapshot does not match the work ledger")
    run_integrity = candidate_run.get("integrity_sha256")
    if not isinstance(run_integrity, str) or not _SHA256.fullmatch(run_integrity):
        raise ReadingPackError("candidate run integrity binding is invalid")
    candidates = {
        item.get("candidate_id"): item
        for item in candidate_run.get("candidates", [])
        if isinstance(item, Mapping) and isinstance(item.get("candidate_id"), str)
    }
    work_items = {item["work_id"]: item for item in ledger["items"]}
    assessment = findings_input.get("assessment")
    _validate_assessment(assessment, "semantic findings assessment")
    eligible_candidate_ids = {
        candidate_id
        for item in ledger["items"]
        for candidate_id in item.get("candidate_ids", [])
    }
    assessed_candidate_ids = set(assessment["assessed_candidate_ids"])
    if not assessed_candidate_ids <= eligible_candidate_ids:
        raise ReadingPackError(
            "semantic assessment references candidates outside the reconciled work ledger"
        )
    if assessment["status"] == "complete" and assessed_candidate_ids != eligible_candidate_ids:
        raise ReadingPackError(
            "complete semantic assessment must cover every candidate in the work ledger"
        )
    findings: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for raw in findings_input.get("findings", []):
        if raw["candidate_id"] not in assessed_candidate_ids:
            raise ReadingPackError(
                "semantic finding references a candidate outside the assessed set"
            )
        work_item = work_items.get(raw["work_id"])
        if work_item is None:
            raise ReadingPackError(f"semantic finding references unknown work_id: {raw['work_id']}")
        if raw["candidate_id"] not in work_item.get("candidate_ids", []):
            raise ReadingPackError(
                f"semantic finding candidate is not bound to work item {raw['work_id']}"
            )
        candidate = candidates.get(raw["candidate_id"])
        if candidate is None or candidate.get("candidate_state") not in USABLE_CANDIDATE_STATES:
            raise ReadingPackError(
                f"semantic finding references a missing or non-reviewable candidate: {raw['candidate_id']}"
            )
        known_evidence = {
            ref.get("id")
            for ref in candidate.get("evidence_refs", [])
            if isinstance(ref, Mapping)
        }
        if not set(raw["evidence_ref_ids"]) <= known_evidence:
            raise ReadingPackError(
                f"semantic finding references evidence outside candidate {raw['candidate_id']}"
            )
        finding_id = _finding_id(
            plan_id=ledger["plan_id"],
            run_id=candidate_run["run_id"],
            finding=raw,
        )
        if finding_id in seen_ids:
            raise ReadingPackError("semantic findings contain a duplicate finding")
        seen_ids.add(finding_id)
        findings.append(
            {
                "finding_id": finding_id,
                **raw,
                "adjudication": {
                    "decision": "pending",
                    "reviewer": "",
                    "reviewed_at": "",
                },
            }
        )
    review: dict[str, Any] = {
        "schema_version": SEMANTIC_REVIEW_SCHEMA_VERSION,
        "review_id": "",
        "plan_id": ledger["plan_id"],
        "ledger_integrity_sha256": ledger["integrity_sha256"],
        "run": {
            "run_id": candidate_run["run_id"],
            "integrity_sha256": run_integrity,
        },
        "assessment": {
            "status": assessment["status"],
            "assessor": assessment["assessor"],
            "assessed_candidate_ids": list(assessment["assessed_candidate_ids"]),
        },
        "findings": findings,
        "summary": _semantic_summary(findings),
        "integrity_sha256": "",
    }
    review["review_id"] = _review_id(review)
    review["integrity_sha256"] = _review_integrity(review)
    validate_semantic_review(review)
    return review


def _validate_adjudication(value: Any, path: str) -> None:
    if not isinstance(value, Mapping):
        raise ReadingPackError(f"invalid semantic review: {path} must be an object")
    _require_exact_keys(value, {"decision", "reviewer", "reviewed_at"}, f"semantic review {path}")
    decision = value.get("decision")
    if decision not in ADJUDICATION_DECISIONS:
        raise ReadingPackError(f"invalid semantic review: {path}.decision is unsupported")
    reviewer = value.get("reviewer")
    reviewed_at = value.get("reviewed_at")
    if decision == "pending":
        if reviewer != "" or reviewed_at != "":
            raise ReadingPackError(f"invalid semantic review: pending {path} must be blank")
    else:
        if not _safe_line(reviewer, 500) or not _safe_line(reviewed_at, 200):
            raise ReadingPackError(f"invalid semantic review: adjudicated {path} requires reviewer and time")
        try:
            datetime.fromisoformat(reviewed_at)
        except ValueError as exc:
            raise ReadingPackError(f"invalid semantic review: {path}.reviewed_at is invalid") from exc


def validate_semantic_review(review: Mapping[str, Any]) -> None:
    require_structure("semantic-review.schema.json", review, label="semantic review")
    if not isinstance(review, Mapping):
        raise ReadingPackError("invalid semantic review: root must be an object")
    expected = {
        "schema_version",
        "review_id",
        "plan_id",
        "ledger_integrity_sha256",
        "run",
        "assessment",
        "findings",
        "summary",
        "integrity_sha256",
    }
    _require_exact_keys(review, expected, "semantic review root")
    if review.get("schema_version") != SEMANTIC_REVIEW_SCHEMA_VERSION:
        raise ReadingPackError("invalid semantic review: unsupported schema_version")
    if not isinstance(review.get("plan_id"), str) or not _PLAN_ID.fullmatch(review["plan_id"]):
        raise ReadingPackError("invalid semantic review: plan_id is invalid")
    if not isinstance(review.get("ledger_integrity_sha256"), str) or not _SHA256.fullmatch(
        review["ledger_integrity_sha256"]
    ):
        raise ReadingPackError("invalid semantic review: ledger integrity binding is invalid")
    run = review.get("run")
    if not isinstance(run, Mapping):
        raise ReadingPackError("invalid semantic review: run must be an object")
    _require_exact_keys(run, {"run_id", "integrity_sha256"}, "semantic review run")
    if not _safe_line(run.get("run_id"), 200):
        raise ReadingPackError("invalid semantic review: run_id is invalid")
    if not isinstance(run.get("integrity_sha256"), str) or not _SHA256.fullmatch(
        run["integrity_sha256"]
    ):
        raise ReadingPackError("invalid semantic review: run integrity is invalid")
    _validate_assessment(review.get("assessment"), "semantic review assessment")
    findings = review.get("findings")
    if not isinstance(findings, list) or len(findings) > MAX_FINDINGS:
        raise ReadingPackError("invalid semantic review: findings must be a bounded array")
    seen: set[str] = set()
    for index, finding in enumerate(findings):
        path = f"findings[{index}]"
        if not isinstance(finding, Mapping):
            raise ReadingPackError(f"invalid semantic review: {path} must be an object")
        _require_exact_keys(
            finding,
            {
                "finding_id",
                "work_id",
                "candidate_id",
                "category",
                "severity",
                "reason_code",
                "evidence_ref_ids",
                "adjudication",
            },
            f"semantic review {path}",
        )
        finding_id = finding.get("finding_id")
        if (
            not isinstance(finding_id, str)
            or not _FINDING_ID.fullmatch(finding_id)
            or finding_id in seen
        ):
            raise ReadingPackError(f"invalid semantic review: {path}.finding_id is invalid or duplicate")
        seen.add(finding_id)
        raw = {key: value for key, value in finding.items() if key not in {"finding_id", "adjudication"}}
        if finding_id != _finding_id(plan_id=review["plan_id"], run_id=run["run_id"], finding=raw):
            raise ReadingPackError(f"invalid semantic review: {path}.finding_id binding failed")
        if not isinstance(finding.get("work_id"), str) or not _WORK_ID.fullmatch(finding["work_id"]):
            raise ReadingPackError(f"invalid semantic review: {path}.work_id is invalid")
        if not isinstance(finding.get("candidate_id"), str) or not _CANDIDATE_ID.fullmatch(
            finding["candidate_id"]
        ):
            raise ReadingPackError(f"invalid semantic review: {path}.candidate_id is invalid")
        if finding.get("category") not in SEMANTIC_CATEGORIES:
            raise ReadingPackError(f"invalid semantic review: {path}.category is unsupported")
        if finding.get("severity") not in SEMANTIC_SEVERITIES:
            raise ReadingPackError(f"invalid semantic review: {path}.severity is unsupported")
        if not isinstance(finding.get("reason_code"), str) or not _REASON_CODE.fullmatch(
            finding["reason_code"]
        ):
            raise ReadingPackError(f"invalid semantic review: {path}.reason_code is invalid")
        refs = finding.get("evidence_ref_ids")
        if (
            not isinstance(refs, list)
            or len(refs) > MAX_EVIDENCE_REFS_PER_FINDING
            or len(refs) != len(set(refs))
            or not all(isinstance(ref, str) and _EVIDENCE_ID.fullmatch(ref) for ref in refs)
        ):
            raise ReadingPackError(f"invalid semantic review: {path}.evidence_ref_ids are invalid")
        _validate_adjudication(finding.get("adjudication"), f"{path}.adjudication")
    if review.get("summary") != _semantic_summary(findings):
        raise ReadingPackError("invalid semantic review: summary does not match findings")
    if not isinstance(review.get("review_id"), str) or not _REVIEW_ID.fullmatch(review["review_id"]):
        raise ReadingPackError("invalid semantic review: review_id is invalid")
    if review["review_id"] != _review_id(review):
        raise ReadingPackError("invalid semantic review: review_id binding check failed")
    if not isinstance(review.get("integrity_sha256"), str) or not _SHA256.fullmatch(
        review["integrity_sha256"]
    ):
        raise ReadingPackError("invalid semantic review: integrity_sha256 is invalid")
    if review["integrity_sha256"] != _review_integrity(review):
        raise ReadingPackError("semantic review integrity check failed")


def write_semantic_review(path: Path, review: Mapping[str, Any], *, overwrite: bool = False) -> None:
    validate_semantic_review(review)
    path = path.resolve()
    if path.exists() and not overwrite:
        raise ReadingPackError(f"refusing to overwrite existing semantic review: {path}", EXIT_IO)
    encoded = json.dumps(review, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    if len(encoded.encode("utf-8")) > MAX_SEMANTIC_REVIEW_BYTES:
        raise ReadingPackError(
            f"semantic review exceeds {MAX_SEMANTIC_REVIEW_BYTES} bytes", EXIT_IO
        )
    atomic_write_text(path, encoded)
    try:
        path.chmod(0o600)
    except OSError as exc:
        raise ReadingPackError(f"cannot restrict semantic review permissions: {exc}", EXIT_IO) from exc


def load_semantic_review(path: Path) -> dict[str, Any]:
    value = _load_json_bounded(path.resolve(), "semantic review", MAX_SEMANTIC_REVIEW_BYTES)
    validate_semantic_review(value)
    return value


@contextmanager
def _semantic_review_lock(path: Path):
    try:
        import fcntl
    except ImportError as exc:  # pragma: no cover - supported runtime is POSIX
        raise ReadingPackError("semantic review locking requires POSIX fcntl", EXIT_IO) from exc
    lock_path = path.parent / f".{path.name}.lock"
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    except OSError as exc:
        raise ReadingPackError(f"cannot open semantic review lock: {exc}", EXIT_IO) from exc
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def adjudicate_semantic_findings(
    path: Path,
    finding_ids: Sequence[str],
    *,
    decision: str,
    reviewer: str,
    reviewed_at: str | None = None,
) -> list[str]:
    """Record a human judgment; confirmed errors remain blocking for this artifact."""

    if decision not in ADJUDICATION_DECISIONS - {"pending"}:
        raise ReadingPackError("semantic adjudication decision is unsupported")
    if not _safe_line(reviewer, 500):
        raise ReadingPackError("semantic adjudication requires a safe, named reviewer")
    requested = list(dict.fromkeys(finding_ids))
    if not requested or not all(_FINDING_ID.fullmatch(value) for value in requested):
        raise ReadingPackError("semantic adjudication requires valid finding IDs")
    timestamp = reviewed_at or datetime.now(timezone.utc).isoformat()
    try:
        datetime.fromisoformat(timestamp)
    except ValueError as exc:
        raise ReadingPackError("semantic adjudication reviewed_at is invalid") from exc
    path = path.resolve()
    with _semantic_review_lock(path):
        review = load_semantic_review(path)
        by_id = {item["finding_id"]: item for item in review["findings"]}
        missing = [identifier for identifier in requested if identifier not in by_id]
        if missing:
            raise ReadingPackError(f"unknown semantic finding id(s): {', '.join(missing)}")
        for identifier in requested:
            current = by_id[identifier]["adjudication"]
            desired = {
                "decision": decision,
                "reviewer": reviewer,
                "reviewed_at": timestamp,
            }
            if current["decision"] != "pending" and current != desired:
                raise ReadingPackError(f"semantic finding {identifier} was already adjudicated")
            by_id[identifier]["adjudication"] = desired
        review["summary"] = _semantic_summary(review["findings"])
        review["integrity_sha256"] = _review_integrity(review)
        write_semantic_review(path, review, overwrite=True)
    return requested
