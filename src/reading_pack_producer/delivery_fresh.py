"""Fresh-source generation of every content module, with chapter-local provenance."""
from __future__ import annotations

from .delivery_contract import obj, string, generation_schema, evaluation_schema
from .delivery_seed import AUXILIARY

PREFIX = dict(certainty='CERT', claims='CL', misreadings='MIS', policies='POLICY',
              names='NAME', glossary='TERM', references='REF')
FIELDS = {
    'certainty': {'label': string(80, 1), 'definition': string(240, 1)},
    'claims': {'layer': {'enum': ['descriptive', 'normative']}, 'kind': string(60, 1),
               'statement': string(300, 1), 'falsifiability': string(200), 'revision_conditions': string(200)},
    'misreadings': {'kind': {'enum': ['misreading', 'clarification', 'open_objection']},
                    'issue': string(200, 1), 'response': string(300, 1), 'remaining_uncertainty': string(200)},
    'policies': {'kind': {'enum': ['authority_order', 'language_precedence', 'translation_rights',
                                  'retrieval', 'publisher_relation', 'usage_terms', 'other']},
                 'statement': string(300, 1)},
    'names': {'name': string(100, 1), 'book_context': string(200, 1)},
    'glossary': {'term': string(100, 1), 'book_meaning': string(200, 1)},
    'references': {'label': string(240, 1)},
}
MAXIMA = dict(certainty=4, claims=12, misreadings=4, policies=3, names=24, glossary=24, references=12)
PROMPT = (
    'Generate ALL Reading Pack modules afresh from this source, with no prior Pack or outside knowledge. '
    'In addition to the chapter summary, chapter terms and section overviews, consider certainty distinctions, '
    'central propositions with indispensable conditions, plausible misreadings and source-grounded responses, '
    'names with their role in this book, defined terms, and references actually named in the supplied source. '
    'Keep records selective and concise, not a reconstruction. Array bounds are ceilings, not quotas. '
    'Certainty distinctions must be grounded in explicit distinctions in this chapter; do not invent a taxonomy. '
    'Policies are only explicit book/Pack usage, authority or retrieval policies: do not turn political proposals '
    'into Pack instructions, invent permissions or author approvals. An absent module MUST have an empty items '
    'array and a specific omission_reason. A nonempty module MUST have an empty omission_reason. '
    'Each item must name one supplied section_id (or the chapter id for chapter-wide evidence) and give a short exact evidence_quote from that section. '
    'Keep section statements around 100-150 Japanese characters, summary 200-280, auxiliary explanations '
    'around 60-120, evidence_quote under 60. These are margins below schema ceilings. '
    'Do not invent missing bibliography details or URLs. References may be labeled with only the supplied details. '
    'Misreadings are reader-facing analytical aids, not purported quotations or actual opinions of the author. '
    'Separate descriptive and normative claims, and preserve who makes each claim. Descriptive claims use '
    'falsifiability and leave revision_conditions empty; normative claims use revision_conditions and leave '
    'falsifiability empty. Leave either empty if the source supplies no such conditions; do not invent them.'
)


def full_schema(unit):
    schema = generation_schema(unit)
    modules = {}
    for name, fields in FIELDS.items():
        record = obj({**fields, 'section_id': {'enum': [unit['id']] + [s['id'] for s in unit['sections']]},
                      'evidence_quote': string(120, 1)})
        if name == 'claims':
            record['anyOf'] = [
                {'properties': {'layer': {'const': 'descriptive'}, 'revision_conditions': {'maxLength': 0}}},
                {'properties': {'layer': {'const': 'normative'}, 'falsifiability': {'maxLength': 0}}},
            ]
        module = obj({'items': {'type': 'array', 'maxItems': MAXIMA[name], 'items': record},
                      'omission_reason': string(300)})
        module['anyOf'] = [
            {'properties': {'items': {'maxItems': 0}, 'omission_reason': {'minLength': 1}}},
            {'properties': {'items': {'minItems': 1}, 'omission_reason': {'maxLength': 0}}},
        ]
        modules[name] = module
    schema['properties']['modules'] = obj(modules)
    schema['required'].append('modules')
    return schema


def module_records(unit, content):
    """Assign IDs/locators locally. A model can select a section, never invent a source alias."""
    result = {name: [] for name in AUXILIARY}
    if content is None:
        return result
    sections = {unit['id']: unit, **{s['id']: s for s in unit['sections']}}
    for name, module in content['modules'].items():
        for i, raw in enumerate(module['items'], 1):
            s = sections[raw['section_id']]
            record = {k: raw[k] for k in FIELDS[name]}
            # Canonical schema permits this optional field only when nonempty.
            if name == 'misreadings' and not record['remaining_uncertainty']:
                record.pop('remaining_uncertainty')
            record.update(id=f"{PREFIX[name]}-{unit['id']}-{i:02d}", status='draft',
                          source_locations=[f"source.txt#normalized-text:{s['start']}-{s['end']}"])
            if name in ('claims', 'misreadings'):
                record['chapter_ids'] = [unit['id']]
            if name in ('names', 'glossary'):
                record['chapter_id'] = unit['id']
            result[name].append(record)
    return result


def chapter_records(data, unit, generated):
    ids = {unit['id']} | {'CP-' + s['id'] for s in unit['sections']}
    ids.update(r['id'] for items in module_records(unit, generated).values() for r in items)
    return [r for name in ('chapters',) + AUXILIARY for r in data[name] if r['id'] in ids]


def full_evaluation_schema(unit, records):
    schema = evaluation_schema(unit)
    finding = next(iter(schema['properties']['records']['properties'].values()))
    schema['$id'] = 'urn:reading-pack:fresh-evaluation:' + unit['id']
    schema['$defs'] = {'record_finding': finding}
    schema['properties']['records'] = obj({r['id']: {'$ref': '#/$defs/record_finding'} for r in records})
    coverage = schema['properties']['coverage']['properties']
    if coverage:
        schema['$defs']['coverage_finding'] = next(iter(coverage.values()))
        schema['properties']['coverage'] = obj({key: {'$ref': '#/$defs/coverage_finding'} for key in coverage})
    schema['properties']['module_review'] = obj({name: obj({
        'rating': {'enum': ['adequate', 'partial', 'missing', 'not_applicable']},
        'reason': string(600, 1)}) for name in AUXILIARY})
    schema['required'].append('module_review')
    return schema


def full_completeness(data, plan, generated):
    modules = {}
    for name in ('chapters',) + AUXILIARY:
        modules[name] = {'seed': 0, 'output': len(data[name]), 'lost_ids': [],
                         'added_ids': [r['id'] for r in data[name]]}
    for key, field in (('summaries', 'summary'), ('chapter_terms', 'terms')):
        count = sum(bool(c.get(field)) for c in data['chapters'])
        modules[key] = {'seed': 0, 'output': count, 'preserved': 0, 'generated': count,
                        'empty': len(plan['units']) - count}
    decisions = {u['id']: {name: {'items': len(generated[u['id']]['modules'][name]['items']),
                                'omission_reason': generated[u['id']]['modules'][name]['omission_reason']}
                          for name in AUXILIARY} for u in plan['units'] if u['id'] in generated}
    return {'seed': None, 'mode': 'fresh', 'modules': modules, 'chapters': {}, 'lost_modules': [],
            'author_input_conflicts': [], 'chapter_map': [dict(id=u['id'], manuscript_title=u['title'],
              sections=len(u['sections']), merged_subheadings=0) for u in plan['units']],
            'module_decisions': decisions,
            'unprocessed_chapters': [u['id'] for u in plan['units'] if u['id'] not in generated],
            'note': 'All content modules requested from the current source. No prior records inherited. '
                    'Empty modules have explicit reasons, separately inspected by the evaluator.'}
