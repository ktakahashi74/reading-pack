"""Shared Draft 2020-12 structural validation for Reading Pack artifacts.

The JSON files under ``schema/`` are the only structural definitions.  This
module loads them without translating their constraints into Python and turns
``Draft202012Validator.iter_errors()`` results into stable, path-aware
findings.  Callers retain semantic checks such as cross-record references,
hash freshness, URL policy, provenance binding, and state transitions.
"""

from __future__ import annotations

import json
import re
import sysconfig
from dataclasses import dataclass
from functools import lru_cache
from importlib import metadata
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError, best_match
from referencing import Registry, Resource

from .errors import ReadingPackError


SCHEMA_NAMES = frozenset(
    {
        "ai-review.schema.json",
        "author-input-manifest.schema.json",
        "author-input-module.schema.json",
        "author-input-plan.schema.json",
        "author-input-state.schema.json",
        "author-qa.schema.json",
        "author-review-manifest.schema.json",
        "author-review-plan.schema.json",
        "author-review-state.schema.json",
        "candidate-run.schema.json",
        "catalog-context-plan.schema.json",
        "catalog-context-responses.schema.json",
        "catalog-inventory.schema.json",
        "evidence-ref.schema.json",
        "generation-ledger.schema.json",
        "generation-response.schema.json",
        "generation-results.schema.json",
        "generation-session.schema.json",
        "import-plan.schema.json",
        "language-pack.schema.json",
        "manual-outline.schema.json",
        "project.schema.json",
        "provenance-receipt.schema.json",
        "qa-plan.schema.json",
        "quality-plan.schema.json",
        "semantic-findings.schema.json",
        "semantic-review.schema.json",
        "source-plan.schema.json",
        "source-registry.schema.json",
    }
)

_MISSING_PROPERTY = re.compile(r"^'([^']+)' is a required property$")
_QUOTED_PROPERTY = re.compile(r"'([^']+)'")


@dataclass(frozen=True)
class SchemaFinding:
    """One deterministic structural failure returned by a JSON Schema."""

    schema_name: str
    path: tuple[str | int, ...]
    keyword: str
    message: str
    schema_path: tuple[str | int, ...]

    def dotted_path(self, prefix: str = "") -> str:
        result = prefix
        for component in self.path:
            if isinstance(component, int):
                result += f"[{component}]"
            elif result:
                result += f".{component}"
            else:
                result = component
        return result


def _candidate_schema_directories() -> Iterator[Path]:
    repository = Path(__file__).resolve().parents[2] / "schema"
    yield repository
    try:
        distribution = metadata.distribution("reading-pack")
    except metadata.PackageNotFoundError:
        distribution = None
    if distribution is not None:
        yield Path(distribution.locate_file("share/reading-pack/schema")).resolve()
    data_root = Path(sysconfig.get_path("data"))
    yield data_root / "share" / "reading-pack" / "schema"


def _schema_directory(explicit: Path | None = None) -> Path:
    candidates = (explicit,) if explicit is not None else _candidate_schema_directories()
    for candidate in candidates:
        if candidate is not None and candidate.is_dir():
            found = {path.name for path in candidate.glob("*.schema.json")}
            if SCHEMA_NAMES <= found:
                return candidate
    searched = ", ".join(str(path) for path in candidates if path is not None)
    raise ReadingPackError(f"Reading Pack schema directory is unavailable: {searched}")


def _load_schema(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReadingPackError(f"cannot load structural schema {path.name}: {exc}") from exc
    if not isinstance(value, dict):
        raise ReadingPackError(f"structural schema {path.name} must be an object")
    try:
        Draft202012Validator.check_schema(value)
    except Exception as exc:
        raise ReadingPackError(f"invalid structural schema {path.name}: {exc}") from exc
    return value


def _selected_error(error: ValidationError) -> ValidationError:
    if not error.context:
        return error
    selected = best_match(error.context)
    return selected if selected is not None else error


def _normalized_error(error: ValidationError) -> tuple[tuple[str | int, ...], str]:
    path = tuple(error.absolute_path)
    message = error.message
    if error.validator == "required":
        match = _MISSING_PROPERTY.fullmatch(message)
        if match:
            return path + (match.group(1),), f"missing fields: {match.group(1)}"
    if error.validator == "additionalProperties":
        properties = sorted(_QUOTED_PROPERTY.findall(message))
        if properties:
            return path + (properties[0],), f"unexpected fields: {', '.join(properties)}"
    if error.validator == "type":
        expected = error.validator_value
        if isinstance(expected, list):
            rendered = " or ".join(str(item) for item in expected)
        else:
            rendered = str(expected)
        if rendered == "object":
            return path, "must be an object (non-object value)"
        return path, f"must be {rendered}"
    if error.validator == "minItems" and error.validator_value == 1:
        return path, "must be a non-empty array"
    if error.validator in {"minimum", "maximum"}:
        comparison = "at least" if error.validator == "minimum" else "at most"
        if path and path[-1] == "size_bytes":
            return path, f"source size is invalid; must be {comparison} {error.validator_value}"
        return path, f"must be {comparison} {error.validator_value}"
    if error.validator == "uniqueItems":
        if path and path[-1] == "reason_codes":
            return path, "reasons contain duplicates"
        return path, "contains duplicates"
    if error.validator == "const":
        return path, f"must equal {error.validator_value!r}"
    if error.validator == "enum":
        return path, f"must be one of {list(error.validator_value)!r}"
    if error.validator == "pattern":
        return path, f"does not match {error.validator_value}"
    if error.validator == "format":
        return path, f"must use {error.validator_value} format"
    return path, message


def _finding_sort_key(finding: SchemaFinding) -> tuple[Any, ...]:
    path = tuple((0, item) if isinstance(item, int) else (1, item) for item in finding.path)
    schema_path = tuple(str(item) for item in finding.schema_path)
    return path, finding.keyword, schema_path, finding.message


class SchemaValidator:
    """Load the installed schema set once and validate arbitrary instances."""

    def __init__(self, schema_directory: Path | None = None) -> None:
        self.schema_directory = _schema_directory(schema_directory)
        self.schemas = {
            name: _load_schema(self.schema_directory / name)
            for name in sorted(SCHEMA_NAMES)
        }
        resources: list[tuple[str, Resource[Any]]] = []
        for name, schema in self.schemas.items():
            resource = Resource.from_contents(schema)
            resources.append(((self.schema_directory / name).resolve().as_uri(), resource))
            resources.append((name, resource))
            schema_id = schema.get("$id")
            if isinstance(schema_id, str):
                resources.append((schema_id, resource))
        self.registry = Registry().with_resources(resources)
        self.validators = {
            name: Draft202012Validator(
                schema,
                registry=self.registry,
                format_checker=FormatChecker(),
            )
            for name, schema in self.schemas.items()
        }

    def iter_errors(self, schema_name: str, instance: Any) -> Iterable[SchemaFinding]:
        try:
            validator = self.validators[schema_name]
        except KeyError as exc:
            raise ReadingPackError(f"unknown structural schema: {schema_name}") from exc
        findings: list[SchemaFinding] = []
        for raw_error in validator.iter_errors(instance):
            error = _selected_error(raw_error)
            path, message = _normalized_error(error)
            findings.append(
                SchemaFinding(
                    schema_name=schema_name,
                    path=path,
                    keyword=str(error.validator),
                    message=message,
                    schema_path=tuple(error.absolute_schema_path),
                )
            )
        return tuple(sorted(findings, key=_finding_sort_key))

    def require_valid(
        self,
        schema_name: str,
        instance: Any,
        *,
        label: str,
        code: str | None = None,
    ) -> None:
        findings = tuple(self.iter_errors(schema_name, instance))
        if not findings:
            return
        first = findings[0]
        path = first.dotted_path(label)
        prefix = f"{code} " if code else ""
        suffix = "" if len(findings) == 1 else f" ({len(findings)} structural errors)"
        raise ReadingPackError(f"{prefix}{path}: {first.message}{suffix}")


@lru_cache(maxsize=1)
def schemas() -> SchemaValidator:
    return SchemaValidator()


def structural_findings(schema_name: str, instance: Any) -> tuple[SchemaFinding, ...]:
    return tuple(schemas().iter_errors(schema_name, instance))


def require_structure(
    schema_name: str,
    instance: Any,
    *,
    label: str,
    code: str | None = None,
) -> None:
    schemas().require_valid(schema_name, instance, label=label, code=code)


def schema_document(schema_name: str) -> dict[str, Any]:
    """Return a detached copy of one published schema for adapter requests."""

    try:
        value = schemas().schemas[schema_name]
    except KeyError as exc:
        raise ReadingPackError(f"unknown structural schema: {schema_name}") from exc
    return json.loads(json.dumps(value, ensure_ascii=False))


def rp_structural_code(finding: SchemaFinding) -> str:
    """Map project/language schema failures to the established RP codes."""

    path = finding.path
    if finding.schema_name == "project.schema.json":
        head = path[0] if path else None
        if head == "languages":
            if finding.keyword == "uniqueItems":
                return "RP003"
            if len(path) > 1 or finding.keyword == "enum":
                return "RP004"
            return "RP002"
        return {
            "level": "RP006",
            "pack_date": "RP007",
            "book": "RP008",
            "workflow": "RP009",
            "status": "RP010",
            "slug": "RP011",
        }.get(head, "RP001")
    if finding.schema_name == "language-pack.schema.json":
        head = path[0] if path else None
        if head == "schema_version":
            return "RP110"
        if head == "language":
            return "RP111"
        collections = {
            "chapters",
            "certainty",
            "claims",
            "misreadings",
            "policies",
            "names",
            "glossary",
            "references",
        }
        if head not in collections:
            return "RP112"
        if len(path) == 1:
            return "RP116" if head == "chapters" and finding.keyword == "minItems" else "RP114"
        if len(path) >= 2 and isinstance(path[1], int):
            if len(path) == 2:
                return "RP100"
            field = path[2]
            if finding.keyword == "required":
                return "RP101"
            if field == "id":
                return "RP102"
            if field == "status":
                return "RP103"
            if field in {"title", "label", "definition", "statement", "issue", "misreading", "response", "name", "term"}:
                return "RP104"
            if field in {"sections", "terms", "chapter_ids", "claim_ids", "source_locations", "aliases"}:
                return "RP105"
            if field == "layer":
                return "RP106"
            if field == "spoiler_scope":
                return "RP125"
            if field == "kind" and head == "misreadings":
                return "RP126"
            if field in {"provenance_source_id", "provenance_source_hash"} or finding.keyword == "dependentRequired":
                return "RP127"
            return "RP123"
        return "RP112"
    raise ReadingPackError(f"no RP structural mapping for {finding.schema_name}")


def qp_structural_code(finding: SchemaFinding) -> str:
    """Map quality-plan schema failures to the established QP codes."""

    if finding.schema_name != "quality-plan.schema.json":
        raise ReadingPackError(f"no QP structural mapping for {finding.schema_name}")
    path = finding.path
    head = path[0] if path else None
    if head == "format_version":
        return "QP003"
    if head == "profile":
        return "QP002"
    if head == "conformance_required":
        return "QP028"
    if head == "scope":
        return "QP004"
    if head == "authority":
        if len(path) == 1:
            return "QP005"
        field = path[1]
        if field == "reviewers" and finding.keyword == "uniqueItems":
            return "QP030"
        return {
            "type": "QP006",
            "reviewers": "QP007",
            "status": "QP009",
            "canonical_data_sha256": "QP048",
            "quality_contract_sha256": "QP048",
        }.get(field, "QP029")
    if head == "spoiler_policy":
        return "QP011"
    if head == "module_overrides":
        if "propertyNames" in finding.schema_path:
            return "QP014"
        if path and path[-1] == "reason":
            return "QP016"
        return "QP013" if len(path) == 1 else "QP015"
    if head == "critical_policies":
        if "propertyNames" in finding.schema_path:
            return "QP031"
        return "QP021" if len(path) == 1 else "QP022"
    if head == "acceptance":
        if len(path) < 2:
            return "QP024"
        if path[1] == "thresholds":
            if "content_floor" in path:
                return "QP051"
            if len(path) > 2 and path[2] in {"structure_precision", "structure_recall"}:
                return "QP025"
            if len(path) > 2 and path[2] in {"source_attribution_errors", "invented_records"}:
                return "QP026"
            return "QP033"
        if path[1] == "result":
            if len(path) > 2 and path[2] in {"structure_precision", "structure_recall"}:
                return "QP037"
            if len(path) > 2 and path[2] in {"source_attribution_errors", "invented_records"}:
                return "QP038"
            return "QP034" if len(path) == 2 else "QP035"
    return "QP027"
