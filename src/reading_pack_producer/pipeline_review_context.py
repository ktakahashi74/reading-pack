"""Bounded neighboring source text for reviewing chapter-level candidates."""
from __future__ import annotations

import copy
import json
import re

from reading_pack.errors import ReadingPackError
from .pipeline_generation import chapter_review_scope


def candidate_review_context(runner, source: dict, candidates: list[dict],
                             canonical: dict, failures: list[dict]) -> dict:
    """Give the independent reviewer the candidate's linked frozen evidence.

    Generation windows are not evidence boundaries for a correction that
    distinguishes manuscript and supplement. Retrieve exact locators, original
    findings and explicitly referenced Pack records, retaining document roles
    and the existing audit retrieval cap. This grants no acceptance or authority
    to supplements, and never exposes questions or reader answers.
    """
    from .pipeline_audit import evidence_context, _slice
    records = {r['id']: r for rows in canonical.values() if isinstance(rows, list)
               for r in rows if isinstance(r, dict) and 'id' in r}
    changed = {c['record']['id'] for c in candidates}
    content = json.dumps([c['record'] for c in candidates], ensure_ascii=False)
    related = {rid for rid in records if rid not in changed and
               re.search(r'(?<![A-Za-z0-9_.:-])' + re.escape(rid) + r'(?![A-Za-z0-9_-]|[.:][A-Za-z0-9])', content)}
    projected = copy.deepcopy(canonical)
    for c in candidates:
        collection, record = c['collection'], copy.deepcopy(c['record'])
        old = records.get(record['id'], {})
        record['source_locations'] = list(dict.fromkeys(
            old.get('source_locations', []) + record.get('source_locations', [])))
        rows = projected.setdefault(collection, [])
        rows[:] = [r for r in rows if r.get('id') != record['id']] + [record]
    findings = [f for f in failures if not f.get('record_ids') or changed.intersection(f['record_ids'])]
    findings = findings + [{'record_ids': sorted(changed | related), 'evidence': []}]
    context = evidence_context(runner, projected, findings,
                              maximum=runner.recipe.get('audit_context_characters', 32000))
    supporting = []
    for sample in context['samples']:
        intervals = [(sample['text_start'], sample['text_start'] + len(sample['text']))]
        if sample['source_id'] == source['source_id']:
            left, right = source['text_start'], source['text_start'] + len(source['text'])
            intervals = [part for a, b in intervals for part in
                         ((a, min(b, left)), (max(a, right), b)) if part[0] < part[1]]
        supporting.extend(_slice(runner.chunks, sample['source_id'], a, b) for a, b in intervals)
    payload = {'source': source, 'candidates': candidates}
    if supporting:
        payload['supporting_sources'] = supporting
    if supporting or not context['retrieval']['complete']:
        payload['source_context'] = {**context['retrieval'],
            'instruction': 'Review against all supplied source spans and their individual roles. '
                           'A supplement does not automatically override the manuscript. '
                           'A cited correction must be established from the relevant passage. '
                           'If missing context prevents verification, use uncertain. '
                           'Related Pack records are context, not source evidence or author approval.'}
    if related:
        payload['related_records'] = [records[rid] for rid in sorted(related)]
    return payload


def candidate_review_batches(runner, chunk: dict, candidates: list[dict], canonical: dict,
                             failures: list[dict]):
    """Split overfull review batches before dispatch, retaining the source cap."""
    size = runner.recipe.get('review_batch_size', 8)
    pending = [(offset, candidates[offset:offset+size]) for offset in range(0,len(candidates),size)]
    while pending:
        offset, batch = pending.pop(0)
        source = chapter_review_source(chunk,batch,canonical,runner.chunks,
                                       runner.recipe.get('chapter_review_context_characters',120000))
        payload = candidate_review_context(runner,source,batch,canonical,failures)
        overfull = any(reason.startswith('context limit:') for reason in
                       payload.get('source_context',{}).get('unresolved',[]))
        if overfull and len(batch)>1:
            middle=len(batch)//2
            pending[:0]=[(offset,batch[:middle]),(offset+middle,batch[middle:])]
            continue
        yield offset,batch,payload


def chapter_review_source(chunk: dict, candidates: list[dict], canonical: dict,
                          chunks: list[dict], maximum: int) -> dict:
    """Expand only changed chapter content, within the same frozen document.

    A chapter may cross generation/repair windows. The reviewer still has to
    establish every new statement from the supplied text; expansion is not an
    approval and does not claim to include the whole chapter.
    """
    scope = chapter_review_scope(candidates, canonical)
    if not any(s['fields_requiring_source_review'] for s in scope.values()):
        return copy.deepcopy(chunk)
    if len(chunk['text']) >= maximum:
        return copy.deepcopy(chunk)
    source = sorted((c for c in chunks if c['source_id'] == chunk['source_id']),
                    key=lambda c: c['start'])
    if not source:
        raise ReadingPackError('chapter review source is missing')
    for index, item in enumerate(source):
        if (item['source_sha256'] != chunk['source_sha256'] or item['role'] != chunk['role']
                or (index and source[index - 1]['end'] != item['start'])):
            raise ReadingPackError('chapter review source is inconsistent')
    first, last = source[0]['start'], source[-1]['end']
    extra = maximum - len(chunk['text'])
    left = max(first, chunk['text_start'] - extra // 2)
    right = min(last, left + maximum)
    left = max(first, right - maximum)
    pieces = []
    cursor = left
    for item in source:
        start, end = max(left, item['start']), min(right, item['end'])
        if start >= end:
            continue
        value = item['text'][start - item['text_start']:end - item['text_start']]
        if start != cursor or len(value) != end - start:
            raise ReadingPackError('chapter review source has a gap')
        pieces.append(value)
        cursor = end
    text = ''.join(pieces)
    if cursor != right or chunk['text'] != text[chunk['text_start'] - left:
                                             chunk['text_start'] - left + len(chunk['text'])]:
        raise ReadingPackError('chapter review source does not preserve original text')
    return {**copy.deepcopy(chunk), 'id': chunk['id'] + '-chapter-review',
            'start': left, 'end': right, 'text_start': left, 'text': text}
