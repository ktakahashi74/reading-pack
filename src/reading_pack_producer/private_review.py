"""Private, source-rehydrated review pages for candidate runs.

The candidate manifest intentionally does not retain evidence excerpts.  This
module resolves the hash-bound spans from the exact source each time a review
page is produced.  The resulting HTML is a private, regenerable derivative: it
is written only below ``.reading-pack/reviews`` with owner-only permissions and
never changes the candidate run or canonical data.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import shlex
import stat
import tempfile
from pathlib import Path
from typing import Any, Iterable, Mapping

from .candidates import (
    _value_hash,
    _verify_source_and_evidence,
    load_candidate_run,
)
from reading_pack.errors import EXIT_IO, ReadingPackError
from reading_pack.project import CONFIG_NAME, load_config, load_language_data
from .work_ledger import load_semantic_review


DEFAULT_CONTEXT_CHARACTERS = 120
MIN_CONTEXT_CHARACTERS = 40
MAX_CONTEXT_CHARACTERS = 240
MAX_PRIVATE_REVIEW_BYTES = 64 * 1024 * 1024


_LABELS = {
    "en": {
        "title": "Private candidate review",
        "private_warning": (
            "Private review material. This file contains dynamically resolved "
            "source excerpts. Keep it under .reading-pack and do not publish it."
        ),
        "integrity": "Integrity checks",
        "integrity_value": (
            "Manifest, source, normalized text, evidence spans, and canonical "
            "snapshot verified at render time."
        ),
        "semantic_warning": (
            "A matching source span proves occurrence and integrity only. It does "
            "not prove entailment, completeness, correct attribution, or authorial authority."
        ),
        "summary": "Run summary",
        "source": "Source",
        "language": "Language",
        "created": "Run created",
        "candidate_count": "Candidates shown",
        "candidate": "Candidate",
        "collection": "Collection",
        "record": "Record",
        "state": "State",
        "change": "Canonical relationship",
        "new_record": "new record",
        "update_record": "updates existing record",
        "unavailable_record": "candidate prose withheld by quarantine",
        "findings": "Automated findings",
        "qa_passed": "Structural QA passed; semantic review is still required.",
        "qa_failed": "Candidate is quarantined or has QA findings.",
        "semantic_findings": "Semantic findings",
        "no_semantic_findings": "No bound semantic finding was supplied for this candidate.",
        "semantic_item": "{severity} · {category} · {reason} · adjudication: {decision}",
        "reason_codes": "Reason codes",
        "no_reasons": "none",
        "changed_fields": "Changed fields",
        "canonical": "Current canonical record",
        "proposed": "Proposed record",
        "absent": "No record with this ID exists in the canonical collection.",
        "evidence": "Dynamically resolved evidence",
        "evidence_locator": "normalized text characters {start}–{end}",
        "no_evidence": "No verified evidence span is available.",
        "checklist": "Human review checklist",
        "check_items": (
            "Does each span actually support the proposed record?",
            "Are qualifications, exceptions, uncertainty, and scope preserved?",
            "Is attribution to the author, a cited source, or a critic correct?",
            "Would this record remain useful without reconstructing the book?",
        ),
        "decision": "Explicit decision commands",
        "decision_note": (
            "Run commands only after reviewing this one candidate. Every command "
            "contains one exact candidate ID; this page provides no accept-all action."
        ),
        "accept": "Accept exact candidate for later draft application",
        "apply": "Apply an already accepted candidate as canonical draft data",
        "reject": "Reject exact candidate without changing canonical data",
        "no_action": "No decision command is appropriate for this candidate state.",
        "field_absent": "(absent)",
    },
    "ja": {
        "title": "非公開candidateレビュー",
        "private_warning": (
            "非公開レビュー資料です。このfileには動的に再解決したsource抜粋が含まれます。"
            ".reading-pack内に保ち、公開しないでください。"
        ),
        "integrity": "完全性検査",
        "integrity_value": (
            "生成時にmanifest、source、正規化text、evidence span、正本snapshotを検証済みです。"
        ),
        "semantic_warning": (
            "source spanの一致が証明するのは出現と完全性だけです。論理的支持、網羅性、"
            "正しい帰属、著者authorityは証明しません。"
        ),
        "summary": "run概要",
        "source": "source",
        "language": "言語",
        "created": "run作成時刻",
        "candidate_count": "表示candidate数",
        "candidate": "candidate",
        "collection": "collection",
        "record": "record",
        "state": "状態",
        "change": "正本との関係",
        "new_record": "新規record",
        "update_record": "既存recordの更新",
        "unavailable_record": "隔離によりcandidate本文は保存されていません",
        "findings": "自動検査結果",
        "qa_passed": "構造QAを通過しています。意味的レビューは別途必要です。",
        "qa_failed": "candidateは隔離中、またはQA指摘があります。",
        "semantic_findings": "意味的検査結果",
        "no_semantic_findings": "このcandidateに拘束された意味的指摘は入力されていません。",
        "semantic_item": "{severity} · {category} · {reason} · 判断: {decision}",
        "reason_codes": "reason code",
        "no_reasons": "なし",
        "changed_fields": "変更field",
        "canonical": "現在の正本record",
        "proposed": "提案record",
        "absent": "正本collectionに同じIDのrecordはありません。",
        "evidence": "動的に再解決したevidence",
        "evidence_locator": "正規化text文字位置 {start}–{end}",
        "no_evidence": "検証済みevidence spanはありません。",
        "checklist": "人間レビューchecklist",
        "check_items": (
            "各spanは提案recordを実際に支持しているか。",
            "限定、例外、不確実性、適用範囲が保持されているか。",
            "著者、引用source、批判者への帰属は正しいか。",
            "本を再構築せずに役立つrecordになっているか。",
        ),
        "decision": "明示的な判断command",
        "decision_note": (
            "この1件を確認した後にだけcommandを実行してください。各commandは正確なcandidate IDを"
            "1件だけ含み、このpageにaccept-all操作はありません。"
        ),
        "accept": "このcandidateだけを後続のdraft適用対象としてaccept",
        "apply": "accept済みcandidateを正本draft dataとして適用",
        "reject": "正本を変更せず、このcandidateだけをreject",
        "no_action": "このcandidate状態に適切な判断commandはありません。",
        "field_absent": "（なし）",
    },
}


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def _escaped(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _json_block(value: Any) -> str:
    return f"<pre>{html.escape(_json_text(value), quote=True)}</pre>"


def _canonical_records(data: Mapping[str, Any]) -> dict[tuple[str, str], Mapping[str, Any]]:
    result: dict[tuple[str, str], Mapping[str, Any]] = {}
    for collection, records in data.items():
        if not isinstance(collection, str) or not isinstance(records, list):
            continue
        for record in records:
            if isinstance(record, Mapping) and isinstance(record.get("id"), str):
                result[(collection, record["id"])] = record
    return result


def _changed_fields(
    canonical: Mapping[str, Any] | None,
    proposed: Mapping[str, Any] | None,
) -> list[str]:
    if proposed is None:
        return []
    if canonical is None:
        return sorted(str(key) for key in proposed)
    return sorted(
        str(key)
        for key in set(canonical) | set(proposed)
        if canonical.get(key) != proposed.get(key)
    )


def _context_html(
    normalized_source: str,
    evidence: Mapping[str, Any],
    *,
    context_characters: int,
) -> str:
    locator = evidence["locator"]
    start = locator["char_start"]
    end = locator["char_end"]
    left_start = max(0, start - context_characters)
    right_end = min(len(normalized_source), end + context_characters)
    left = normalized_source[left_start:start]
    span = normalized_source[start:end]
    right = normalized_source[end:right_end]
    leading = "…" if left_start else ""
    trailing = "…" if right_end < len(normalized_source) else ""
    return (
        '<div class="source-context">'
        f'<span class="ellipsis">{leading}</span>'
        f"{html.escape(left, quote=True)}"
        f"<mark>{html.escape(span, quote=True)}</mark>"
        f"{html.escape(right, quote=True)}"
        f'<span class="ellipsis">{trailing}</span>'
        "</div>"
    )


def _command(*parts: str) -> str:
    return " ".join(shlex.quote(part) for part in parts)


def _decision_commands(
    *,
    state: str,
    candidate_id: str,
    run: Path,
    source_path: Path,
    project: Path,
    language: str,
    labels: Mapping[str, Any],
) -> list[tuple[str, str]]:
    common = ("reading-pack", "candidates")
    commands: list[tuple[str, str]] = []
    if state == "ready_for_review":
        commands.append(
            (
                str(labels["accept"]),
                _command(
                    *common,
                    "accept",
                    str(run),
                    "--id",
                    candidate_id,
                    "--reviewer",
                    "Reviewer Name",
                ),
            )
        )
    if state == "accepted":
        commands.append(
            (
                str(labels["apply"]),
                _command(
                    *common,
                    "apply",
                    str(run),
                    "--source",
                    str(source_path),
                    "--project",
                    str(project),
                    "--lang",
                    language,
                    "--id",
                    candidate_id,
                ),
            )
        )
    if state not in {"rejected", "applying", "applied"}:
        commands.append(
            (
                str(labels["reject"]),
                _command(*common, "reject", str(run), "--id", candidate_id),
            )
        )
    return commands


def _candidate_html(
    candidate: Mapping[str, Any],
    *,
    canonical: Mapping[str, Any] | None,
    normalized_source: str,
    context_characters: int,
    labels: Mapping[str, Any],
    run: Path,
    source_path: Path,
    project: Path,
    language: str,
    semantic_findings: list[Mapping[str, Any]],
) -> str:
    candidate_id = str(candidate.get("candidate_id", ""))
    collection = str(candidate.get("collection", ""))
    record_id = str(candidate.get("record_id", ""))
    state = str(candidate.get("candidate_state", ""))
    record = candidate.get("record")
    proposed = record if isinstance(record, Mapping) else None
    changed = _changed_fields(canonical, proposed)
    qa = candidate.get("qa", {})
    passed = isinstance(qa, Mapping) and qa.get("passed") is True
    reasons = qa.get("reason_codes", []) if isinstance(qa, Mapping) else []
    refs = candidate.get("evidence_refs", [])
    relation = labels["update_record"] if canonical is not None else labels["new_record"]

    reason_text = ", ".join(_escaped(reason) for reason in reasons) or _escaped(labels["no_reasons"])
    changed_text = ", ".join(_escaped(field) for field in changed) or _escaped(labels["no_reasons"])
    finding = labels["qa_passed"] if passed else labels["qa_failed"]

    if semantic_findings:
        semantic_items = []
        for item in semantic_findings:
            adjudication = item.get("adjudication", {})
            decision = (
                adjudication.get("decision", "pending")
                if isinstance(adjudication, Mapping)
                else "pending"
            )
            description = str(labels["semantic_item"]).format(
                severity=item.get("severity", ""),
                category=item.get("category", ""),
                reason=item.get("reason_code", ""),
                decision=decision,
            )
            references = ", ".join(str(value) for value in item.get("evidence_ref_ids", []))
            semantic_items.append(
                '<li class="semantic-finding">'
                f'<code>{_escaped(item.get("finding_id", ""))}</code> — {_escaped(description)}'
                + (f'<br><small>{_escaped(references)}</small>' if references else "")
                + "</li>"
            )
        semantic_html = f"<ul>{''.join(semantic_items)}</ul>"
    else:
        semantic_html = f'<p class="empty">{_escaped(labels["no_semantic_findings"])}</p>'

    canonical_html = _json_block(canonical) if canonical is not None else f'<p class="empty">{_escaped(labels["absent"])}</p>'
    proposed_html = _json_block(proposed) if proposed is not None else f'<p class="empty">{_escaped(labels["unavailable_record"])}</p>'

    if isinstance(refs, list) and refs:
        evidence_parts = []
        for evidence in refs:
            locator = evidence["locator"]
            field_label = (
                f'<code>{_escaped(evidence["supports_field"])}</code> · '
                if evidence.get("supports_field")
                else ""
            )
            evidence_parts.append(
                '<div class="evidence">'
                f'<div class="locator"><code>{_escaped(evidence["id"])}</code> · '
                f'{field_label}'
                f'{_escaped(str(labels["evidence_locator"]).format(start=locator["char_start"], end=locator["char_end"]))}</div>'
                f'{_context_html(normalized_source, evidence, context_characters=context_characters)}'
                "</div>"
            )
        evidence_html = "".join(evidence_parts)
    else:
        evidence_html = f'<p class="empty">{_escaped(labels["no_evidence"])}</p>'

    commands = _decision_commands(
        state=state,
        candidate_id=candidate_id,
        run=run,
        source_path=source_path,
        project=project,
        language=language,
        labels=labels,
    )
    if commands:
        commands_html = "".join(
            f'<div class="command"><div>{_escaped(description)}</div><pre>{_escaped(command)}</pre></div>'
            for description, command in commands
        )
    else:
        commands_html = f'<p class="empty">{_escaped(labels["no_action"])}</p>'

    checklist = "".join(f"<li>{_escaped(item)}</li>" for item in labels["check_items"])
    return f"""
    <section class="candidate" id="{_escaped(candidate_id)}">
      <h2>{_escaped(labels['candidate'])} <code>{_escaped(candidate_id)}</code></h2>
      <dl class="metadata">
        <dt>{_escaped(labels['collection'])}</dt><dd><code>{_escaped(collection)}</code></dd>
        <dt>{_escaped(labels['record'])}</dt><dd><code>{_escaped(record_id or labels['field_absent'])}</code></dd>
        <dt>{_escaped(labels['state'])}</dt><dd><span class="state state-{_escaped(state)}">{_escaped(state)}</span></dd>
        <dt>{_escaped(labels['change'])}</dt><dd>{_escaped(relation)}</dd>
      </dl>
      <h3>{_escaped(labels['findings'])}</h3>
      <p>{_escaped(finding)}</p>
      <dl class="metadata compact">
        <dt>{_escaped(labels['reason_codes'])}</dt><dd>{reason_text}</dd>
        <dt>{_escaped(labels['changed_fields'])}</dt><dd>{changed_text}</dd>
      </dl>
      <h3>{_escaped(labels['semantic_findings'])}</h3>
      {semantic_html}
      <div class="comparison">
        <div><h3>{_escaped(labels['canonical'])}</h3>{canonical_html}</div>
        <div><h3>{_escaped(labels['proposed'])}</h3>{proposed_html}</div>
      </div>
      <h3>{_escaped(labels['evidence'])}</h3>
      {evidence_html}
      <h3>{_escaped(labels['checklist'])}</h3>
      <ul>{checklist}</ul>
      <h3>{_escaped(labels['decision'])}</h3>
      <p>{_escaped(labels['decision_note'])}</p>
      {commands_html}
    </section>
    """


def _page_html(
    manifest: Mapping[str, Any],
    candidates: list[Mapping[str, Any]],
    *,
    canonical_records: Mapping[tuple[str, str], Mapping[str, Any]],
    normalized_source: str,
    context_characters: int,
    run: Path,
    source_path: Path,
    project: Path,
    semantic_findings: Mapping[str, list[Mapping[str, Any]]],
) -> str:
    language = str(manifest["language"])
    labels = _LABELS.get(language, _LABELS["en"])
    candidate_sections = []
    for candidate in candidates:
        key = (str(candidate.get("collection", "")), str(candidate.get("record_id", "")))
        candidate_sections.append(
            _candidate_html(
                candidate,
                canonical=canonical_records.get(key),
                normalized_source=normalized_source,
                context_characters=context_characters,
                labels=labels,
                run=run,
                source_path=source_path,
                project=project,
                language=language,
                semantic_findings=semantic_findings.get(
                    str(candidate.get("candidate_id", "")), []
                ),
            )
        )
    source = manifest["source"]
    return f"""<!doctype html>
<html lang="{_escaped(language)}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'">
  <meta name="referrer" content="no-referrer">
  <title>{_escaped(labels['title'])} — {_escaped(manifest['run_id'])}</title>
  <style>
    :root {{ color-scheme: light dark; font-family: system-ui, sans-serif; line-height: 1.5; }}
    body {{ max-width: 1180px; margin: 0 auto; padding: 2rem; }}
    .warning {{ border: 2px solid #b45309; background: #fffbeb; color: #451a03; padding: 1rem; border-radius: .5rem; }}
    .semantic {{ border-left: .35rem solid #b45309; padding: .7rem 1rem; background: color-mix(in srgb, canvas 92%, #f59e0b); }}
    .candidate {{ border-top: 3px solid #64748b; margin-top: 3rem; padding-top: 1.25rem; }}
    .metadata {{ display: grid; grid-template-columns: minmax(8rem, 13rem) 1fr; gap: .35rem 1rem; }}
    .metadata dt {{ font-weight: 700; }} .metadata dd {{ margin: 0; overflow-wrap: anywhere; }}
    .comparison {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 1rem; }}
    pre {{ white-space: pre-wrap; overflow-wrap: anywhere; border: 1px solid #94a3b8; border-radius: .35rem; padding: .75rem; background: color-mix(in srgb, canvas 95%, #64748b); }}
    .source-context {{ white-space: pre-wrap; overflow-wrap: anywhere; padding: .8rem; border-left: .3rem solid #2563eb; background: color-mix(in srgb, canvas 92%, #3b82f6); }}
    mark {{ background: #fde047; color: #1c1917; }}
    .locator {{ margin: .8rem 0 .3rem; color: #64748b; }}
    .state {{ font-weight: 700; }}
    .state-quarantined, .state-rejected {{ color: #b91c1c; }}
    .state-ready_for_review {{ color: #047857; }}
    .command pre {{ user-select: all; }}
    .empty, .ellipsis {{ color: #64748b; font-style: italic; }}
    @media (max-width: 760px) {{ .comparison {{ grid-template-columns: 1fr; }} .metadata {{ grid-template-columns: 1fr; }} }}
    @media print {{ .command {{ display: none; }} body {{ max-width: none; }} }}
  </style>
</head>
<body>
  <header>
    <h1>{_escaped(labels['title'])}</h1>
    <p class="warning"><strong>{_escaped(labels['private_warning'])}</strong></p>
    <p class="semantic">{_escaped(labels['semantic_warning'])}</p>
    <h2>{_escaped(labels['summary'])}</h2>
    <dl class="metadata">
      <dt>run ID</dt><dd><code>{_escaped(manifest['run_id'])}</code></dd>
      <dt>{_escaped(labels['source'])}</dt><dd><code>{_escaped(source.get('id', 'SRC-1'))}</code> · {_escaped(source.get('role', 'primary-book'))} · {_escaped(source['name'])} · <code>sha256:{_escaped(source['sha256'])}</code></dd>
      <dt>{_escaped(labels['language'])}</dt><dd>{_escaped(language)}</dd>
      <dt>{_escaped(labels['created'])}</dt><dd>{_escaped(manifest['created_at'])}</dd>
      <dt>{_escaped(labels['candidate_count'])}</dt><dd>{len(candidates)}</dd>
      <dt>{_escaped(labels['integrity'])}</dt><dd>{_escaped(labels['integrity_value'])}</dd>
    </dl>
  </header>
  {''.join(candidate_sections)}
</body>
</html>
"""


def _ensure_private_review_directory(project: Path) -> Path:
    private_root = project / ".reading-pack"
    review_root = private_root / "reviews"
    for directory in (private_root, review_root):
        try:
            if directory.is_symlink():
                raise ReadingPackError(
                    f"private review directory must not be a symlink: {directory}",
                    EXIT_IO,
                )
            directory.mkdir(mode=0o700, parents=False, exist_ok=True)
            if not directory.is_dir():
                raise ReadingPackError(
                    f"private review path is not a directory: {directory}", EXIT_IO
                )
            directory.chmod(0o700)
        except ReadingPackError:
            raise
        except OSError as exc:
            raise ReadingPackError(
                f"cannot prepare private review directory {directory}: {exc}", EXIT_IO
            ) from exc
    return review_root.resolve()


def _destination(
    project: Path,
    manifest: Mapping[str, Any],
    output_path: Path | None,
) -> Path:
    review_root = _ensure_private_review_directory(project)
    if output_path is None:
        fingerprint = hashlib.sha256(
            f"{manifest['run_id']}:{manifest['integrity_sha256']}".encode("utf-8")
        ).hexdigest()[:16]
        destination = review_root / f"candidate-review-{fingerprint}.html"
    elif output_path.is_absolute():
        destination = output_path
    else:
        destination = review_root / output_path
    try:
        if destination.parent.resolve() != review_root:
            raise ReadingPackError(
                "private review output must be a direct child of .reading-pack/reviews",
                EXIT_IO,
            )
    except OSError as exc:
        raise ReadingPackError(f"cannot resolve private review output: {exc}", EXIT_IO) from exc
    if destination.suffix.lower() != ".html" or not destination.name:
        raise ReadingPackError("private review output must be an .html file", EXIT_IO)
    if destination.is_symlink() or destination.exists():
        raise ReadingPackError(
            f"refusing to overwrite private review output: {destination}", EXIT_IO
        )
    return destination


def _write_private_html(path: Path, content: str) -> None:
    encoded = content.encode("utf-8")
    if len(encoded) > MAX_PRIVATE_REVIEW_BYTES:
        raise ReadingPackError(
            f"private review exceeds {MAX_PRIVATE_REVIEW_BYTES} bytes; select fewer candidates",
            EXIT_IO,
        )
    descriptor: int | None = None
    temporary_path: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        temporary_path = Path(temporary_name)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = None
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        # A hard link publishes the completed file atomically and, unlike
        # os.replace(), cannot overwrite a destination created after our
        # earlier existence check.
        os.link(temporary_path, path, follow_symlinks=False)
        temporary_path.unlink()
        temporary_path = None
        path.chmod(0o600)
    except OSError as exc:
        raise ReadingPackError(f"cannot write private review {path}: {exc}", EXIT_IO) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def render_private_candidate_review(
    project: Path,
    *,
    run: Path,
    source_path: Path,
    output_path: Path | None = None,
    candidate_ids: Iterable[str] | None = None,
    context_characters: int = DEFAULT_CONTEXT_CHARACTERS,
    semantic_review_path: Path | None = None,
) -> Path:
    """Render a static, local candidate review page without changing run state.

    ``candidate_ids`` limits what is displayed; omission means "show all", not
    "accept all".  The page provides only per-candidate commands with explicit
    IDs.  Relative output paths are interpreted below ``.reading-pack/reviews``
    and nested or external destinations are rejected.
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
    project = project.resolve()
    if not (project / CONFIG_NAME).is_file():
        raise ReadingPackError(f"{CONFIG_NAME} not found in {project}", EXIT_IO)
    run = run.resolve()
    source_path = source_path.resolve()
    manifest = load_candidate_run(run)

    semantic_by_candidate: dict[str, list[Mapping[str, Any]]] = {}
    if semantic_review_path is not None:
        semantic_review = load_semantic_review(semantic_review_path.resolve())
        semantic_run = semantic_review["run"]
        if (
            semantic_run["run_id"] != manifest["run_id"]
            or semantic_run["integrity_sha256"] != manifest["integrity_sha256"]
        ):
            raise ReadingPackError(
                "semantic review is stale or does not match the candidate run"
            )
        available_candidates = {
            candidate["candidate_id"]: candidate for candidate in manifest["candidates"]
        }
        for item in semantic_review["findings"]:
            candidate = available_candidates.get(item["candidate_id"])
            if candidate is None:
                raise ReadingPackError(
                    "semantic review references a candidate outside the candidate run"
                )
            evidence_ids = {
                evidence["id"] for evidence in candidate.get("evidence_refs", [])
            }
            if not set(item["evidence_ref_ids"]) <= evidence_ids:
                raise ReadingPackError(
                    "semantic review references evidence outside its candidate"
                )
            semantic_by_candidate.setdefault(item["candidate_id"], []).append(item)

    # This both re-derives the authorized text and verifies every stored span.
    # No extracted text sidecar or caller-provided excerpt is accepted.
    normalized_source, _ = _verify_source_and_evidence(manifest, source_path)

    config = load_config(project)
    language = manifest["language"]
    configured_languages = config.get("languages", [])
    if language not in configured_languages:
        raise ReadingPackError("candidate language is not configured in the project")
    project_data_by_lang = {
        lang: load_language_data(project, lang) for lang in configured_languages
    }
    canonical_data = project_data_by_lang[language]
    binding = manifest["canonical"]
    if (
        _value_hash(canonical_data) != binding["data_sha256"]
        or _value_hash(project_data_by_lang) != binding["project_data_sha256"]
    ):
        raise ReadingPackError(
            "canonical data changed after candidate creation; create a fresh run before review"
        )

    available = {
        candidate["candidate_id"]: candidate for candidate in manifest["candidates"]
    }
    if candidate_ids is None:
        selected = list(manifest["candidates"])
    else:
        requested = list(dict.fromkeys(candidate_ids))
        if not requested:
            raise ReadingPackError("at least one candidate ID is required")
        if any(not isinstance(identifier, str) or identifier not in available for identifier in requested):
            raise ReadingPackError("one or more candidate IDs were not found")
        selected = [available[identifier] for identifier in requested]

    destination = _destination(project, manifest, output_path)
    page = _page_html(
        manifest,
        selected,
        canonical_records=_canonical_records(canonical_data),
        normalized_source=normalized_source,
        context_characters=context_characters,
        run=run,
        source_path=source_path,
        project=project,
        semantic_findings=semantic_by_candidate,
    )
    _write_private_html(destination, page)
    try:
        mode = stat.S_IMODE(destination.stat().st_mode)
    except OSError as exc:
        raise ReadingPackError(f"cannot verify private review permissions: {exc}", EXIT_IO) from exc
    if mode != 0o600:
        raise ReadingPackError("private review permissions are not owner-only", EXIT_IO)
    return destination
