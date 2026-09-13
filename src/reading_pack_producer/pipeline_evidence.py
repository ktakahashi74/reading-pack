"""Source-bound spans: workers select IDs, the controller retrieves quotations."""
from __future__ import annotations

import copy

from reading_pack.errors import ReadingPackError
from .work_ledger import artifact_hash


def source_spans(chunk: dict) -> list[dict]:
    text = chunk['text']
    offset = chunk['text_start']
    spans, start = [], 0
    while start < len(text):
        end = min(start + 480, len(text))
        if end < len(text):
            boundary = max(text.rfind(mark, start + 240, end) for mark in ('\n', '。', '. '))
            if boundary >= 0:
                end = boundary + 1
            if len(text) - end < 8:
                end = len(text)
        value = {'source_id': chunk['source_id'], 'source_sha256': chunk['source_sha256'],
                 'start': offset + start, 'end': offset + end, 'text': text[start:end]}
        spans.append({'id': 'SPAN-' + artifact_hash(value)[:24], **value})
        start = end
    return spans


def worker_payload(payload: dict) -> tuple[dict, dict]:
    """Send each source passage once, preserving offsets and document roles."""
    value = copy.deepcopy(payload)
    spans = []
    chunks = ([value['source']] if 'source' in value else value.get('samples', []))
    chunks = chunks + value.get('supporting_sources', [])
    for chunk in chunks:
        units = source_spans(chunk)
        spans.extend(units)
        chunk['span_ids'] = [unit['id'] for unit in units]
        del chunk['text']
    if 'case' in value or 'evaluation_cases' in value:
        # Graders receive only the frozen case evidence, never additional manuscript text.
        spans = [{'id': ref['span_id'], 'source_id': ref['source_id'],
                  'source_sha256': ref['source_sha256'], 'start': ref['start'],
                  'end': ref['end'], 'text': ref['quote']} for case in ([value['case']] if 'case' in value else [entry['case'] for entry in value['evaluation_cases']]) for ref in case['evidence']]
    registry = {span['id']: span for span in spans}
    if registry:
        value['evidence_spans'] = list(registry.values())
    return value, registry


def resolve_references(value, registry: dict):
    """Resolve only IDs in this request; preserve exact text and source location."""
    if isinstance(value, dict):
        if 'span_id' in value:
            span = registry.get(value['span_id'])
            if span is None or value.get('source_id', span['source_id']) != span['source_id']:
                raise ReadingPackError('worker selected an unknown or misattributed source span')
            return {'source_id': span['source_id'], 'span_id': span['id'],
                    'source_sha256': span['source_sha256'], 'start': span['start'],
                    'end': span['end'], 'quote': span['text']}
        return {key: resolve_references(item, registry) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve_references(item, registry) for item in value]
    return value


def benchmark_contract(schema: dict) -> str:
    """Render numerical instructions from the actual benchmark result schema."""
    parts = []
    for split, cases in schema['properties'].items():
        requirements = cases['items']['properties']['requirements']
        parts.append(f"{split}: {cases['minItems']} to {cases['maxItems']} question(s), "
                     f"each with {requirements['minItems']} to {requirements['maxItems']} atomic requirements.")
    return ' '.join(parts)
