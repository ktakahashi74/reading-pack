"""One-stop private human review for heterogeneous candidate artifacts.

The bundle is a read-only view over one or more integrity-bound candidate
runs.  It re-verifies every source and evidence span at render time, checks
that every run still targets the current canonical snapshot, and writes only
below ``.reading-pack/reviews`` with owner-only permissions.

The output deliberately has no accept-all (or any other mutation) action.  A
reviewer may use it to consider chapters, summaries, claims, people, terms,
references, and author Q&A together, but decisions remain candidate-specific
and must be recorded through the separate decision workflow.
"""

from __future__ import annotations

import hashlib
import html
import json
import stat
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .candidates import (
    _value_hash,
    _verify_source_and_evidence,
    load_candidate_run,
    normalize_text,
)
from .catalog_extraction import load_catalog_inventory
from reading_pack.errors import EXIT_IO, ReadingPackError
from .private_review import (
    DEFAULT_CONTEXT_CHARACTERS,
    MAX_CONTEXT_CHARACTERS,
    MIN_CONTEXT_CHARACTERS,
    _canonical_records,
    _context_html,
    _ensure_private_review_directory,
    _write_private_html,
)
from reading_pack.project import CONFIG_NAME, load_config, load_language_data
from .work_ledger import coverage_report, load_semantic_review, load_work_ledger


BUNDLE_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class ReviewBundleArtifact:
    """Paths required to review one candidate run in the bundle.

    ``work_ledger_path``, ``semantic_review_path``, and
    ``catalog_inventory_path`` are optional, but a semantic review requires its
    reconciled work ledger so the complete chain of run, coverage, and semantic
    bindings can be checked.  A catalog inventory must be generator-bound to
    this exact run and source.
    """

    run: Path
    source_path: Path
    work_ledger_path: Path | None = None
    semantic_review_path: Path | None = None
    catalog_inventory_path: Path | None = None


@dataclass(frozen=True)
class _LoadedArtifact:
    manifest: Mapping[str, Any]
    normalized_source: str
    coverage: Mapping[str, Any] | None
    semantic_findings: Mapping[str, tuple[Mapping[str, Any], ...]]
    ledger_integrity_sha256: str | None
    semantic_integrity_sha256: str | None
    catalog_inventory: Mapping[str, Any] | None


_SECTION_SPECS = (
    ("chapters", "chapters"),
    ("summaries", "chapters"),
    ("claims", "claims"),
    ("certainty", "certainty"),
    ("people", "names"),
    ("terms", "glossary"),
    ("references", "references"),
    ("author_qa", "misreadings"),
    ("policies", "policies"),
)

_PROJECTED_FIELDS = {
    "chapters": (
        "id",
        "kind",
        "title",
        "pages",
        "sections",
        "terms",
        "contributors",
        "aliases",
        "learning_objectives",
        "prerequisites",
        "spoiler_scope",
        "source_locations",
        "status",
    ),
    "summaries": ("id", "title", "summary", "status"),
    "claims": (
        "id",
        "layer",
        "kind",
        "statement",
        "chapter_ids",
        "certainty_id",
        "falsifiability",
        "revision_conditions",
        "source_locations",
        "status",
    ),
    "certainty": ("id", "label", "definition", "source_locations", "status"),
    "people": ("id", "name", "chapter_id", "source_locations", "status"),
    "terms": ("id", "term", "chapter_id", "source_locations", "status"),
    "references": ("id", "label", "url", "source_locations", "status"),
    "author_qa": (
        "id",
        "kind",
        "issue",
        "misreading",
        "impact",
        "response",
        "remaining_uncertainty",
        "chapter_ids",
        "claim_ids",
        "source_locations",
        "status",
    ),
    "policies": ("id", "kind", "statement", "source_locations", "status"),
}

_LABELS = {
    "en": {
        "title": "One-stop private human review",
        "private": (
            "Private review material. This file contains dynamically resolved "
            "source excerpts. Keep it under .reading-pack and do not publish it."
        ),
        "boundary": (
            "Read-only review boundary: generating or reading this bundle does not "
            "accept, apply, approve, or publish any candidate. A bundle-level review "
            "is never blanket approval; every decision remains candidate-specific."
        ),
        "integrity": (
            "Every run, source hash, normalized evidence span, optional coverage "
            "ledger, optional semantic review, optional catalog inventory, and "
            "canonical snapshot was verified again while rendering this file."
        ),
        "bundle": "Bundle identity",
        "language": "Language",
        "runs": "Candidate runs",
        "sources": "Sources",
        "unique_candidates": "Unique candidates",
        "catalog_report": "Catalog extraction report",
        "catalogs": "Bound catalog inventories",
        "catalog_caveat": (
            "Heuristic seed report: these counts are extraction candidates, not a "
            "complete or approved index. A zero count does not prove absence. Review "
            "chapter mapping and each candidate independently."
        ),
        "catalog_inventory": "Inventory",
        "catalog_extractor": "Extractor",
        "catalog_map_method": "Chapter-map method",
        "catalog_map_review": "Chapter-map review required",
        "catalog_resolved_chapters": "Resolved chapters",
        "catalog_people": "People candidates",
        "catalog_terms": "Term candidates",
        "catalog_references": "Reference candidates",
        "catalog_unresolved_people": "Unresolved people signals",
        "catalog_unresolved_terms": "Unresolved term signals",
        "catalog_confidence_counts": "Heuristic confidence signals",
        "catalog_discovery": "Catalog discovery source",
        "catalog_seed": "heuristic seed",
        "catalog_generated": "model/NER recall addition",
        "catalog_confidence": "heuristic confidence",
        "catalog_reasons": "heuristic reason codes",
        "catalog_inferred_warning": (
            "The title-sequence chapter map is inferred and requires human review; "
            "it must not be treated as an approved chapter assignment."
        ),
        "catalog_explicit_note": (
            "The chapter map is explicitly source-bound, but candidate inclusion "
            "still requires independent human review."
        ),
        "navigation": "Review sections",
        "section_status": "Section status",
        "candidate_counts": "Candidate states",
        "evidence_count": "Evidence references",
        "source_count": "Bound sources",
        "coverage": "Generation coverage",
        "semantic": "Semantic review",
        "candidate": "Candidate",
        "run": "run",
        "record": "record",
        "state": "state",
        "source": "source",
        "changed": "changed fields",
        "proposed": "Proposed review data",
        "canonical": "Current canonical data",
        "evidence": "Evidence and reference metadata",
        "semantic_findings": "Semantic findings",
        "scope": "Chapter scope",
        "book_scope": "Book or unscoped",
        "no_candidates": (
            "No candidate artifact was supplied for this section. This means not "
            "generated or not included; it does not prove the source has no such content."
        ),
        "no_canonical": "No matching canonical record.",
        "no_evidence": "No verified evidence reference is available.",
        "no_findings": "No bound semantic finding was supplied.",
        "status_not_available": "not_available",
        "status_incomplete": "incomplete",
        "status_blocked": "blocked",
        "status_review": "review_required",
        "status_decided": "decision_recorded_not_approved",
        "status_accounted_empty": "accounted_no_candidate",
        "sections": {
            "chapters": "Chapters and structure",
            "summaries": "Chapter summaries",
            "claims": "Claims",
            "certainty": "Certainty definitions",
            "people": "People index",
            "terms": "Term index",
            "references": "References",
            "author_qa": "Author Q&A and reading issues",
            "policies": "Book-specific policies",
        },
    },
    "ja": {
        "title": "ワンストップ非公開・人間レビュー",
        "private": (
            "非公開レビュー資料です。このfileには動的に再解決したsource抜粋が含まれます。"
            ".reading-pack内に保ち、公開しないでください。"
        ),
        "boundary": (
            "読み取り専用の境界：このbundleの生成・閲覧はcandidateのaccept、apply、承認、公開を"
            "一切行いません。bundle全体の確認を一括承認とはみなさず、判断は必ずcandidate単位で"
            "別workflowに記録します。"
        ),
        "integrity": (
            "生成時に全run、source hash、正規化evidence span、任意のcoverage ledger、任意の"
            "意味的レビュー、任意のcatalog inventory、および正本snapshotを再検証しました。"
        ),
        "bundle": "bundle識別情報",
        "language": "言語",
        "runs": "candidate run",
        "sources": "source",
        "unique_candidates": "重複を除くcandidate",
        "catalog_report": "catalog抽出レポート",
        "catalogs": "拘束catalog inventory",
        "catalog_caveat": (
            "heuristic seedのレポートです。件数は抽出候補であり、完全な索引でも承認済み索引でも"
            "ありません。0件は不在の証明ではありません。章割当と各candidateを個別に確認してください。"
        ),
        "catalog_inventory": "inventory",
        "catalog_extractor": "抽出器",
        "catalog_map_method": "章map方式",
        "catalog_map_review": "章mapの人間確認要否",
        "catalog_resolved_chapters": "割当済み章",
        "catalog_people": "人名candidate",
        "catalog_terms": "用語candidate",
        "catalog_references": "参考資料candidate",
        "catalog_unresolved_people": "章未解決の人名signal",
        "catalog_unresolved_terms": "章未解決の用語signal",
        "catalog_confidence_counts": "heuristic信頼signal",
        "catalog_discovery": "catalog発見経路",
        "catalog_seed": "heuristic seed",
        "catalog_generated": "model/NER recall追加",
        "catalog_confidence": "heuristic信頼度",
        "catalog_reasons": "heuristic reason code",
        "catalog_inferred_warning": (
            "title sequenceから推定した章mapです。人間確認が必要であり、承認済みの章割当として"
            "扱ってはいけません。"
        ),
        "catalog_explicit_note": (
            "章mapはsourceに明示的に拘束されていますが、candidate採否には引き続き個別の"
            "人間レビューが必要です。"
        ),
        "navigation": "レビュー項目",
        "section_status": "項目状態",
        "candidate_counts": "candidate状態内訳",
        "evidence_count": "evidence参照数",
        "source_count": "拘束source数",
        "coverage": "生成coverage",
        "semantic": "意味的レビュー",
        "candidate": "candidate",
        "run": "run",
        "record": "record",
        "state": "状態",
        "source": "source",
        "changed": "変更field",
        "proposed": "レビュー対象data",
        "canonical": "現在の正本data",
        "evidence": "evidence・参照metadata",
        "semantic_findings": "意味的指摘",
        "scope": "章scope",
        "book_scope": "本全体・章指定なし",
        "no_candidates": (
            "この項目のcandidate artifactは入力されていません。未生成または未収録という意味であり、"
            "sourceに該当内容が存在しないことの証明ではありません。"
        ),
        "no_canonical": "対応する正本recordはありません。",
        "no_evidence": "検証済みevidence参照はありません。",
        "no_findings": "拘束された意味的指摘は入力されていません。",
        "status_not_available": "not_available（未生成）",
        "status_incomplete": "incomplete（未完了）",
        "status_blocked": "blocked（要修正）",
        "status_review": "review_required（人間確認待ち）",
        "status_decided": "decision_recorded_not_approved（判断記録済・未承認）",
        "status_accounted_empty": "accounted_no_candidate（処理済・候補なし）",
        "sections": {
            "chapters": "章・構造",
            "summaries": "章要約",
            "claims": "主張",
            "certainty": "確実性定義",
            "people": "人名索引",
            "terms": "用語索引",
            "references": "参考資料",
            "author_qa": "著者QA・読解上の論点",
            "policies": "本書固有方針",
        },
    },
}


def _escaped(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _json_block(value: Any) -> str:
    rendered = json.dumps(
        value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False
    )
    return f"<pre>{html.escape(rendered, quote=True)}</pre>"


def _projection(record: Mapping[str, Any] | None, section: str) -> dict[str, Any] | None:
    if record is None:
        return None
    return {
        field: record[field]
        for field in _PROJECTED_FIELDS[section]
        if field in record
    }


def _load_bound_catalog_inventory(
    manifest: Mapping[str, Any],
    normalized_source: str,
    source_path: Path,
    inventory_path: Path,
) -> dict[str, Any]:
    """Load and recheck one catalog inventory against its candidate run.

    The inventory is not trusted merely because its own integrity hash is
    valid.  Its identity must be recorded by the run generator, and its source
    and chapter offsets must still resolve against the exact source already
    verified for that run.
    """

    inventory = load_catalog_inventory(inventory_path)
    generator = manifest["generator"]
    expected_revision = f"1:{inventory['inventory_id']}"
    if (
        generator.get("revision") != expected_revision
        or generator.get("settings_hash") != inventory["integrity_sha256"]
    ):
        raise ReadingPackError(
            "catalog inventory is stale or does not match its candidate run"
        )
    if inventory["language"] != manifest["language"]:
        raise ReadingPackError(
            "catalog inventory language does not match its candidate run"
        )
    if inventory["canonical_data_sha256"] != manifest["canonical"]["data_sha256"]:
        raise ReadingPackError(
            "catalog inventory canonical snapshot does not match its candidate run"
        )

    inventory_source = inventory["source"]
    run_source = manifest["source"]
    source_fields = ("id", "role", "name", "format", "sha256")
    if any(
        inventory_source.get(key) != run_source.get(key) for key in source_fields
    ):
        raise ReadingPackError(
            "catalog inventory source identity does not match its candidate run"
        )
    try:
        source_size = source_path.stat().st_size
    except OSError as exc:
        raise ReadingPackError(
            f"cannot verify catalog inventory source size: {exc}", EXIT_IO
        ) from exc
    if inventory_source.get("size_bytes") != source_size:
        raise ReadingPackError(
            "catalog inventory source size does not match its candidate run"
        )

    normalized_sha256 = hashlib.sha256(normalized_source.encode("utf-8")).hexdigest()
    if inventory["text_sha256"] != normalized_sha256:
        raise ReadingPackError("catalog normalized source hash is stale")
    for span in inventory["chapter_spans"]:
        start = span["char_start"]
        end = span["char_end"]
        if end > len(normalized_source) or hashlib.sha256(
            normalized_source[start:end].encode("utf-8")
        ).hexdigest() != span["span_sha256"]:
            raise ReadingPackError(
                "catalog chapter span no longer matches the source"
            )
    return inventory


def _bundle_identity(
    language: str,
    canonical_sha256: str,
    artifacts: Sequence[_LoadedArtifact],
) -> tuple[str, str]:
    def artifact_identity(artifact: _LoadedArtifact) -> dict[str, Any]:
        value = {
            "run_id": artifact.manifest["run_id"],
            "run_integrity_sha256": artifact.manifest["integrity_sha256"],
            "source_sha256": artifact.manifest["source"]["sha256"],
            "coverage_plan_id": (
                artifact.coverage.get("plan_id")
                if artifact.coverage is not None
                else None
            ),
            "ledger_integrity_sha256": artifact.ledger_integrity_sha256,
            "semantic_integrity_sha256": artifact.semantic_integrity_sha256,
            "semantic_state": (
                artifact.coverage.get("semantic", {}).get("state")
                if artifact.coverage is not None
                else None
            ),
        }
        if artifact.catalog_inventory is not None:
            value["catalog_inventory_id"] = artifact.catalog_inventory["inventory_id"]
            value["catalog_integrity_sha256"] = artifact.catalog_inventory[
                "integrity_sha256"
            ]
        return value

    payload = {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "language": language,
        "canonical_data_sha256": canonical_sha256,
        "artifacts": sorted(
            (artifact_identity(artifact) for artifact in artifacts),
            key=lambda item: (item["run_id"], item["run_integrity_sha256"]),
        ),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    return f"RB-{digest[:20].upper()}", digest


def _load_artifacts(
    project: Path,
    artifacts: Sequence[ReviewBundleArtifact],
) -> tuple[str, Mapping[str, Any], list[_LoadedArtifact]]:
    if not artifacts:
        raise ReadingPackError("at least one review bundle artifact is required")
    if not all(isinstance(artifact, ReviewBundleArtifact) for artifact in artifacts):
        raise ReadingPackError("review bundle artifacts must be ReviewBundleArtifact values")

    config = load_config(project)
    configured_languages = config.get("languages", [])
    project_data_by_lang = {
        language: load_language_data(project, language)
        for language in configured_languages
    }
    loaded: list[_LoadedArtifact] = []
    language: str | None = None
    run_ids: set[str] = set()
    candidate_ids: set[str] = set()

    for specification in artifacts:
        manifest = load_candidate_run(Path(specification.run).resolve())
        run_id = manifest["run_id"]
        if run_id in run_ids:
            raise ReadingPackError(f"duplicate candidate run in review bundle: {run_id}")
        run_ids.add(run_id)
        if language is None:
            language = manifest["language"]
        elif manifest["language"] != language:
            raise ReadingPackError("all review bundle runs must use the same language")
        if language not in project_data_by_lang:
            raise ReadingPackError("candidate language is not configured in the project")

        current = project_data_by_lang[language]
        binding = manifest["canonical"]
        if (
            _value_hash(current) != binding["data_sha256"]
            or _value_hash(project_data_by_lang) != binding["project_data_sha256"]
        ):
            raise ReadingPackError(
                "canonical data changed after candidate creation; create fresh runs before review"
            )

        normalized_source, _ = _verify_source_and_evidence(
            manifest, Path(specification.source_path).resolve()
        )
        catalog_inventory = None
        if specification.catalog_inventory_path is not None:
            catalog_inventory = _load_bound_catalog_inventory(
                manifest,
                normalized_source,
                Path(specification.source_path).resolve(),
                Path(specification.catalog_inventory_path).resolve(),
            )
        run_candidate_ids = {
            candidate["candidate_id"] for candidate in manifest["candidates"]
        }
        overlap = candidate_ids & run_candidate_ids
        if overlap:
            raise ReadingPackError(
                "candidate IDs occur in more than one review bundle run"
            )
        candidate_ids.update(run_candidate_ids)

        semantic_findings: dict[str, tuple[Mapping[str, Any], ...]] = {}
        report: Mapping[str, Any] | None = None
        if (
            specification.semantic_review_path is not None
            and specification.work_ledger_path is None
        ):
            raise ReadingPackError(
                "a semantic review in a review bundle requires its reconciled work ledger"
            )
        ledger = None
        if specification.work_ledger_path is not None:
            ledger = load_work_ledger(Path(specification.work_ledger_path).resolve())
            expected_run = {
                "run_id": run_id,
                "integrity_sha256": manifest["integrity_sha256"],
            }
            if ledger.get("run") != expected_run:
                raise ReadingPackError(
                    "work ledger is stale or does not match its candidate run"
                )
            if (
                ledger.get("language") != language
                or ledger.get("canonical_data_sha256") != binding["data_sha256"]
            ):
                raise ReadingPackError(
                    "work ledger does not match the candidate language and canonical snapshot"
                )
        semantic = None
        if specification.semantic_review_path is not None:
            semantic = load_semantic_review(
                Path(specification.semantic_review_path).resolve()
            )
            if semantic.get("run") != {
                "run_id": run_id,
                "integrity_sha256": manifest["integrity_sha256"],
            }:
                raise ReadingPackError(
                    "semantic review is stale or does not match its candidate run"
                )
            available_candidates = {
                candidate["candidate_id"]: candidate
                for candidate in manifest["candidates"]
            }
            eligible_candidates = {
                candidate_id
                for item in ledger["items"]
                for candidate_id in item.get("candidate_ids", [])
            }
            assessed_candidates = set(
                semantic["assessment"]["assessed_candidate_ids"]
            )
            if not assessed_candidates <= eligible_candidates:
                raise ReadingPackError(
                    "semantic review assesses candidates outside its work ledger"
                )
            if (
                semantic["assessment"]["status"] == "complete"
                and assessed_candidates != eligible_candidates
            ):
                raise ReadingPackError(
                    "complete semantic review does not cover its full work ledger"
                )
            work_items = {item["work_id"]: item for item in ledger["items"]}
            grouped: dict[str, list[Mapping[str, Any]]] = {}
            for finding in semantic["findings"]:
                candidate = available_candidates.get(finding["candidate_id"])
                work_item = work_items.get(finding["work_id"])
                if (
                    candidate is None
                    or finding["candidate_id"] not in assessed_candidates
                    or work_item is None
                    or finding["candidate_id"] not in work_item.get("candidate_ids", [])
                ):
                    raise ReadingPackError(
                        "semantic finding is outside its candidate and work bindings"
                    )
                evidence_ids = {
                    evidence["id"] for evidence in candidate.get("evidence_refs", [])
                }
                if not set(finding["evidence_ref_ids"]) <= evidence_ids:
                    raise ReadingPackError(
                        "semantic finding references evidence outside its candidate"
                    )
                grouped.setdefault(finding["candidate_id"], []).append(finding)
            semantic_findings = {
                identifier: tuple(values) for identifier, values in grouped.items()
            }
        if ledger is not None:
            report = coverage_report(ledger, semantic)

        loaded.append(
            _LoadedArtifact(
                manifest=manifest,
                normalized_source=normalized_source,
                coverage=report,
                semantic_findings=semantic_findings,
                ledger_integrity_sha256=(
                    ledger["integrity_sha256"] if ledger is not None else None
                ),
                semantic_integrity_sha256=(
                    semantic["integrity_sha256"] if semantic is not None else None
                ),
                catalog_inventory=catalog_inventory,
            )
        )

    assert language is not None
    loaded.sort(
        key=lambda artifact: (
            str(artifact.manifest["run_id"]),
            str(artifact.manifest["integrity_sha256"]),
        )
    )
    return language, project_data_by_lang[language], loaded


def _section_candidates(
    artifacts: Sequence[_LoadedArtifact], collection: str
) -> list[tuple[_LoadedArtifact, Mapping[str, Any]]]:
    return [
        (artifact, candidate)
        for artifact in artifacts
        for candidate in artifact.manifest["candidates"]
        if candidate.get("collection") == collection
    ]


def _coverage_items(
    artifacts: Sequence[_LoadedArtifact], module: str
) -> list[Mapping[str, Any]]:
    return [
        item
        for artifact in artifacts
        if artifact.coverage is not None
        for item in artifact.coverage.get("modules", [])
        if item.get("module") == module
    ]


def _section_status(
    candidates: Sequence[tuple[_LoadedArtifact, Mapping[str, Any]]],
    coverage_items: Sequence[Mapping[str, Any]],
    labels: Mapping[str, Any],
) -> str:
    if any(item.get("failed") or item.get("pending") for item in coverage_items):
        return str(labels["status_incomplete"])
    semantic_states = {
        artifact.coverage.get("semantic", {}).get("state")
        for artifact, _ in candidates
        if artifact.coverage is not None
    }
    states = Counter(candidate.get("candidate_state") for _, candidate in candidates)
    if states["quarantined"] or states["proposed"] or "blocked" in semantic_states:
        return str(labels["status_blocked"])
    if not candidates:
        if coverage_items and all(
            item.get("pending", 0) == 0 and item.get("failed", 0) == 0
            for item in coverage_items
        ):
            return str(labels["status_accounted_empty"])
        return str(labels["status_not_available"])
    if states["ready_for_review"] or "review_required" in semantic_states:
        return str(labels["status_review"])
    return str(labels["status_decided"])


def _metadata_counts(values: Mapping[str, int]) -> str:
    return " · ".join(
        f"{_escaped(key)}={value}"
        for key, value in values.items()
        if value
    ) or "0"


def _catalog_signal_html(
    artifact: _LoadedArtifact,
    candidate: Mapping[str, Any],
    labels: Mapping[str, Any],
) -> str:
    """Explain whether a catalog candidate came from the seed or recall pass."""

    inventory = artifact.catalog_inventory
    record = candidate.get("record")
    collection = candidate.get("collection")
    if (
        inventory is None
        or not isinstance(record, Mapping)
        or collection not in {"names", "glossary", "references"}
    ):
        return ""
    if collection == "names":
        kind = "person"
        value = record.get("name")
        chapter_id = record.get("chapter_id")
    elif collection == "glossary":
        kind = "term"
        value = record.get("term")
        chapter_id = record.get("chapter_id")
    else:
        kind = "reference"
        value = record.get("url")
        chapter_id = ""
    matched = None
    if isinstance(value, str) and isinstance(chapter_id, str):
        for item in inventory["items"]:
            item_value = item["url"] if kind == "reference" else item["label"]
            if (
                item["kind"] == kind
                and normalize_text(item_value) == normalize_text(value)
                and item["chapter"]["chapter_id"] == chapter_id
            ):
                matched = item
                break
    if matched is None:
        return (
            f'<dt>{_escaped(labels["catalog_discovery"])}</dt>'
            f'<dd><code>{_escaped(labels["catalog_generated"])}</code></dd>'
        )
    reasons = ", ".join(str(value) for value in matched["reason_codes"])
    return (
        f'<dt>{_escaped(labels["catalog_discovery"])}</dt>'
        f'<dd><code>{_escaped(labels["catalog_seed"])}</code></dd>'
        f'<dt>{_escaped(labels["catalog_confidence"])}</dt>'
        f'<dd><code>{_escaped(matched["confidence"])}</code></dd>'
        f'<dt>{_escaped(labels["catalog_reasons"])}</dt>'
        f'<dd><code>{_escaped(reasons)}</code></dd>'
    )


def _candidate_html(
    section: str,
    artifact: _LoadedArtifact,
    candidate: Mapping[str, Any],
    *,
    canonical: Mapping[str, Any] | None,
    labels: Mapping[str, Any],
    context_characters: int,
    scope_label: str,
) -> str:
    manifest = artifact.manifest
    record_value = candidate.get("record")
    record = record_value if isinstance(record_value, Mapping) else None
    proposed = _projection(record, section)
    current = _projection(canonical, section)
    changed = sorted(
        field
        for field in set(current or {}) | set(proposed or {})
        if (current or {}).get(field) != (proposed or {}).get(field)
    )
    refs = candidate.get("evidence_refs", [])
    evidence_parts: list[str] = []
    if isinstance(refs, list):
        for evidence in refs:
            locator = evidence["locator"]
            evidence_parts.append(
                '<div class="evidence-item">'
                '<dl class="metadata compact">'
                f'<dt>ID</dt><dd><code>{_escaped(evidence["id"])}</code></dd>'
                f'<dt>supports</dt><dd><code>{_escaped(evidence.get("supports_field", "record"))}</code></dd>'
                f'<dt>locator</dt><dd><code>{_escaped(locator["kind"])}:'
                f'{locator["char_start"]}-{locator["char_end"]}</code></dd>'
                f'<dt>source sha256</dt><dd><code>{_escaped(evidence["source_sha256"])}</code></dd>'
                f'<dt>span sha256</dt><dd><code>{_escaped(evidence["span_sha256"])}</code></dd>'
                '</dl>'
                f'{_context_html(artifact.normalized_source, evidence, context_characters=context_characters)}'
                '</div>'
            )
    evidence_html = "".join(evidence_parts) or f'<p class="empty">{_escaped(labels["no_evidence"])}</p>'

    findings = artifact.semantic_findings.get(candidate["candidate_id"], ())
    if findings:
        finding_parts = []
        for finding in findings:
            adjudication = finding.get("adjudication", {})
            finding_parts.append(
                "<li>"
                f'<code>{_escaped(finding["finding_id"])}</code> · '
                f'{_escaped(finding["severity"])} · {_escaped(finding["category"])} · '
                f'{_escaped(finding["reason_code"])} · '
                f'{_escaped(adjudication.get("decision", "pending"))}'
                "</li>"
            )
        findings_html = f"<ul>{''.join(finding_parts)}</ul>"
    else:
        findings_html = f'<p class="empty">{_escaped(labels["no_findings"])}</p>'

    source = manifest["source"]
    canonical_html = (
        _json_block(current)
        if current is not None
        else f'<p class="empty">{_escaped(labels["no_canonical"])}</p>'
    )
    proposed_html = (
        _json_block(proposed)
        if proposed is not None
        else '<p class="empty">candidate prose withheld by quarantine</p>'
    )
    changed_text = ", ".join(_escaped(field) for field in changed) or "none"
    catalog_signal = _catalog_signal_html(artifact, candidate, labels)
    return f"""
    <article class="candidate" id="{_escaped(section)}-{_escaped(candidate['candidate_id'])}">
      <h3>{_escaped(labels['candidate'])} <code>{_escaped(candidate['candidate_id'])}</code></h3>
      <dl class="metadata">
        <dt>{_escaped(labels['run'])}</dt><dd><code>{_escaped(manifest['run_id'])}</code></dd>
        <dt>{_escaped(labels['record'])}</dt><dd><code>{_escaped(candidate.get('record_id', ''))}</code></dd>
        <dt>{_escaped(labels['state'])}</dt><dd><code>{_escaped(candidate.get('candidate_state', ''))}</code></dd>
        <dt>{_escaped(labels['source'])}</dt><dd><code>{_escaped(source.get('id', 'SRC-1'))}</code> · {_escaped(source.get('role', 'primary-book'))} · {_escaped(source['name'])} · <code>sha256:{_escaped(source['sha256'])}</code></dd>
        <dt>{_escaped(labels['scope'])}</dt><dd>{_escaped(scope_label)}</dd>
        <dt>{_escaped(labels['changed'])}</dt><dd>{changed_text}</dd>
        {catalog_signal}
      </dl>
      <div class="comparison">
        <div><h4>{_escaped(labels['canonical'])}</h4>{canonical_html}</div>
        <div><h4>{_escaped(labels['proposed'])}</h4>{proposed_html}</div>
      </div>
      <h4>{_escaped(labels['evidence'])}</h4>
      {evidence_html}
      <h4>{_escaped(labels['semantic_findings'])}</h4>
      {findings_html}
    </article>
    """


def _candidate_scope(
    section: str,
    candidate: Mapping[str, Any],
    labels: Mapping[str, Any],
) -> str:
    record = candidate.get("record")
    if section in {"chapters", "summaries"} and candidate.get("record_id"):
        return str(candidate["record_id"])
    if isinstance(record, Mapping):
        chapter_id = record.get("chapter_id")
        if isinstance(chapter_id, str) and chapter_id:
            return chapter_id
        chapter_ids = record.get("chapter_ids")
        if isinstance(chapter_ids, list) and chapter_ids:
            return ", ".join(str(value) for value in chapter_ids)
    return str(labels["book_scope"])


def _section_html(
    section: str,
    collection: str,
    artifacts: Sequence[_LoadedArtifact],
    *,
    canonical_records: Mapping[tuple[str, str], Mapping[str, Any]],
    labels: Mapping[str, Any],
    context_characters: int,
) -> str:
    candidates = _section_candidates(artifacts, collection)
    coverage_items = _coverage_items(artifacts, collection)
    states = Counter(
        str(candidate.get("candidate_state", "unknown"))
        for _, candidate in candidates
    )
    evidence_count = sum(
        len(candidate.get("evidence_refs", []))
        for _, candidate in candidates
        if isinstance(candidate.get("evidence_refs"), list)
    )
    sources = {
        (
            artifact.manifest["source"].get("id", "SRC-1"),
            artifact.manifest["source"]["sha256"],
        )
        for artifact, _ in candidates
    }
    coverage_summary = Counter()
    for item in coverage_items:
        for status in ("total", "pending", "complete", "no_supported_candidate", "failed", "skipped"):
            coverage_summary[status] += int(item.get(status, 0))
    semantic_states = Counter(
        artifact.coverage.get("semantic", {}).get("state", "not_assessed")
        for artifact, _ in candidates
        if artifact.coverage is not None
    )
    status = _section_status(candidates, coverage_items, labels)

    if candidates:
        grouped: dict[str, list[tuple[_LoadedArtifact, Mapping[str, Any]]]] = {}
        for artifact, candidate in candidates:
            scope = _candidate_scope(section, candidate, labels)
            grouped.setdefault(scope, []).append((artifact, candidate))
        groups = []
        for scope, selected in grouped.items():
            cards = []
            for artifact, candidate in selected:
                key = (collection, str(candidate.get("record_id", "")))
                cards.append(
                    _candidate_html(
                        section,
                        artifact,
                        candidate,
                        canonical=canonical_records.get(key),
                        labels=labels,
                        context_characters=context_characters,
                        scope_label=scope,
                    )
                )
            groups.append(
                f'<div class="chapter-group"><h3 class="scope-heading">'
                f'{_escaped(labels["scope"])}: {_escaped(scope)}</h3>{"".join(cards)}</div>'
            )
        body = "".join(groups)
    else:
        body = f'<p class="empty no-candidates">{_escaped(labels["no_candidates"])}</p>'

    return f"""
    <section class="review-section" id="section-{_escaped(section)}">
      <h2>{_escaped(labels['sections'][section])}</h2>
      <dl class="metadata section-summary">
        <dt>{_escaped(labels['section_status'])}</dt><dd><strong>{_escaped(status)}</strong></dd>
        <dt>{_escaped(labels['candidate_counts'])}</dt><dd>{_metadata_counts(states)}</dd>
        <dt>{_escaped(labels['evidence_count'])}</dt><dd>{evidence_count}</dd>
        <dt>{_escaped(labels['source_count'])}</dt><dd>{len(sources)}</dd>
        <dt>{_escaped(labels['coverage'])}</dt><dd>{_metadata_counts(coverage_summary)}</dd>
        <dt>{_escaped(labels['semantic'])}</dt><dd>{_metadata_counts(semantic_states)}</dd>
      </dl>
      {body}
    </section>
    """


def _catalog_report_html(
    artifacts: Sequence[_LoadedArtifact], labels: Mapping[str, Any]
) -> str:
    selected = [
        artifact for artifact in artifacts if artifact.catalog_inventory is not None
    ]
    if not selected:
        return ""
    cards: list[str] = []
    for artifact in selected:
        inventory = artifact.catalog_inventory
        assert inventory is not None
        summary = inventory["summary"]
        chapter_map = inventory["chapter_map"]
        confidence = Counter(item["confidence"] for item in inventory["items"])
        map_note = (
            labels["catalog_inferred_warning"]
            if chapter_map["review_required"]
            else labels["catalog_explicit_note"]
        )
        cards.append(
            '<article class="catalog-card">'
            f'<h3><code>{_escaped(artifact.manifest["run_id"])}</code></h3>'
            '<dl class="metadata">'
            f'<dt>{_escaped(labels["catalog_inventory"])}</dt>'
            f'<dd><code>{_escaped(inventory["inventory_id"])}</code> · '
            f'<code>sha256:{_escaped(inventory["integrity_sha256"])}</code></dd>'
            f'<dt>{_escaped(labels["catalog_extractor"])}</dt>'
            f'<dd><code>{_escaped(inventory["extractor"])}</code></dd>'
            f'<dt>{_escaped(labels["catalog_map_method"])}</dt>'
            f'<dd><code>{_escaped(chapter_map["method"])}</code></dd>'
            f'<dt>{_escaped(labels["catalog_map_review"])}</dt>'
            f'<dd><code>{str(chapter_map["review_required"]).lower()}</code></dd>'
            f'<dt>{_escaped(labels["catalog_resolved_chapters"])}</dt>'
            f'<dd>{summary["resolved_chapters"]}</dd>'
            f'<dt>{_escaped(labels["catalog_people"])}</dt>'
            f'<dd>{summary["people"]}</dd>'
            f'<dt>{_escaped(labels["catalog_terms"])}</dt>'
            f'<dd>{summary["terms"]}</dd>'
            f'<dt>{_escaped(labels["catalog_references"])}</dt>'
            f'<dd>{summary["references"]}</dd>'
            f'<dt>{_escaped(labels["catalog_unresolved_people"])}</dt>'
            f'<dd>{summary["unresolved_people"]}</dd>'
            f'<dt>{_escaped(labels["catalog_unresolved_terms"])}</dt>'
            f'<dd>{summary["unresolved_terms"]}</dd>'
            f'<dt>{_escaped(labels["catalog_confidence_counts"])}</dt>'
            f'<dd>{_metadata_counts(confidence)}</dd>'
            '</dl>'
            f'<p class="integrity">{_escaped(map_note)}</p>'
            '</article>'
        )
    return f"""
    <section class="review-section catalog-report" id="catalog-extraction-report">
      <h2>{_escaped(labels['catalog_report'])}</h2>
      <p class="warning"><strong>{_escaped(labels['catalog_caveat'])}</strong></p>
      {''.join(cards)}
    </section>
    """


def _page_html(
    *,
    language: str,
    canonical_data: Mapping[str, Any],
    artifacts: Sequence[_LoadedArtifact],
    bundle_id: str,
    bundle_sha256: str,
    context_characters: int,
) -> str:
    labels = _LABELS.get(language, _LABELS["en"])
    records = _canonical_records(canonical_data)
    catalog_report = _catalog_report_html(artifacts, labels)
    catalog_count = sum(
        artifact.catalog_inventory is not None for artifact in artifacts
    )
    sections = [
        _section_html(
            section,
            collection,
            artifacts,
            canonical_records=records,
            labels=labels,
            context_characters=context_characters,
        )
        for section, collection in _SECTION_SPECS
    ]
    navigation = "".join(
        f'<li><a href="#section-{_escaped(section)}">{_escaped(labels["sections"][section])}</a></li>'
        for section, _ in _SECTION_SPECS
    )
    if catalog_count:
        navigation = (
            f'<li><a href="#catalog-extraction-report">'
            f'{_escaped(labels["catalog_report"])}</a></li>'
            + navigation
        )
    candidate_ids = {
        candidate["candidate_id"]
        for artifact in artifacts
        for candidate in artifact.manifest["candidates"]
    }
    source_keys = {
        (
            artifact.manifest["source"].get("id", "SRC-1"),
            artifact.manifest["source"]["sha256"],
        )
        for artifact in artifacts
    }
    return f"""<!doctype html>
<html lang="{_escaped(language)}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'">
  <meta name="referrer" content="no-referrer">
  <title>{_escaped(labels['title'])} — {_escaped(bundle_id)}</title>
  <style>
    :root {{ color-scheme: light dark; font-family: system-ui, sans-serif; line-height: 1.5; }}
    body {{ max-width: 1240px; margin: 0 auto; padding: 2rem; }}
    .warning {{ border: 2px solid #b45309; background: #fffbeb; color: #451a03; padding: 1rem; border-radius: .5rem; }}
    .boundary {{ border-left: .4rem solid #b91c1c; padding: .8rem 1rem; background: color-mix(in srgb, canvas 92%, #ef4444); font-weight: 650; }}
    .integrity {{ border-left: .35rem solid #2563eb; padding: .7rem 1rem; background: color-mix(in srgb, canvas 94%, #3b82f6); }}
    .review-section {{ border-top: 4px solid #475569; margin-top: 3rem; padding-top: 1rem; }}
    .candidate {{ border-top: 1px solid #94a3b8; margin-top: 2rem; padding-top: .75rem; }}
    .chapter-group {{ margin: 1.5rem 0 2.5rem; }} .scope-heading {{ position: sticky; top: 0; padding: .45rem .7rem; background: color-mix(in srgb, canvas 90%, #64748b); z-index: 1; }}
    .metadata {{ display: grid; grid-template-columns: minmax(9rem, 14rem) 1fr; gap: .35rem 1rem; }}
    .metadata dt {{ font-weight: 700; }} .metadata dd {{ margin: 0; overflow-wrap: anywhere; }}
    .comparison {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 1rem; }}
    pre {{ white-space: pre-wrap; overflow-wrap: anywhere; border: 1px solid #94a3b8; border-radius: .35rem; padding: .75rem; background: color-mix(in srgb, canvas 95%, #64748b); }}
    .source-context {{ white-space: pre-wrap; overflow-wrap: anywhere; padding: .8rem; border-left: .3rem solid #2563eb; background: color-mix(in srgb, canvas 92%, #3b82f6); }}
    mark {{ background: #fde047; color: #1c1917; }}
    .evidence-item {{ margin: 1rem 0; }} .empty {{ color: #64748b; font-style: italic; }}
    nav ul {{ columns: 2; }}
    @media (max-width: 760px) {{ .comparison {{ grid-template-columns: 1fr; }} .metadata {{ grid-template-columns: 1fr; }} nav ul {{ columns: 1; }} }}
  </style>
</head>
<body>
  <header>
    <h1>{_escaped(labels['title'])}</h1>
    <p class="warning"><strong>{_escaped(labels['private'])}</strong></p>
    <p class="boundary">{_escaped(labels['boundary'])}</p>
    <p class="integrity">{_escaped(labels['integrity'])}</p>
    <dl class="metadata">
      <dt>{_escaped(labels['bundle'])}</dt><dd><code>{_escaped(bundle_id)}</code> · <code>sha256:{_escaped(bundle_sha256)}</code></dd>
      <dt>{_escaped(labels['language'])}</dt><dd>{_escaped(language)}</dd>
      <dt>{_escaped(labels['runs'])}</dt><dd>{len(artifacts)}</dd>
      <dt>{_escaped(labels['sources'])}</dt><dd>{len(source_keys)}</dd>
      <dt>{_escaped(labels['unique_candidates'])}</dt><dd>{len(candidate_ids)}</dd>
      <dt>{_escaped(labels['catalogs'])}</dt><dd>{catalog_count}</dd>
    </dl>
    <nav aria-label="{_escaped(labels['navigation'])}"><h2>{_escaped(labels['navigation'])}</h2><ul>{navigation}</ul></nav>
  </header>
  {catalog_report}
  {''.join(sections)}
</body>
</html>
"""


def _destination(
    project: Path,
    bundle_id: str,
    output_path: Path | None,
) -> Path:
    review_root = _ensure_private_review_directory(project)
    if output_path is None:
        destination = review_root / f"review-bundle-{bundle_id[3:].lower()}.html"
    elif Path(output_path).is_absolute():
        destination = Path(output_path)
    else:
        destination = review_root / Path(output_path)
    try:
        if destination.parent.resolve() != review_root:
            raise ReadingPackError(
                "review bundle output must be a direct child of .reading-pack/reviews",
                EXIT_IO,
            )
    except OSError as exc:
        raise ReadingPackError(f"cannot resolve review bundle output: {exc}", EXIT_IO) from exc
    if destination.suffix.lower() != ".html" or not destination.name:
        raise ReadingPackError("review bundle output must be an .html file", EXIT_IO)
    if destination.is_symlink() or destination.exists():
        raise ReadingPackError(
            f"refusing to overwrite review bundle output: {destination}", EXIT_IO
        )
    return destination


def render_private_review_bundle(
    project: Path,
    *,
    artifacts: Iterable[ReviewBundleArtifact],
    output_path: Path | None = None,
    context_characters: int = DEFAULT_CONTEXT_CHARACTERS,
) -> Path:
    """Render one read-only, private HTML review across candidate workflows.

    The function verifies all supplied artifacts before creating the output.
    It never mutates a candidate run or canonical data and deliberately emits
    no decision action.  Relative outputs must be direct children of
    ``.reading-pack/reviews``.
    """

    if (
        not isinstance(context_characters, int)
        or isinstance(context_characters, bool)
        or not MIN_CONTEXT_CHARACTERS <= context_characters <= MAX_CONTEXT_CHARACTERS
    ):
        raise ReadingPackError(
            f"context characters must be between {MIN_CONTEXT_CHARACTERS} and "
            f"{MAX_CONTEXT_CHARACTERS}"
        )
    project = Path(project).resolve()
    if not (project / CONFIG_NAME).is_file():
        raise ReadingPackError(f"{CONFIG_NAME} not found in {project}", EXIT_IO)
    artifact_list = list(artifacts)
    language, canonical_data, loaded = _load_artifacts(project, artifact_list)
    canonical_sha256 = _value_hash(canonical_data)
    bundle_id, bundle_sha256 = _bundle_identity(language, canonical_sha256, loaded)
    destination = _destination(project, bundle_id, output_path)
    page = _page_html(
        language=language,
        canonical_data=canonical_data,
        artifacts=loaded,
        bundle_id=bundle_id,
        bundle_sha256=bundle_sha256,
        context_characters=context_characters,
    )
    _write_private_html(destination, page)
    try:
        mode = stat.S_IMODE(destination.stat().st_mode)
    except OSError as exc:
        raise ReadingPackError(
            f"cannot verify review bundle permissions: {exc}", EXIT_IO
        ) from exc
    if mode != 0o600:
        raise ReadingPackError("review bundle permissions are not owner-only", EXIT_IO)
    return destination
