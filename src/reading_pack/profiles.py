"""Genre-aware quality conformance plans for Reading Pack projects.

Profiles describe a minimum set of capabilities and human policies; they are
gates, not scores.  The module is deliberately independent from the core
validator so existing projects remain valid when ``quality-plan.json`` is
absent.  Callers may opt a project into profile conformance by creating that
file and setting ``conformance_required`` to true.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Mapping

from .errors import EXIT_IO, ReadingPackError
from .hashing import canonical_data_hash
from .schema_validation import qp_structural_code, structural_findings

QUALITY_PLAN_NAME = "quality-plan.json"
MODULES = frozenset(
    {
        "chapters", "certainty", "claims", "misreadings", "policies", "names",
        "glossary", "references",
    }
)
AUTHORITY_TYPES = frozenset(
    {"author", "contributor", "scholarly_editor", "rights_holder", "subject_editor"}
)
SPOILER_POLICIES = frozenset({"not_applicable", "spoiler_free", "spoilers_labeled", "full"})
OVERRIDE_STATES = frozenset({"required", "optional", "not_applicable"})
UNSAFE_PLAN_TEXT = re.compile(
    r"[\x00-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]"
)
MAX_EVALUATION_RECORD_BYTES = 8 * 1024 * 1024
MAX_QUALITY_PLAN_BYTES = 1024 * 1024
MAX_EVALUATION_FINDINGS = 10_000
HASH_PATTERN = re.compile(r"[0-9a-f]{64}")
SAFE_RELATIVE_EVIDENCE = re.compile(r"[A-Za-z0-9._/-]{1,500}")
CONTENT_FLOOR_METRICS = frozenset(
    {
        "chapter_summaries",
        "summary_characters",
        "chapter_terms",
        "certainty_levels",
        "claims",
        "claims_with_certainty",
        "claims_with_falsifiability",
        "claims_with_revision_conditions",
        "misreadings",
        "names",
        "names_with_context",
        "glossary_terms",
        "glossary_terms_with_meaning",
        "references",
        "content_characters",
    }
)
CHAPTER_KINDS = frozenset(
    {
        "frontmatter",
        "part",
        "chapter",
        "afterword",
        "appendix",
        "notes",
        "bibliography",
        "glossary",
        "index",
        "colophon",
        "unknown",
    }
)
GENRE_LIST_FIELDS = frozenset(
    {"sections", "terms", "contributors", "aliases", "learning_objectives"}
)
GENRE_STRING_FIELDS = frozenset({"title", "summary"})
SPOILER_SCOPES = frozenset({"none", "chapter_only", "labeled_spoilers", "full_book"})
FIELD_EXEMPT_KINDS = {
    "summary": frozenset({"bibliography", "index", "colophon"}),
    "learning_objectives": frozenset(
        {"frontmatter", "part", "afterword", "notes", "bibliography", "glossary", "index", "colophon"}
    ),
    "contributors": frozenset({"frontmatter", "part", "bibliography", "glossary", "index", "colophon"}),
    "aliases": frozenset({"frontmatter", "part", "afterword", "notes", "bibliography", "colophon"}),
    "spoiler_scope": frozenset({"frontmatter", "part", "bibliography", "index", "colophon"}),
}

POLICY_RUBRICS = {
    "source_fidelity": "Every published statement is traceable to the authorized source or explicitly labeled outside it.",
    "non_reconstruction": "The public bundle cannot substitute for or reconstruct the book's expressive sequence.",
    "rights": "The rights holder has cleared every public derived element and distribution term.",
    "claim_attribution": "Claims remain attributed to the correct author, contributor, chapter, and epistemic layer.",
    "epistemic_integrity": "Evidence, interpretation, uncertainty, and normative choice remain distinct.",
    "qualification_preservation": "Scope limits, exceptions, conditions, and caveats are not dropped.",
    "learning_coverage": "Declared learning objectives are covered and routed to their source locations.",
    "misconception_safety": "Known misconceptions are corrected without inventing answers or definitions.",
    "spoiler_control": "Every public record respects the declared spoiler boundary.",
    "interpretive_openness": "The pack does not collapse an open literary work into one authoritative interpretation.",
    "contributor_attribution": "Each contribution is attributed to its actual contributor.",
    "authority_coverage": "The recorded authority is entitled to review every included contribution or exceptions are resolved.",
    "inventory_completeness": "The declared reference inventory has no omitted entries within scope.",
    "alias_routing": "Aliases and cross-references route to the correct canonical entry.",
}


@dataclass(frozen=True)
class Profile:
    """A named, non-scoring quality contract."""

    name: str
    required_modules: frozenset[str]
    required_chapter_fields: frozenset[str]
    critical_policies: frozenset[str]
    default_spoiler_policy: str = "not_applicable"
    minimum_level: int = 1
    not_applicable_modules: frozenset[str] = frozenset()


@dataclass(frozen=True)
class QualityIssue:
    """A lightweight issue type compatible in shape with core validation issues."""

    severity: str
    code: str
    path: str
    message: str

    def format(self) -> str:
        return f"{self.severity.upper()} {self.code} {self.path}: {self.message}"


PROFILES: dict[str, Profile] = {
    "general-navigation": Profile(
        "general-navigation",
        frozenset({"chapters"}),
        # A chapter-only table of contents is still useful navigation. Requiring
        # section names would pressure editors to invent them for sources that
        # do not have an internal section hierarchy.
        frozenset({"title"}),
        frozenset({"source_fidelity", "non_reconstruction", "rights"}),
    ),
    "academic-argument": Profile(
        "academic-argument",
        frozenset({"chapters", "claims", "references"}),
        frozenset({"title", "summary"}),
        frozenset(
            {
                "source_fidelity",
                "claim_attribution",
                "epistemic_integrity",
                "non_reconstruction",
                "rights",
            }
        ),
        minimum_level=3,
    ),
    "nonfiction-reading": Profile(
        "nonfiction-reading",
        frozenset({"chapters", "claims", "references"}),
        frozenset({"title", "summary"}),
        frozenset(
            {"source_fidelity", "claim_attribution", "qualification_preservation", "non_reconstruction", "rights"}
        ),
        minimum_level=3,
        not_applicable_modules=frozenset({"references"}),
    ),
    "textbook-learning": Profile(
        "textbook-learning",
        frozenset({"chapters", "claims", "glossary", "references"}),
        frozenset({"title", "summary", "learning_objectives"}),
        frozenset(
            {"source_fidelity", "learning_coverage", "misconception_safety", "non_reconstruction", "rights"}
        ),
        minimum_level=3,
    ),
    "fiction-spoiler-free": Profile(
        "fiction-spoiler-free",
        frozenset({"chapters"}),
        frozenset({"title", "summary", "spoiler_scope"}),
        frozenset(
            {"source_fidelity", "spoiler_control", "interpretive_openness", "non_reconstruction", "rights"}
        ),
        "spoiler_free",
        2,
    ),
    "anthology-attribution": Profile(
        "anthology-attribution",
        frozenset({"chapters", "references"}),
        frozenset({"title", "summary", "contributors"}),
        frozenset(
            {"source_fidelity", "contributor_attribution", "authority_coverage", "non_reconstruction", "rights"}
        ),
        minimum_level=2,
    ),
    "reference-routing": Profile(
        "reference-routing",
        frozenset({"chapters", "glossary", "references"}),
        frozenset({"title", "aliases"}),
        frozenset(
            {"source_fidelity", "inventory_completeness", "alias_routing", "non_reconstruction", "rights"}
        ),
        minimum_level=2,
    ),
}


def create_default_quality_plan(profile: str, scope: str, authority_type: str) -> dict[str, Any]:
    """Return a canonical draft plan; no field implies human approval."""

    if profile not in PROFILES:
        raise ValueError(f"unknown quality profile: {profile}")
    if (
        not isinstance(scope, str)
        or not scope.strip()
        or len(scope) > 500
        or UNSAFE_PLAN_TEXT.search(scope)
    ):
        raise ValueError("scope must be one safe non-empty line of at most 500 characters")
    if authority_type not in AUTHORITY_TYPES:
        raise ValueError(f"unknown authority type: {authority_type}")
    selected = PROFILES[profile]
    return {
        "format_version": 1,
        "profile": profile,
        "conformance_required": True,
        "scope": scope.strip(),
        "authority": {
            "type": authority_type,
            "reviewers": [],
            "status": "pending",
            "canonical_data_sha256": "",
            "quality_contract_sha256": "",
        },
        "spoiler_policy": selected.default_spoiler_policy,
        "module_overrides": {},
        "critical_policies": {policy: "pending" for policy in sorted(selected.critical_policies)},
        "acceptance": {
            "thresholds": {
                "structure_precision": 1.0,
                "structure_recall": 1.0,
                "source_attribution_errors": 0,
                "invented_records": 0,
            },
            "result": {
                "status": "pending",
                "structure_precision": None,
                "structure_recall": None,
                "source_attribution_errors": None,
                "invented_records": None,
                "canonical_data_sha256": "",
                "evidence_record": "",
                "evidence_sha256": "",
                "reviewer": "",
            },
        },
    }


def quality_contract_hash(plan: Mapping[str, Any]) -> str:
    """Bind authority approval to the substantive, non-status quality contract."""

    policies = plan.get("critical_policies", {})
    profile_name = plan.get("profile")
    profile = PROFILES.get(profile_name) if isinstance(profile_name, str) else None
    payload = {
        "format_version": plan.get("format_version"),
        "profile": profile_name,
        "conformance_required": plan.get("conformance_required"),
        "scope": plan.get("scope"),
        "spoiler_policy": plan.get("spoiler_policy"),
        "module_overrides": plan.get("module_overrides"),
        "profile_contract": (
            {
                "minimum_level": profile.minimum_level,
                "required_modules": sorted(profile.required_modules),
                "not_applicable_modules": sorted(profile.not_applicable_modules),
                "required_chapter_fields": sorted(profile.required_chapter_fields),
                "critical_policies": {
                    name: POLICY_RUBRICS[name]
                    for name in sorted(profile.critical_policies)
                },
            }
            if profile is not None
            else None
        ),
        "critical_policy_names": (
            sorted(str(name) for name in policies) if isinstance(policies, dict) else []
        ),
        "acceptance_thresholds": (
            plan.get("acceptance", {}).get("thresholds")
            if isinstance(plan.get("acceptance"), dict)
            else None
        ),
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate object key: {key}")
        value[key] = item
    return value


def _reject_nonfinite_json(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def _strict_json_bytes(payload: bytes, source: Path) -> Any:
    try:
        text = payload.decode("utf-8")
        return json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite_json,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise ReadingPackError(f"cannot read {source}: {exc}", EXIT_IO) from exc


def load_quality_plan(project: Path) -> dict[str, Any] | None:
    """Load ``quality-plan.json`` or return ``None`` for a legacy project."""

    path = project / QUALITY_PLAN_NAME
    if not path.exists():
        return None
    try:
        with path.open("rb") as handle:
            payload = handle.read(MAX_QUALITY_PLAN_BYTES + 1)
    except OSError as exc:
        raise ReadingPackError(f"cannot read {path}: {exc}", EXIT_IO) from exc
    if len(payload) > MAX_QUALITY_PLAN_BYTES:
        raise ReadingPackError(
            f"cannot read {path}: exceeds {MAX_QUALITY_PLAN_BYTES} bytes",
            EXIT_IO,
        )
    value = _strict_json_bytes(payload, path)
    if not isinstance(value, dict):
        raise ReadingPackError(f"cannot read {path}: top level must be an object", EXIT_IO)
    return value


def _issue(
    issues: list[QualityIssue],
    code: str,
    path: str,
    message: str,
    *,
    release: bool,
    always_error: bool = False,
) -> None:
    issues.append(QualityIssue("error" if always_error or release else "warning", code, path, message))


def _nonempty_records(data_by_lang: Mapping[str, Mapping[str, Any]], module: str) -> bool:
    return bool(data_by_lang) and all(
        isinstance(data, Mapping)
        and isinstance(data.get(module), list)
        and bool(data.get(module))
        for data in data_by_lang.values()
    )


def _safe_line(value: Any, *, maximum: int = 500) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and len(value) <= maximum
        and UNSAFE_PLAN_TEXT.search(value) is None
    )


def _valid_hash(value: Any) -> bool:
    return isinstance(value, str) and HASH_PATTERN.fullmatch(value) is not None


def _valid_number(value: Any) -> bool:
    return type(value) in {int, float} and math.isfinite(value)


def content_metrics(
    data_by_lang: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, int]]:
    """Measure canonical information coverage without assigning a quality score.

    The counts are deliberately simple and reproducible.  They can enforce a
    declared no-regression floor, but do not replace source or semantic review.
    """

    result: dict[str, dict[str, int]] = {}
    for language, data in sorted(data_by_lang.items()):
        metrics = {key: 0 for key in CONTENT_FLOOR_METRICS}
        if not isinstance(data, Mapping):
            result[language] = metrics
            continue

        content_strings: list[str] = []
        chapters = data.get("chapters")
        if isinstance(chapters, list):
            for chapter in chapters:
                if not isinstance(chapter, Mapping):
                    continue
                summary = chapter.get("summary")
                if isinstance(summary, str) and summary.strip():
                    metrics["chapter_summaries"] += 1
                    metrics["summary_characters"] += len(summary)
                    content_strings.append(summary)
                terms = chapter.get("terms")
                if isinstance(terms, list):
                    valid_terms = [
                        item for item in terms if isinstance(item, str) and item.strip()
                    ]
                    metrics["chapter_terms"] += len(valid_terms)
                    content_strings.extend(valid_terms)

        certainty = data.get("certainty")
        if isinstance(certainty, list):
            metrics["certainty_levels"] = sum(
                1 for item in certainty if isinstance(item, Mapping)
            )

        claims = data.get("claims")
        if isinstance(claims, list):
            for claim in claims:
                if not isinstance(claim, Mapping):
                    continue
                metrics["claims"] += 1
                for field, metric in (
                    ("certainty_id", "claims_with_certainty"),
                    ("falsifiability", "claims_with_falsifiability"),
                    ("revision_conditions", "claims_with_revision_conditions"),
                ):
                    value = claim.get(field)
                    if isinstance(value, str) and value.strip():
                        metrics[metric] += 1
                content_strings.extend(
                    value
                    for field in (
                        "statement",
                        "falsifiability",
                        "revision_conditions",
                    )
                    if isinstance((value := claim.get(field)), str) and value.strip()
                )

        misreadings = data.get("misreadings")
        if isinstance(misreadings, list):
            for item in misreadings:
                if not isinstance(item, Mapping):
                    continue
                metrics["misreadings"] += 1
                content_strings.extend(
                    value
                    for field in (
                        "issue",
                        "misreading",
                        "response",
                        "impact",
                        "remaining_uncertainty",
                    )
                    if isinstance((value := item.get(field)), str) and value.strip()
                )

        names = data.get("names")
        if isinstance(names, list):
            for item in names:
                if not isinstance(item, Mapping):
                    continue
                metrics["names"] += 1
                name = item.get("name")
                context = item.get("book_context")
                if isinstance(name, str) and name.strip():
                    content_strings.append(name)
                if isinstance(context, str) and context.strip():
                    metrics["names_with_context"] += 1
                    content_strings.append(context)

        glossary = data.get("glossary")
        if isinstance(glossary, list):
            for item in glossary:
                if not isinstance(item, Mapping):
                    continue
                metrics["glossary_terms"] += 1
                term = item.get("term")
                meaning = item.get("book_meaning")
                if isinstance(term, str) and term.strip():
                    content_strings.append(term)
                if isinstance(meaning, str) and meaning.strip():
                    metrics["glossary_terms_with_meaning"] += 1
                    content_strings.append(meaning)

        references = data.get("references")
        if isinstance(references, list):
            for item in references:
                if not isinstance(item, Mapping):
                    continue
                metrics["references"] += 1
                label = item.get("label")
                if isinstance(label, str) and label.strip():
                    content_strings.append(label)

        metrics["content_characters"] = sum(len(item) for item in content_strings)
        result[language] = metrics
    return result


def _evidence_path(project: Path, value: Any) -> Path | None:
    if (
        not isinstance(value, str)
        or SAFE_RELATIVE_EVIDENCE.fullmatch(value) is None
        or value.startswith("/")
        or "//" in value
        or any(part in {"", ".", ".."} for part in value.split("/"))
        or not value.endswith(".json")
    ):
        return None
    try:
        path = (project / value).resolve()
        path.relative_to(project.resolve())
    except (OSError, RuntimeError, ValueError):
        return None
    return path


def _read_evaluation_record(path: Path) -> tuple[bytes, dict[str, Any]]:
    try:
        with path.open("rb") as handle:
            payload = handle.read(MAX_EVALUATION_RECORD_BYTES + 1)
    except OSError as exc:
        raise ReadingPackError(f"cannot read {path}: {exc}", EXIT_IO) from exc
    if len(payload) > MAX_EVALUATION_RECORD_BYTES:
        raise ReadingPackError(
            f"cannot read {path}: exceeds {MAX_EVALUATION_RECORD_BYTES} bytes",
            EXIT_IO,
        )
    value = _strict_json_bytes(payload, path)
    if not isinstance(value, dict):
        raise ReadingPackError(f"cannot read {path}: top level must be an object", EXIT_IO)
    return payload, value


def _validate_evaluation_evidence(
    issues: list[QualityIssue],
    *,
    project: Path,
    profile: Profile,
    plan: Mapping[str, Any],
    result: Mapping[str, Any],
    data_by_lang: Mapping[str, Mapping[str, Any]],
    expected_data_hash: str,
    strict: bool,
) -> None:
    evidence_name = result.get("evidence_record")
    path = _evidence_path(project, evidence_name)
    if path is None:
        _issue(
            issues,
            "QP044",
            f"{QUALITY_PLAN_NAME}.acceptance.result.evidence_record",
            "must name a safe project-relative JSON file",
            release=strict,
            always_error=True,
        )
        return
    try:
        payload, evidence = _read_evaluation_record(path)
    except ReadingPackError as exc:
        _issue(
            issues,
            "QP045",
            str(evidence_name),
            f"cannot verify evaluation evidence: {exc}",
            release=strict,
        )
        return

    actual_hash = hashlib.sha256(payload).hexdigest()
    if result.get("evidence_sha256") != actual_hash:
        _issue(
            issues,
            "QP046",
            f"{QUALITY_PLAN_NAME}.acceptance.result.evidence_sha256",
            "evaluation evidence hash is absent or stale",
            release=strict,
        )

    evidence_fields = {
        "format_version",
        "kind",
        "status",
        "profile",
        "canonical_data_sha256",
        "quality_contract_sha256",
        "method",
        "reviewer",
        "reviewed_at",
        "counts",
    }
    if set(evidence) != evidence_fields:
        _issue(
            issues,
            "QP049",
            str(evidence_name),
            "evaluation record has missing or unexpected fields",
            release=strict,
            always_error=True,
        )
    if evidence.get("format_version") != 1 or evidence.get("kind") != "reading-pack-quality-evaluation":
        _issue(
            issues,
            "QP049",
            str(evidence_name),
            "evaluation record must use format version 1 and the Reading Pack evaluation kind",
            release=strict,
            always_error=True,
        )
    if evidence.get("status") != "approved":
        _issue(
            issues,
            "QP050",
            f"{evidence_name}.status",
            "evaluation evidence must record explicit approval",
            release=strict,
        )
    if evidence.get("profile") != profile.name:
        _issue(
            issues,
            "QP050",
            f"{evidence_name}.profile",
            "evaluation evidence is for a different profile",
            release=strict,
        )
    if evidence.get("canonical_data_sha256") != expected_data_hash:
        _issue(
            issues,
            "QP050",
            f"{evidence_name}.canonical_data_sha256",
            "evaluation evidence is stale for current canonical data",
            release=strict,
        )
    if evidence.get("quality_contract_sha256") != quality_contract_hash(plan):
        _issue(
            issues,
            "QP050",
            f"{evidence_name}.quality_contract_sha256",
            "evaluation evidence is stale for the current quality contract",
            release=strict,
        )
    for field, maximum in (("method", 1_000), ("reviewer", 500)):
        if not _safe_line(evidence.get(field), maximum=maximum):
            _issue(
                issues,
                "QP049",
                f"{evidence_name}.{field}",
                "must be one safe non-empty line",
                release=strict,
                always_error=True,
            )
    if evidence.get("reviewer") != result.get("reviewer"):
        _issue(
            issues,
            "QP050",
            f"{evidence_name}.reviewer",
            "evaluation reviewer does not match the accepted result",
            release=strict,
        )
    reviewed_at = evidence.get("reviewed_at")
    try:
        if not isinstance(reviewed_at, str):
            raise ValueError
        date.fromisoformat(reviewed_at)
    except ValueError:
        _issue(
            issues,
            "QP049",
            f"{evidence_name}.reviewed_at",
            "must be a valid YYYY-MM-DD date",
            release=strict,
            always_error=True,
        )

    counts = evidence.get("counts")
    count_fields = {
        "expected_structure_records",
        "observed_structure_records",
        "matched_structure_records",
        "source_attribution_errors",
        "invented_record_ids",
    }
    if not isinstance(counts, dict) or set(counts) != count_fields:
        _issue(
            issues,
            "QP049",
            f"{evidence_name}.counts",
            "must contain the exact auditable count fields",
            release=strict,
            always_error=True,
        )
        return
    expected_count = counts.get("expected_structure_records")
    observed_count = counts.get("observed_structure_records")
    matched_count = counts.get("matched_structure_records")
    if (
        type(expected_count) is not int
        or type(observed_count) is not int
        or type(matched_count) is not int
        or expected_count <= 0
        or observed_count <= 0
        or matched_count < 0
        or matched_count > expected_count
        or matched_count > observed_count
    ):
        _issue(
            issues,
            "QP049",
            f"{evidence_name}.counts",
            "structure counts must be positive, internally consistent integers",
            release=strict,
            always_error=True,
        )
        return
    canonical_structure_count = 0
    for data in data_by_lang.values():
        if not isinstance(data, Mapping):
            continue
        chapters = data.get("chapters")
        if not isinstance(chapters, list):
            continue
        canonical_structure_count += len(chapters)
        for chapter in chapters:
            if isinstance(chapter, Mapping) and isinstance(
                chapter.get("sections"), list
            ):
                canonical_structure_count += len(chapter["sections"])
    if observed_count != canonical_structure_count:
        _issue(
            issues,
            "QP050",
            f"{evidence_name}.counts.observed_structure_records",
            "does not equal the current canonical chapter-and-section inventory",
            release=strict,
        )
    attribution_errors = counts.get("source_attribution_errors")
    invented_ids = counts.get("invented_record_ids")
    for field, value in (
        ("source_attribution_errors", attribution_errors),
        ("invented_record_ids", invented_ids),
    ):
        if (
            not isinstance(value, list)
            or len(value) > MAX_EVALUATION_FINDINGS
            or not all(_safe_line(item, maximum=2_000) for item in value)
        ):
            _issue(
                issues,
                "QP049",
                f"{evidence_name}.counts.{field}",
                "must be a bounded array of safe, non-empty findings",
                release=strict,
                always_error=True,
            )
            return

    derived = {
        "structure_precision": matched_count / observed_count,
        "structure_recall": matched_count / expected_count,
        "source_attribution_errors": len(attribution_errors),
        "invented_records": len(invented_ids),
    }
    for metric, expected_value in derived.items():
        reported = result.get(metric)
        matches = (
            _valid_number(reported)
            and math.isclose(reported, expected_value, rel_tol=0.0, abs_tol=1e-12)
            if metric in {"structure_precision", "structure_recall"}
            else type(reported) is int and reported == expected_value
        )
        if not matches:
            _issue(
                issues,
                "QP050",
                f"{QUALITY_PLAN_NAME}.acceptance.result.{metric}",
                "accepted result does not match the values derived from evaluation evidence",
                release=strict,
            )


def validate_quality_plan(
    project: Path,
    data_by_lang: Mapping[str, Mapping[str, Any]],
    release: bool = False,
    project_level: int | None = None,
    *,
    plan_override: Mapping[str, Any] | None = None,
) -> list[QualityIssue]:
    """Validate profile conformance without assigning a quality score.

    Draft validation reports incomplete human work as warnings. In release
    mode an opted-in plan converts conformance failures to errors. A release
    always requires an explicit plan; draft validation keeps legacy projects
    readable by reporting the absence as a warning.
    """

    plan = (
        dict(plan_override)
        if plan_override is not None
        else load_quality_plan(project)
    )
    if plan is None:
        return [
            QualityIssue(
                "error" if release else "warning",
                "QP001",
                QUALITY_PLAN_NAME,
                "quality plan is absent; release requires an explicit profile contract",
            )
        ]

    conformance = plan.get("conformance_required") is True
    strict = release and conformance
    issues: list[QualityIssue] = []
    schema_errors = structural_findings("quality-plan.schema.json", plan)
    for finding in schema_errors:
        _issue(
            issues,
            qp_structural_code(finding),
            finding.dotted_path(QUALITY_PLAN_NAME),
            finding.message,
            release=strict,
            always_error=True,
        )
    profile_name = plan.get("profile")
    profile = PROFILES.get(profile_name) if isinstance(profile_name, str) else None
    if profile is None:
        return issues

    if project_level is not None and (
        type(project_level) is not int or project_level < profile.minimum_level
    ):
        _issue(
            issues,
            "QP041",
            "reading-pack.toml.level",
            f"profile {profile.name} requires level {profile.minimum_level} or higher",
            release=strict,
        )

    authority = plan.get("authority")
    if not isinstance(authority, dict):
        authority = {}
    else:
        reviewers = authority.get("reviewers")
        if isinstance(reviewers, list) and not reviewers:
            _issue(issues, "QP008", f"{QUALITY_PLAN_NAME}.authority.reviewers", "conformance requires at least one named reviewer", release=strict)
        authority_status = authority.get("status")
        if authority_status in {"pending", "reviewed"}:
            _issue(issues, "QP010", f"{QUALITY_PLAN_NAME}.authority.status", "conformance requires approved", release=strict)
        expected_authority_hash = canonical_data_hash(dict(data_by_lang))
        if authority.get("canonical_data_sha256") != expected_authority_hash:
            _issue(
                issues,
                "QP042",
                f"{QUALITY_PLAN_NAME}.authority.canonical_data_sha256",
                "authority approval is absent or stale for current canonical data",
                release=strict,
            )
        if authority.get("quality_contract_sha256") != quality_contract_hash(plan):
            _issue(
                issues,
                "QP043",
                f"{QUALITY_PLAN_NAME}.authority.quality_contract_sha256",
                "authority approval is absent or stale for the current quality contract",
                release=strict,
            )

    spoiler_policy = plan.get("spoiler_policy")
    if (
        isinstance(spoiler_policy, str)
        and spoiler_policy in SPOILER_POLICIES
        and profile.name == "fiction-spoiler-free"
        and spoiler_policy != "spoiler_free"
    ):
        _issue(issues, "QP012", f"{QUALITY_PLAN_NAME}.spoiler_policy", "fiction-spoiler-free requires spoiler_free", release=strict)

    overrides = plan.get("module_overrides", {})
    if not isinstance(overrides, dict):
        overrides = {}
    effective_required = set(profile.required_modules)
    for module, override in overrides.items():
        path = f"{QUALITY_PLAN_NAME}.module_overrides.{module}"
        if module not in MODULES:
            continue
        override_status = override.get("status") if isinstance(override, dict) else None
        if (
            not isinstance(override, dict)
            or not isinstance(override_status, str)
            or override_status not in OVERRIDE_STATES
        ):
            continue
        state = override_status
        reason = override.get("reason")
        if state == "not_applicable":
            if (
                module in profile.required_modules
                and module not in profile.not_applicable_modules
            ):
                _issue(issues, "QP017", path, f"{module} is mandatory for profile {profile.name}", release=strict)
            effective_required.discard(module)
        elif state == "required":
            effective_required.add(module)
        elif module in profile.required_modules:
            _issue(issues, "QP018", path, f"mandatory module {module} cannot be made optional", release=strict)

    for module in sorted(effective_required):
        if not _nonempty_records(data_by_lang, module):
            _issue(issues, "QP019", f"data.*.{module}", f"profile {profile.name} requires non-empty {module} in every language", release=strict)

    for lang, data in data_by_lang.items():
        if not isinstance(data, Mapping):
            _issue(
                issues,
                "QP048",
                f"data/pack.{lang}.json",
                "language data must be an object",
                release=strict,
                always_error=True,
            )
            continue
        chapters = data.get("chapters", [])
        if not isinstance(chapters, list):
            _issue(
                issues,
                "QP048",
                f"data/pack.{lang}.json.chapters",
                "chapters must be an array",
                release=strict,
                always_error=True,
            )
            continue
        for index, chapter in enumerate(chapters):
            if not isinstance(chapter, dict):
                continue
            chapter_path = f"data/pack.{lang}.json.chapters[{index}]"
            kind = chapter.get("kind")
            if kind is not None and (
                not isinstance(kind, str) or kind not in CHAPTER_KINDS
            ):
                _issue(
                    issues,
                    "QP048",
                    f"{chapter_path}.kind",
                    "must name a supported structural kind",
                    release=strict,
                    always_error=True,
                )
                kind = None
            for field in sorted(profile.required_chapter_fields):
                value = chapter.get(field)
                if kind in FIELD_EXEMPT_KINDS.get(field, frozenset()):
                    continue
                if field in GENRE_STRING_FIELDS:
                    valid = _safe_line(value, maximum=500)
                elif field in GENRE_LIST_FIELDS:
                    valid = (
                        field in chapter
                        and isinstance(value, list)
                        and bool(value)
                        and len(value) <= 1_000
                        and all(_safe_line(item, maximum=500) for item in value)
                    )
                    if valid and field in {"contributors", "aliases"}:
                        valid = len(value) == len(set(value))
                elif field == "spoiler_scope":
                    valid = isinstance(value, str) and value in SPOILER_SCOPES
                    if valid and profile.name == "fiction-spoiler-free":
                        valid = value == "none"
                else:
                    valid = value is not None and value != "" and value != []
                if not valid:
                    _issue(
                        issues,
                        "QP020",
                        f"{chapter_path}.{field}",
                        f"profile {profile.name} requires a valid {field}",
                        release=strict,
                    )

    policy_states = plan.get("critical_policies")
    if not isinstance(policy_states, dict):
        policy_states = {}
    unexpected_policies = set(policy_states) - set(profile.critical_policies)
    if unexpected_policies:
        _issue(
            issues,
            "QP031",
            f"{QUALITY_PLAN_NAME}.critical_policies",
            f"unexpected policies for profile {profile.name}: {sorted(unexpected_policies)}",
            release=strict,
            always_error=True,
        )
    for policy in sorted(profile.critical_policies):
        state = policy_states.get(policy)
        if isinstance(state, str) and state in {"pending", "reviewed"}:
            _issue(issues, "QP023", f"{QUALITY_PLAN_NAME}.critical_policies.{policy}", "conformance requires approved", release=strict)

    acceptance = plan.get("acceptance")
    if not isinstance(acceptance, dict):
        acceptance = {}
    else:
        thresholds = acceptance.get("thresholds")
        result = acceptance.get("result")
        if not isinstance(thresholds, dict):
            thresholds = {}
        content_floor = thresholds.get("content_floor")
        if content_floor is not None:
            floor_path = f"{QUALITY_PLAN_NAME}.acceptance.thresholds.content_floor"
            floor_prefix = ("acceptance", "thresholds", "content_floor")
            valid_floor = isinstance(content_floor, dict) and not any(
                finding.path[:3] == floor_prefix for finding in schema_errors
            )
            floor_languages = content_floor.get("languages") if valid_floor else None
            observed_metrics = content_metrics(data_by_lang)
            if valid_floor and isinstance(floor_languages, dict):
                for language, minimums in floor_languages.items():
                    language_path = f"{floor_path}.languages.{language}"
                    if language not in data_by_lang:
                        _issue(
                            issues,
                            "QP051",
                            language_path,
                            "must use a project language and supported non-negative integer metrics",
                            release=strict,
                            always_error=True,
                        )
                        continue
                    observed = observed_metrics[language]
                    for metric, minimum in sorted(minimums.items()):
                        if observed[metric] < minimum:
                            _issue(
                                issues,
                                "QP052",
                                f"data/pack.{language}.json.{metric}",
                                (
                                    f"observed {observed[metric]} is below the declared "
                                    f"content floor {minimum} from {content_floor['source_label']}"
                                ),
                                release=strict,
                            )
        if not isinstance(result, dict):
            result = {}
        else:
            result_status = result.get("status")
            if result_status in {"pending", "reviewed"}:
                _issue(issues, "QP036", f"{QUALITY_PLAN_NAME}.acceptance.result.status", "release conformance requires an approved measured evaluation", release=strict)
            measured = {
                "structure_precision": result.get("structure_precision"),
                "structure_recall": result.get("structure_recall"),
                "source_attribution_errors": result.get("source_attribution_errors"),
                "invented_records": result.get("invented_records"),
            }
            for metric in ("structure_precision", "structure_recall"):
                value = measured[metric]
                threshold = thresholds.get(metric)
                if (
                    _valid_number(value)
                    and _valid_number(threshold)
                    and value < threshold
                ):
                    _issue(issues, "QP037", f"{QUALITY_PLAN_NAME}.acceptance.result.{metric}", "measured result does not meet the threshold", release=strict)
            for metric in ("source_attribution_errors", "invented_records"):
                value = measured[metric]
                threshold = thresholds.get(metric)
                if (
                    type(value) is int
                    and type(threshold) is int
                    and value > threshold
                ):
                    _issue(issues, "QP038", f"{QUALITY_PLAN_NAME}.acceptance.result.{metric}", "measured result does not meet the threshold", release=strict)
            expected_hash = canonical_data_hash(dict(data_by_lang))
            if result.get("canonical_data_sha256") != expected_hash:
                _issue(issues, "QP039", f"{QUALITY_PLAN_NAME}.acceptance.result.canonical_data_sha256", "measured evaluation is stale for current canonical data", release=strict)
            for field in ("evidence_record", "reviewer"):
                value = result.get(field)
                if not _safe_line(value, maximum=500):
                    _issue(issues, "QP040", f"{QUALITY_PLAN_NAME}.acceptance.result.{field}", "must be one safe non-empty line", release=strict)
            evidence_record = result.get("evidence_record")
            evidence_sha256 = result.get("evidence_sha256")
            if evidence_sha256 != "" and not _valid_hash(evidence_sha256):
                _issue(issues, "QP047", f"{QUALITY_PLAN_NAME}.acceptance.result.evidence_sha256", "must be a SHA-256 hash", release=strict)
            elif evidence_sha256 == "":
                _issue(issues, "QP047", f"{QUALITY_PLAN_NAME}.acceptance.result.evidence_sha256", "must be a SHA-256 hash", release=strict)
            if isinstance(evidence_record, str) and evidence_record.strip():
                _validate_evaluation_evidence(
                    issues,
                    project=project,
                    profile=profile,
                    plan=plan,
                    result=result,
                    data_by_lang=data_by_lang,
                    expected_data_hash=expected_hash,
                    strict=strict,
                )
    return issues
