"""Source-bound deterministic enrichments; never infer content or approval."""
from __future__ import annotations

import copy
import hashlib
import re
from difflib import SequenceMatcher

from reading_pack.errors import ReadingPackError
from reading_pack.schema_validation import require_structure


def freeze_inputs(value, text, units):
    if value is None:
        return None
    require_structure("delivery-mechanical.schema.json", value, label='mechanical inputs')
    if value['source_sha256'] != hashlib.sha256(text.encode('utf-8')).hexdigest():
        raise ReadingPackError('mechanical inputs source hash mismatch')
    sections = {s['id']: (u['id'], s) for u in units for s in u['sections']}
    seen = set()
    for item in value.get('section_pages', []):
        sid = item['section_id']
        if sid not in sections or sid in seen:
            raise ReadingPackError('unknown or duplicate page section: ' + sid)
        seen.add(sid)
    seen = {}; identities = set()
    for entity in value.get('names', []):
        if entity['entity_id'] in identities:
            raise ReadingPackError('duplicate name entity id')
        identities.add(entity['entity_id'])
        if entity['canonical'] not in text:
            raise ReadingPackError('canonical name must occur in the frozen source')
        for name in [entity['canonical'], *entity['aliases']]:
            if name in seen:
                raise ReadingPackError('ambiguous or duplicate name alias: ' + name)
            seen[name] = entity['entity_id']
    return copy.deepcopy(value)


def enrich(data, plan):
    """Apply only supplied, source-bound names and section starts before evaluation."""
    value = plan.get('mechanical_inputs') or {}
    by_section = {s['id']: (u['id'], s) for u in plan['units'] for s in u['sections']}
    if value.get('section_pages'):
        data['section_pages'] = [{'chapter_id': by_section[i['section_id']][0],
            'section_id': i['section_id'], 'title': by_section[i['section_id']][1]['title'],
            'printed_page': i['printed_page']} for i in value['section_pages']]
    aliases = {a: e['canonical'] for e in value.get('names', []) for a in e['aliases']}
    changes = []
    for record in data['names']:
        name = record['name']
        if name in aliases and aliases[name] != name:
            record['name'] = aliases[name]
            record['aliases'] = sorted(set(record.get('aliases', []) + [name]))
            changes.append({'record_id': record['id'], 'original': name, 'canonical': record['name']})
    return changes


def name_diagnostics(data, plan, generated):
    """Report applied dictionary mappings and unconfirmed spelling candidates."""
    changes = []
    from .delivery_fresh import module_records
    originals = {r['id']: r['name'] for u in plan['units']
                 for r in module_records(u, generated.get(u['id']))['names']} if plan.get('content_mode') == 'fresh' else {}
    for r in data['names']:
        if r['id'] in originals and originals[r['id']] != r['name']:
            changes.append({'record_id': r['id'], 'original': originals[r['id']], 'canonical': r['name']})
    names = sorted({r['name'] for r in data['names']})
    candidates = []
    # Bounded diagnostic only: never merge people by spelling similarity.
    for i, a in enumerate(names[:2000]):
        for b in names[i+1:2000]:
            if min(len(a), len(b)) >= 4 and abs(len(a)-len(b)) <= 3 and SequenceMatcher(None, a, b).ratio() >= .8:
                candidates.append({'left': a, 'right': b, 'status': 'unconfirmed'})
                if len(candidates) >= 200:
                    return {'applied': changes, 'unconfirmed_candidates': candidates, 'truncated': True}
    return {'applied': changes, 'unconfirmed_candidates': candidates, 'truncated': len(names)>2000}


def quote_check(text, quote, start, end):
    """Recover only a unique whitespace-equivalent substring in the declared range."""
    segment = text[start:end]
    result = {'exact_match': bool(quote.strip()) and quote in segment, 'original_quote': quote,
              'status': 'exact', 'effective_quote': quote, 'restored': False}
    if result['exact_match']:
        return result
    positions = [i for i, char in enumerate(segment) if not char.isspace()]
    compact = ''.join(segment[i] for i in positions)
    needle = re.sub(r'\s', '', quote)
    offset = compact.find(needle) if needle else -1
    if offset >= 0 and compact.find(needle, offset+1) < 0:
        first, last = positions[offset], positions[offset+len(needle)-1]+1
        result.update(status='whitespace_restored', effective_quote=segment[first:last],
                      restored=True, start=start+first, end=start+last)
    elif offset >= 0:
        result['status'] = 'ambiguous_whitespace_match'
    elif needle and quote in text:
        result['status'] = 'exact_outside_declared_range'
    else:
        result['status'] = 'unmatched'
    return result
