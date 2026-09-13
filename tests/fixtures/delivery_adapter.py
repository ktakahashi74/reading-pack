"""Offline delivery protocol fixture: no model/network calls."""
import json
import sys
from pathlib import Path

request = json.load(sys.stdin)
mode = sys.argv[1]
with Path(sys.argv[2]).open('a') as log:
    log.write(request['job'] + '\n')
if mode == 'generation_error' and request['stage'] == 'generate':
    sys.exit(1)
if mode == 'partial_generation' and request['job'] == 'generate/CH-02':
    sys.exit(1)
if mode == 'evaluation_error' and request['stage'] != 'generate':
    sys.exit(1)


def value(schema):
    if 'const' in schema:
        return schema['const']
    if 'enum' in schema:
        return schema['enum'][0]
    kind = schema.get('type')
    if kind == 'object':
        return {k: value(s) for k, s in schema['properties'].items()}
    if kind == 'array':
        return []
    if kind == 'integer':
        return 0 if mode == 'low_scores' else 4
    return 'Synthetic evidence for protocol tests.'


response = value(request['response_schema'])
if request['stage'] == 'generate':
    source = request['payload']['source_text']
    result = response['result']
    result['summary'] = '' if mode == 'empty_content' else 'Synthetic chapter summary.'
    for section in request['payload']['chapter']['sections']:
        result['sections'][section['id']]['statement'] = '' if mode == 'empty_content' else 'Synthetic section statement.'
        result['sections'][section['id']]['evidence_quote'] = section['title']
elif request['stage'] == 'artifact_content':
    if mode == 'low_scores':
        for record in response['result']['records'].values():
            record.update(verdict='unsupported', severity='major')
    if mode == 'missing_record':
        response['result']['records'].pop(next(iter(response['result']['records'])))
if mode == 'wrong_identity' and request['stage'] != 'generate':
    response['model'] = 'different-model'
print(json.dumps(response))
