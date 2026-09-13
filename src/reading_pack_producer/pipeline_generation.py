"""Deterministic generation units and author-input protection."""
from __future__ import annotations

import copy
from pathlib import Path

from reading_pack.project import load_language_data
from reading_pack_review.author_input import COLLECTION_MODULES, FIELD_MODULES, load_author_input_state
from .candidates import ALLOWED_FIELDS, CHAPTER_STRUCTURAL_FIELDS


def author_contract(project: Path, language: str) -> dict:
    state = load_author_input_state(project)
    modules = state['languages'].get(language, {}).get('modules', {}) if state else {}
    from reading_pack_review.author_review import load_author_review_state, _module_binding, has_draft_permission, review_overrides_for_author_input
    review = load_author_review_state(project)
    result = {name: {'mode': value['mode'], 'protected_ids': value['provided_record_ids']} for name, value in modules.items()}
    for module, collection in COLLECTION_MODULES.items():
        rule = modules.get(module)
        if not rule or rule['mode'] not in {'provided', 'augment'}:
            continue
        binding = _module_binding(state, language, module)
        expected = review_overrides_for_author_input(review, language=language, module=module,
            module_state_sha256=binding, initial_hashes=rule['provided_record_hashes'])
        allowed = sorted(rid for rid, digest in expected.items() if has_draft_permission(review, language, collection, binding, rid, digest))
        if allowed:
            result[module]['draft_revision_ids'] = allowed
    return result


def record_protected(rule: dict, record_id: str) -> bool:
    if rule['mode'] == 'omit':
        return True
    if record_id in rule.get('draft_revision_ids', []):
        return False
    return rule['mode'] == 'provided' or record_id in rule['protected_ids']


def protection_reason(item: dict, canonical: dict, contract: dict) -> str | None:
    collection, record = item['collection'], item['record']
    module = next((m for m, c in COLLECTION_MODULES.items() if c == collection), collection)
    rule = contract.get(module, {'mode': 'generate', 'protected_ids': []})
    if rule['mode'] in {'provided', 'omit'} and record_protected(rule, record.get('id')):
        return 'author_input_' + rule['mode']
    if record.get('id') in rule['protected_ids'] and record_protected(rule, record.get('id')):
        return 'author_input_protected_record'
    if collection == 'chapters':
        old = next((c for c in canonical['chapters'] if c['id'] == record.get('id')), None)
        if old is None:
            return 'chapter_structure_is_fixed'
        for field in CHAPTER_STRUCTURAL_FIELDS:
            if record.get(field) != old.get(field):
                return 'chapter_structure_is_fixed'
        for name, (field, empty) in FIELD_MODULES.items():
            rule = contract.get(name, {'mode': 'generate', 'protected_ids': []})
            if (rule['mode'] in {'provided', 'omit'} or record['id'] in rule['protected_ids']) and record.get(field, empty) != old.get(field, empty):
                return 'author_input_protected_' + field
    return None


def compact_canonical(canonical: dict) -> dict:
    """Omit controller provenance from generation context, never content."""
    return {collection: [{k: copy.deepcopy(v) for k, v in record.items() if k in fields}
                         for record in canonical.get(collection, [])]
            for collection, fields in ALLOWED_FIELDS.items()}


def chapter_review_scope(candidates: list[dict], canonical: dict) -> dict:
    """Attest unchanged structure; new summary/term content still needs evidence."""
    from .work_ledger import artifact_hash
    chapters = {c['id']: c for c in canonical['chapters']}
    scopes = {}
    for candidate in candidates:
        if candidate['collection'] != 'chapters':
            continue
        record = candidate['record']; old = chapters.get(record['id'])
        if old is None or any(record.get(k) != old.get(k) for k in CHAPTER_STRUCTURAL_FIELDS):
            continue
        scopes[candidate['candidate_id']] = {
            'unchanged_fields': list(CHAPTER_STRUCTURAL_FIELDS),
            'unchanged_fields_sha256': artifact_hash({k: old.get(k) for k in CHAPTER_STRUCTURAL_FIELDS}),
            'fields_requiring_source_review': sorted(k for k in ALLOWED_FIELDS['chapters']
                if k not in CHAPTER_STRUCTURAL_FIELDS and k != 'status' and record.get(k) != old.get(k)),
        }
    return scopes


def generation_context(canonical: dict, contract: dict) -> tuple[dict, dict]:
    """Retain protected IDs for references; omit their uneditable prose only here."""
    from reading_pack.hashing import semantic_hash
    result = compact_canonical(canonical)
    omitted = {}
    for module, collection in COLLECTION_MODULES.items():
        if collection == 'chapters':
            continue  # Chapter structure and field protections need whole records.
        rule = contract.get(module, {'mode': 'generate', 'protected_ids': []})
        for index, record in enumerate(result[collection]):
            if record_protected(rule, record['id']):
                omitted.setdefault(collection, []).append({'id': record['id'], 'sha256': semantic_hash(record)})
                result[collection][index] = {'id': record['id']}
    return result, omitted


def generation_chunks(chunks: list[dict], size: int) -> list[dict]:
    result = []
    for chunk in chunks:
        if chunk['end'] - chunk['start'] <= size:
            result.append(copy.deepcopy(chunk))
            continue
        for start in range(chunk['start'], chunk['end'], size):
            end = min(start + size, chunk['end'])
            left = max(chunk['text_start'], start - 500)
            right = min(chunk['text_start'] + len(chunk['text']), end + 500)
            result.append({**chunk, 'id': f'{chunk["source_id"]}-{start}-generation',
                           'start': start, 'end': end, 'text_start': left,
                           'text': chunk['text'][left-chunk['text_start']:right-chunk['text_start']]})
    return result


def hydrate_chapter_fields(item: dict, canonical: dict) -> list[str]:
    """Restore omitted immutable metadata, never replace a proposed value."""
    if item['collection'] != 'chapters':
        return []
    record = item['record']
    old = next((c for c in canonical['chapters'] if c['id'] == record.get('id')), None)
    fields = []
    if old:
        for field in CHAPTER_STRUCTURAL_FIELDS:
            if field not in record and field in old:
                record[field] = copy.deepcopy(old[field])
                fields.append(field)
    return fields


def dependency_closed_candidates(accepted: list[dict], canonical: dict) -> tuple[list[dict], list[dict]]:
    """Keep the maximal accepted subset whose canonical references can resolve."""
    remaining = list(accepted)
    rejected = []
    while True:
        known = {collection: {record['id'] for record in canonical.get(collection, [])}
                 for collection in ALLOWED_FIELDS}
        for candidate in remaining:
            known[candidate['collection']].add(candidate['record']['id'])
        keep = []
        for candidate in remaining:
            collection, record = candidate['collection'], candidate['record']
            missing = []
            if collection == 'claims' and record.get('certainty_id') and record['certainty_id'] not in known['certainty']:
                missing.append(record['certainty_id'])
            if collection == 'misreadings':
                missing.extend(rid for rid in record.get('claim_ids', []) if rid not in known['claims'])
            if missing:
                rejected.append({'record_id': record['id'], 'missing_ids': missing})
            else:
                keep.append(candidate)
        if len(keep) == len(remaining):
            return keep, rejected
        remaining = keep
