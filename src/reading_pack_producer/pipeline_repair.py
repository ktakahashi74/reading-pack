"""Route repairs to actual source findings and editable neighboring records."""
from __future__ import annotations

import copy
import re

from reading_pack_review.author_input import COLLECTION_MODULES
from .candidates import normalize_text
from .pipeline_generation import compact_canonical, protection_reason, record_protected


AUDIT_ERROR_SUMMARY = 'source audit found attribution or invented-record errors'


def coverage_repair_targets(canonical: dict, contract: dict, failures: list[dict]) -> set[str]:
    """Select the editable existing records explicitly named by coverage findings.

    A pack-level finding can name its suitable destination in prose rather than
    record_ids. Mixed batches must not suppress that destination. Only when no
    destination is named does the existing missing-coverage fallback apply.
    This selection never grants a protected-record override.
    """
    records = {r['id']: (collection, r) for collection, rows in compact_canonical(canonical).items() for r in rows}
    editable = {rid for rid, (collection, record) in records.items()
                if not protection_reason({'collection':collection,'record':record}, canonical, contract)}
    selected = set()
    for finding in failures:
        if finding.get('criterion') != 'required_coverage' and finding.get('category') != 'missing_coverage':
            continue
        named = {rid for rid in records if re.search(
            r'(?<![A-Za-z0-9_.:-])' + re.escape(rid) + r'(?![A-Za-z0-9_-]|[.:][A-Za-z0-9])', finding.get('reason',''))}
        selected.update(named & editable)
        if not named and not any(f.get('record_ids') for f in failures):
            selected.update(editable)
    return selected



def repair_findings_with_requirements(failures: list[dict], development: list[dict]) -> list[dict]:
    """Retain frozen development evidence when routing a coverage repair.

    Adjudication evidence establishes the defect but need not contain the
    omitted fact. The original requirement's source evidence identifies that
    fact, which can be in a distant footnote or supplement. Never infer a
    requirement from prose, consult holdout, or alter the adjudication itself.
    """
    requirements = {f"development:{case['id']}:{index}": case
                    for case in development for index in range(len(case['requirements']))}
    result = copy.deepcopy(failures)
    for finding in result:
        selected = [rid for rid in finding.get('requirement_ids', []) if rid in requirements]
        if not selected:
            continue
        references = finding.setdefault('evidence', [])
        for rid in selected:
            for reference in requirements[rid]['evidence']:
                if reference not in references:
                    references.append(copy.deepcopy(reference))
        finding['repair_requirement_ids'] = selected
    return result


def is_audit_summary(finding: dict) -> bool:
    return (finding.get('category') == 'misattributed' and finding.get('reason') == AUDIT_ERROR_SUMMARY
            and not finding.get('record_ids') and not finding.get('evidence'))


def local_repair_failures(failures: list[dict], chunk: dict) -> list[dict]:
    selected = []
    for finding in failures:
        scope = finding.get('source_scope')
        adjudicated = finding.get('classification') in {'blocking','unresolved'} and finding.get('evidence')
        if scope and not adjudicated and (scope['source_id'] != chunk['source_id'] or
                      scope['end'] <= chunk['start'] or scope['start'] >= chunk['end']):
            continue
        if not finding.get('evidence') or any(
                ref['source_id'] == chunk['source_id'] and normalize_text(ref['quote']) in normalize_text(chunk['text'])
                for ref in finding['evidence']):
            selected.append(finding)
    # The summary remains a quality failure in the report. Repair workers receive
    # actionable findings, not an instruction to search for unspecified errors.
    return [f for f in selected if not is_audit_summary(f)]


def audit_error_guidance(audit: dict, chunk: dict, canonical: dict) -> list[dict]:
    messages = audit['source_attribution_errors'] + audit['invented_record_ids']
    if not messages:
        return []
    ids = {r['id'] for records in compact_canonical(canonical).values() for r in records}
    targets = sorted(rid for rid in ids if any(
        re.search(r'(?<![A-Za-z0-9.-])' + re.escape(rid) + r'(?![A-Za-z0-9.-])', message)
        for message in messages))
    represented = {rid for finding in audit.get('findings', [])
                   if finding['category'] == 'misattributed' and finding.get('evidence')
                   for rid in finding['record_ids']}
    # Existing source-backed attribution findings already provide narrower
    # repair locations. Aggregate arrays remain in the acceptance gate either
    # way; only errors lacking such guidance need a whole source interval.
    if targets and set(targets) <= represented and all(any(
            re.search(r'(?<![A-Za-z0-9.-])' + re.escape(rid) + r'(?![A-Za-z0-9.-])', message)
            for rid in targets) for message in messages):
        return []
    return [{'category': 'misattributed', 'record_ids': targets,
             'reason': 'Resolve the source audit details in source_audit_errors; preserve protected author records.',
             'evidence': [], 'source_scope': {key: chunk[key] for key in ('source_id', 'start', 'end')},
             'source_audit_errors': {key: copy.deepcopy(audit[key]) for key in ('source_attribution_errors', 'invented_record_ids')}}]


def protected_repair_context(canonical: dict, contract: dict, failures: list[dict]) -> tuple[set[str], list[dict]]:
    indexed = {r['id']: (collection, r) for collection, records in compact_canonical(canonical).items() for r in records}
    protected, related = [], set()
    targets = {rid for finding in failures for rid in finding.get('record_ids', [])}
    modules = {collection: module for module, collection in COLLECTION_MODULES.items()}
    for rid in sorted(targets & indexed.keys()):
        collection, record = indexed[rid]
        rule = contract.get(modules[collection], {'mode': 'generate', 'protected_ids': []})
        if not record_protected(rule, rid):
            continue
        protected.append({'collection': collection, 'record': copy.deepcopy(record)})
        records = [record] + [indexed[cid][1] for cid in record.get('claim_ids', []) if cid in indexed]
        chapters = {cid for r in records for cid in r.get('chapter_ids', [])}
        chapters.update(r['chapter_id'] for r in records if r.get('chapter_id'))
        for candidate_id, (kind, candidate) in indexed.items():
            if ((kind == 'chapters' and candidate_id in chapters) or
                    (kind in {'glossary', 'names'} and candidate.get('chapter_id') in chapters)):
                if not protection_reason({'collection': kind, 'record': candidate}, canonical, contract):
                    related.add(candidate_id)
    return related, protected
