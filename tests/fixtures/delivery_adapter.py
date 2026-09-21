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
if mode in ('partial_generation', 'partial_repair', 'partial_repair_reeval_error') and request['job'] == 'generate/CH-02':
    sys.exit(1)
if mode in ('reevaluation_error', 'partial_repair_reeval_error') and request['job'].startswith('reevaluate/'):
    sys.exit(1)
if mode == 'evaluation_error' and request['stage'] != 'generate':
    sys.exit(1)


def value(schema):
    if '$ref' in schema:
        name = schema['$ref'].removeprefix('#/$defs/')
        return value(request['response_schema']['properties']['result']['$defs'][name])
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
    if 'modules' in result and mode in ('fresh_modules', 'missing_module', 'unexplained_empty'):
        for name, module in result['modules'].items():
            item_schema = request['response_schema']['properties']['result']['properties']['modules']['properties'][name]['properties']['items']['items']
            item = value(item_schema)
            if name == 'claims':
                item['revision_conditions'] = ''
            anchor = (request['payload']['chapter']['sections'] or [request['payload']['chapter']])[0]
            item['section_id'] = anchor['id']
            item['evidence_quote'] = source[anchor['start'] - request['payload']['source_start']:][:20]
            module.update(items=[item], omission_reason='')
        if mode == 'missing_module':
            del result['modules']['references']
        if mode == 'unexplained_empty':
            result['modules']['names'] = {'items': [], 'omission_reason': ''}
elif request['stage'] == 'artifact_content':
    if mode == 'low_scores':
        for record in response['result']['records'].values():
            record.update(verdict='unsupported', severity='major')
    if mode == 'missing_record':
        response['result']['records'].pop(next(iter(response['result']['records'])))
if mode in ('repair_one', 'repair_bad_quote', 'repair_outside_scope', 'reevaluation_error', 'partial_repair', 'partial_repair_reeval_error'):
    if request['job'].startswith('evaluate/') and request['stage'] == 'artifact_content':
        first = next(k for k in response['result']['records'] if k.startswith('CP-'))
        response['result']['records'][first].update(verdict='partially_supported',severity='minor',reason='Restore the exact first evidence sentence.')
    if request['stage'] == 'repair':
        payload=request['payload']; first=payload['chapter']['sections'][0]
        target='/sections/'+first['id']; issue='record:CP-'+first['id']
        response['result']['dispositions']={i['id']:{'action':'changed' if i['id']==issue else 'no_change','reason':'Source checked.'} for i in payload['findings']}
        response['result']['changes']=[{'target':target,'operation':'replace',
            'replacement':{'statement':'Evidence one.','evidence_quote':'Evidence one.'},'issue_ids':[issue],
            'reason':'Restore source detail.','section_id':first['id'],'source_quote':'Evidence one.'}]
        if mode=='repair_bad_quote':response['result']['changes'][0]['source_quote']='Invented quotation'
        if mode=='repair_outside_scope':response['result']['changes'][0]['target']='/summary'
if request['stage']=='repair' and mode=='low_scores':
    response['result']['changes']=[]
    for disposition in response['result']['dispositions'].values():disposition.update(action='no_change',reason='Finding declined after source inspection.')
if mode == 'wrong_identity' and request['stage'] != 'generate':
    response['model'] = 'different-model'
print(json.dumps(response))
