"""Worker-facing record schemas derived from the candidate admission rules."""

from __future__ import annotations

import copy
from typing import Any

from reading_pack.schema_validation import schema_document
from .candidates import ALLOWED_FIELDS, CONTENT_FIELDS, ID_PATTERNS, LIST_LIMITS, STRING_LIMITS


def candidate_record_schemas(canonical: dict) -> dict:
    document = schema_document('language-pack.schema.json')

    def expand(value: Any) -> Any:
        if isinstance(value, dict):
            if '$ref' in value:
                return expand(document['$defs'][value['$ref'].removeprefix('#/$defs/')])
            return {key: expand(item) for key, item in value.items()}
        if isinstance(value, list):
            return [expand(item) for item in value]
        return copy.deepcopy(value)

    result = {}
    definitions = dict(zip(ID_PATTERNS, ('chapter', 'certainty', 'claim', 'misreading',
                                        'policy', 'name', 'term', 'reference')))
    chapter_ids = [record['id'] for record in canonical['chapters']]
    for collection, definition in definitions.items():
        schema = expand(document['$defs'][definition])
        properties = schema['properties']
        for field in list(properties):
            if field not in ALLOWED_FIELDS[collection]:
                del properties[field]
        # Canonical provenance is assigned by the controller, never by a worker.
        schema.pop('allOf', None)
        id_rule = {'type': 'string', 'pattern': '^' + ID_PATTERNS[collection] + '$',
                   'maxLength': 200}
        existing = [record['id'] for record in canonical.get(collection, [])]
        properties['id'] = ({'enum': chapter_ids} if collection == 'chapters' else
                            {'anyOf': [id_rule, {'enum': existing}]} if existing else id_rule)
        properties['status'] = {'const': 'draft'}
        for field in CONTENT_FIELDS[collection]:
            properties[field]['minLength'] = 1
        for field, maximum in STRING_LIMITS[collection].items():
            properties[field]['maxLength'] = min(properties[field].get('maxLength', maximum), maximum)
        for field, (count, length) in LIST_LIMITS.get(collection, {}).items():
            properties[field]['maxItems'] = count
            properties[field]['items'].update(minLength=1, maxLength=length)
        if 'chapter_id' in properties:
            properties['chapter_id']['enum'] = chapter_ids
        if 'chapter_ids' in properties:
            properties['chapter_ids']['items']['enum'] = chapter_ids
        if collection == 'claims':
            schema['allOf'] = [
                {'if': {'properties': {'layer': {'const': layer}}},
                 'then': {'properties': {field: {'const': ''}}}}
                for layer, field in [('descriptive', 'revision_conditions'), ('normative', 'falsifiability')]
            ]
        result[collection] = schema
    return result
