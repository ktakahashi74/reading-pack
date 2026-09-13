"""Fixed audit requirements, source-bound adjudication and explicit advice."""
from __future__ import annotations

import copy
import re

from reading_pack.errors import ReadingPackError
from reading_pack.profiles import PROFILES
from reading_pack.staging import _existing_kind, _planned_records
from .pipeline_evidence import source_spans, worker_payload
from .pipeline_generation import author_contract, record_protected, compact_canonical
from .work_ledger import artifact_hash


AUDIT_POLICY = {
    'version': 1,
    'purpose': 'A concise source-faithful reading aid, not an exhaustive replacement for the book.',
    'criteria': {
        'fidelity': 'An existing statement contradicts, invents, or materially distorts the source.',
        'attribution': 'A statement, position, source locator or author voice is attributed incorrectly.',
        'qualification': 'An omitted condition changes the meaning or scope of an existing statement.',
        'consistency': 'The Pack gives incompatible answers about the same issue without marking the distinction.',
        'required_coverage': 'One of the explicitly listed frozen coverage requirements is missing from the whole Pack. Cite its requirement ID; do not invent an additional coverage obligation.',
        'structure': 'A concrete body heading is missing, spurious, misplaced or misnamed in the source-bound navigation.',
        'spoiler': 'Content violates the declared spoiler policy.',
    },
    'selection_rules': [
        'Chapter summaries orient the reader; do not require paragraph-level coverage or every example.',
        'Names mentioned only in bibliographic/footnote attribution need no separate person record.',
        'Do not require every citation, background institution or synonym to have its own record.',
        'Consider qualifications elsewhere in the whole Pack; a missing field is not automatically a missing meaning.',
        'Additional detail, optional cross-links and alternative wording are advice unless a criterion has a concrete material reader impact.',
        'An original author-supplied roster is fixed; do not require adding every source proposition to that roster.',
        'Failure to find support in one excerpt is unresolved evidence, not proof of fabrication or source silence.',
        'Supplements keep their stated roles; never infer that any supplement automatically overrides the manuscript.',
    ],
    'decision_rule': 'Only confirmed material violations and unresolved suspected violations block. Advice and disproven suspicions are retained separately. Never change thresholds or author approval.',
}

CANONICAL_SCOPE = {
    'complete_pack_records': True,
    'instruction': 'The canonical collections supplied here are the complete Pack records. '
        'A coverage ID development:<case>:<index> identifies an atomic requirement of a frozen reader question. '
        'It is not a Pack record ID, and no development module exists or is required. '
        'Decide whether the required meaning is present across the supplied Pack, without requiring the coverage ID to appear in a record. '
        'Source excerpts can be incomplete; source availability and Pack coverage are separate questions.',
}


def audit_contract(profile: str) -> dict:
    return {**copy.deepcopy(AUDIT_POLICY), 'profile': profile,
            'required_modules': sorted(PROFILES[profile].required_modules)}


def run_contract(runner, canonical: dict) -> dict:
    """Freeze finite coverage obligations; factual errors remain disallowed everywhere."""
    from .pipeline import _seal, _unseal
    from .pipeline_contracts import ARTIFACT_CONTRACT_VERSION, manifest_contract
    artifact_acceptance = manifest_contract(runner.manifest) == ARTIFACT_CONTRACT_VERSION
    contract=audit_contract(runner.recipe['profile'])
    purpose = runner.manifest.get('reader_utility_contract')
    if purpose:
        contract['reader_utility_contract'] = copy.deepcopy(purpose)
        contract['reader_questions_define_coverage'] = False
    if artifact_acceptance:
        contract['contract_version'] = ARTIFACT_CONTRACT_VERSION
        contract['reader_questions_define_coverage'] = False
    requirements=[{'id':'module:'+name,'description':'The '+name+' module must contain source-supported content.'}
                  for name in contract['required_modules']]
    requirements.extend({'id':'chapter:'+c['id'],'description':'Provide a concise orientation to '+c['title']+'.'}
                        for c in canonical['chapters'])
    benchmark=runner.root/'benchmark.json'
    if not artifact_acceptance and benchmark.exists() and purpose:
        # Bind the reviewed probes without publishing them as content quotas.
        contract['development_suite_sha256'] = artifact_hash(_unseal(benchmark)['development'])
    # Existing studies retain their frozen scope. New studies must not turn
    # whatever a generated question happens to ask into an adoption obligation.
    if not artifact_acceptance and benchmark.exists() and not purpose:
        for case in _unseal(benchmark)['development']:
            requirements.extend({'id':f'development:{case["id"]}:{i}', 'description':requirement}
                                for i,requirement in enumerate(case['requirements']))
    contract['coverage_requirements']=requirements
    path=runner.root/'audit-contract.json'
    if path.exists():
        if _unseal(path)!=contract:
            raise ReadingPackError('frozen audit coverage contract changed')
    else:
        _seal(path,contract)
    return contract


def structural_counts(runner, canonical: dict) -> dict | None:
    """Use the parsed/reviewed inventory, not addition of model-estimated counts."""
    from .pipeline import _unseal
    names = ('seed-reconciled-import-plan.json', 'reviewed-import-plan.json', 'source-import-plan.json')
    path = next((runner.root / name for name in names if (runner.root / name).exists()), None)
    if path is None:
        return None
    if (runner.root / 'source-layout.json').exists():
        review = _unseal(runner.root / 'structure-reviews.json')
        if not review.get('complete'):
            raise ReadingPackError('structure inventory has not passed independent review')
    plan = _unseal(path)
    if plan['source']['sha256'] != runner.manifest['sources'][0]['sha256']:
        raise ReadingPackError('structure inventory source binding changed')
    inventory_sha256 = artifact_hash(plan)
    aliases_sha256 = None
    outline_path = runner.root / 'source-text-outline.json'
    if path.name == 'source-import-plan.json' and outline_path.exists():
        # Older checkpoints retain the original plan and its sealed aliases,
        # without a separate mapped plan. Apply only those recorded aliases.
        outline = _unseal(outline_path)
        if outline['source_sha256'] != plan['source']['sha256']:
            raise ReadingPackError('structure alias source binding changed')
        units = {unit['staging_id']: unit for unit in plan['units']}
        seen = set()
        for alias in outline['aliases']:
            unit = units.get(alias['unit_id'])
            if unit is None or alias['unit_id'] in seen or unit['title'] != alias['source_title']:
                raise ReadingPackError('structure alias does not match the source inventory')
            unit['title'] = alias['canonical_title']
            seen.add(alias['unit_id'])
        aliases_sha256 = artifact_hash(outline)
    expected = _planned_records(plan)
    actual = canonical['chapters']
    count = lambda chapters: sum(1 + len(c.get('sections', [])) for c in chapters)
    matched = 0
    for old, new in zip(expected, actual):
        if (_existing_kind(old), old['title'], old.get('pages', '')) != (_existing_kind(new), new['title'], new.get('pages', '')):
            continue
        matched += 1
        matched += sum(a == b for a, b in zip(old['sections'], new['sections']))
    return {'expected_structure_records': count(expected), 'observed_structure_records': count(actual),
            'matched_structure_records': matched, 'inventory_sha256': inventory_sha256,
            'aliases_sha256': aliases_sha256}


def _slice(chunks: list[dict], source_id: str, start: int, end: int) -> dict:
    source = sorted((c for c in chunks if c['source_id'] == source_id), key=lambda c: c['start'])
    if not source or not 0 <= start < end <= source[-1]['end']:
        raise ReadingPackError('audit context has an invalid source range')
    cursor, pieces = start, []
    for chunk in source:
        left, right = max(start, chunk['start']), min(end, chunk['end'])
        if left >= right:
            continue
        text = chunk['text'][left-chunk['text_start']:right-chunk['text_start']]
        if left != cursor or len(text) != right-left or chunk['source_sha256'] != source[0]['source_sha256']:
            raise ReadingPackError('audit context source is inconsistent')
        pieces.append(text); cursor = right
    if cursor != end:
        raise ReadingPackError('audit context has a source gap')
    return {**source[0], 'id': f'{source_id}-{start}-{end}-audit-context', 'start': start, 'end': end,
            'text_start': start, 'text': ''.join(pieces)}


def evidence_context(runner, canonical: dict, findings: list[dict], *, maximum: int = 32000) -> dict:
    """Retrieve only frozen sources by exact evidence, locators and anchors.

    No arbitrary files, URLs, reader questions or held-out answers are accessed.
    Every retrieval omission is explicit and prevents dismissing a suspicion.
    """
    sources = {s['id']: s for s in runner.manifest['sources']}
    by_name = {}
    for sid, source in sources.items():
        by_name.setdefault(source['name'], []).append(sid)
    lengths = {sid: max(c['end'] for c in runner.chunks if c['source_id'] == sid) for sid in sources}
    records = {r['id']: r for values in canonical.values() if isinstance(values, list)
               for r in values if isinstance(r, dict) and 'id' in r}
    wanted, missing = [], []
    primary_queries = set()

    def anchor_ranges(anchor, ids):
        found = False
        for chunk in runner.chunks:
            if chunk['source_id'] not in ids:
                continue
            for match in re.finditer(r'(?<![A-Za-z0-9_.:-])' + re.escape(anchor) + r'(?![A-Za-z0-9_.:-])', chunk['text']):
                position = chunk['text_start'] + match.start()
                if chunk['start'] <= position < chunk['end']:
                    wanted.append((chunk['source_id'], max(0, position-500), min(lengths[chunk['source_id']], position+6500)))
                    found = True
        return found
    for finding in findings:
        for ref in finding.get('evidence', []):
            if ref['source_id'] not in sources or sources[ref['source_id']]['sha256'] != ref.get('source_sha256'):
                raise ReadingPackError('audit finding source identity changed')
            exact = _slice(runner.chunks, ref['source_id'], ref['start'], ref['end'])
            if exact['text'] != ref['quote']:
                raise ReadingPackError('audit finding source quotation changed')
            wanted.append((ref['source_id'], max(0,ref['start']-1000), min(lengths[ref['source_id']],ref['end']+1000)))
        for rid in finding.get('record_ids', []):
            record = records.get(rid)
            if record is None:
                missing.append('unknown record: '+rid);continue
            for locator in record.get('source_locations', []):
                match = re.fullmatch(r'(.+)#normalized-text:(\d+)-(\d+)', locator)
                if not match:
                    fragment = re.fullmatch(r'([^#]+)#([A-Za-z0-9_.:-]+)', locator)
                    if fragment:
                        ids = by_name.get(fragment[1], [])
                        if len(ids) != 1 or not anchor_ranges(fragment[2], ids):
                            missing.append('unresolved locator: ' + locator)
                    # Free-form page locators remain visible to the judge.
                    continue
                ids = by_name.get(match[1], [])
                if len(ids)!=1 or not 0 <= int(match[2]) < int(match[3]) <= lengths[ids[0]]:
                    missing.append('unresolved locator: '+locator);continue
                sid=ids[0];wanted.append((sid,max(0,int(match[2])-1000),min(lengths[sid],int(match[3])+1000)))
            anchor=record.get('anchor')
            if isinstance(anchor,str) and re.fullmatch(r'[A-Za-z0-9_.:-]+',anchor):
                if not anchor_ranges(anchor, sources):
                    missing.append('unresolved anchor: '+anchor)
            if finding.get('category') == 'misattributed':
                # Attribution disputes need manuscript context as well as a
                # supplement locator. Search explicit entity labels only.
                labels = [record.get('name'), record.get('term'), *record.get('aliases', [])]
                primary_queries.update(label for label in labels if isinstance(label, str) and 3 <= len(label) <= 120)
        if finding.get('category') == 'misattributed':
            text = finding.get('reason', '') + ' ' + ' '.join(ref['quote'] for ref in finding.get('evidence', []))
            primary_queries.update(re.findall(r'fn:[A-Za-z0-9_.:-]+', text))
    literal_searches = []
    for sid, source in sources.items():
        if source['role'] != 'primary-book' or not primary_queries:
            continue
        text = _slice(runner.chunks, sid, 0, lengths[sid])['text']
        for query in sorted(primary_queries):
            positions = [m.start() for m in re.finditer(re.escape(query), text)]
            literal_searches.append({'query': query, 'source_id': sid, 'source_sha256': source['sha256'],
                                     'searched_range': [0, lengths[sid]], 'match_count': len(positions)})
            wanted.extend((sid, max(0, position-600), min(lengths[sid], position+len(query)+1000)) for position in positions)
    # Preserve retrieval priority, subtract previously included ranges, and keep
    # every complete requested interval or explicitly report its omission.
    samples=[];seen={};used=0
    for sid,start,end in wanted:
        intervals=[(start,end)]
        for left,right in seen.get(sid,[]):
            intervals=[part for a,b in intervals for part in ((a,min(b,left)),(max(a,right),b)) if part[0]<part[1]]
        need=sum(b-a for a,b in intervals)
        if used+need>maximum:
            missing.append(f'context limit: {sid}:{start}-{end}');continue
        for a,b in intervals:
            samples.append(_slice(runner.chunks,sid,a,b));used+=b-a;seen.setdefault(sid,[]).append((a,b))
    result = {'samples':samples,'retrieval':{'characters':used,'maximum_characters':maximum,
            'complete':not missing,'unresolved':list(dict.fromkeys(missing))}}
    if literal_searches:
        result['retrieval']['literal_searches'] = literal_searches
        result['retrieval']['literal_search_note'] = 'Exact label searches of frozen manuscript text only. Zero matches do not prove semantic absence. Retrieved quotations, source roles and the complete Pack determine attribution; this search does not settle a finding.'
    return result


def _suspicions(audit: dict, chunk: dict, canonical: dict) -> list[dict]:
    findings=copy.deepcopy(audit['findings'])
    # Legacy aggregate strings must not bypass adjudication or vanish. Add
    # explicit observations; their source interval supplies retrieval context.
    records={r['id'] for rows in compact_canonical(canonical).values() for r in rows}
    first=source_spans(chunk)[0] if audit['source_attribution_errors'] or audit['invented_record_ids'] else None
    ref=({'source_id':first['source_id'],'span_id':first['id'],'source_sha256':first['source_sha256'],
         'start':first['start'],'end':first['end'],'quote':first['text']} if first else None)
    for field in ('source_attribution_errors','invented_record_ids'):
        for message in audit[field]:
            ids=sorted(rid for rid in records if re.search(r'(?<![A-Za-z0-9.-])'+re.escape(rid)+r'(?![A-Za-z0-9.-])',message))
            if any(f['category']=='misattributed' and f['record_ids']==ids and f['reason']==message for f in findings):continue
            findings.append({'category':'misattributed','record_ids':ids,'reason':message,'evidence':[ref],
                             'aggregate_kind':field,'source_scope':{k:chunk[k] for k in ('source_id','start','end')},
                             'source_audit_errors':{field:[message]}})
    return list({'F-'+artifact_hash(f)[:24]:{'finding_id':'F-'+artifact_hash(f)[:24],**f} for f in findings}.values())


def adjudication_batches(runner, canonical: dict, findings: list[dict]):
    """Split an overfull batch locally before calling a judge; never raise its cap."""
    pending=[(start,findings[start:start+4]) for start in range(0,len(findings),4)]
    while pending:
        start,batch=pending.pop(0)
        context=evidence_context(runner,canonical,batch,maximum=runner.recipe.get('audit_context_characters',32000))
        overfull=any(reason.startswith('context limit:') for reason in context['retrieval']['unresolved'])
        if overfull and len(batch)>1:
            middle=len(batch)//2
            pending[:0]=[(start,batch[:middle]),(start+middle,batch[middle:])]
            continue
        yield start,batch,context


def adjudicate_audit(runner, project, canonical: dict, audit: dict, chunk: dict, number: int, *, resolution_context=None) -> dict:
    audit=copy.deepcopy(audit)
    requirement_ids={r['id'] for r in run_contract(runner,canonical)['coverage_requirements']}
    for f in audit['findings']:
        misplaced=set(f.get('record_ids',[])) & requirement_ids
        if misplaced:
            f['record_ids']=[rid for rid in f['record_ids'] if rid not in misplaced]
            f['requirement_ids']=sorted(set(f.get('requirement_ids',[])) | misplaced)
    findings=_suspicions(audit,chunk,canonical)
    result={'blocking':[],'advisories':[],'dismissed':[],'decisions':[]}
    batches=([(0,findings,resolution_context)] if resolution_context is not None else adjudication_batches(runner,canonical,findings))
    for start,batch,context in batches:
        payload={**context,'canonical':compact_canonical(canonical),'findings':batch,
                 'audit_contract':run_contract(runner,canonical),
                 'canonical_scope': copy.deepcopy(CANONICAL_SCOPE),
                 'author_input_contract':author_contract(project,runner.lang)}
        response=runner.call('audit_adjudicate',payload,f'{number}/audit-adjudicate/{chunk["id"]}/{start}')
        decisions=response['decisions']
        if len(decisions)!=len(batch) or {d['finding_id'] for d in decisions}!={f['finding_id'] for f in batch}:
            raise ReadingPackError('audit adjudication coverage mismatch')
        _,registry=worker_payload({'samples':context['samples']})
        for decision in decisions:
            finding=next(f for f in batch if f['finding_id']==decision['finding_id'])
            for ref in decision['evidence']:
                span=registry.get(ref['span_id'])
                if span is None or ref['source_id']!=span['source_id'] or ref['quote']!=span['text']:
                    raise ReadingPackError('audit adjudication used unprovided evidence')
            status=decision['classification']
            if status=='blocking' and (decision['criterion'] not in AUDIT_POLICY['criteria'] or not decision['reader_impact'].strip() or not decision['evidence'] or decision['repair_scope']=='none'):
                raise ReadingPackError('blocking audit decision lacks a criterion, impact or evidence')
            if status=='blocking' and decision['criterion']=='required_coverage':
                allowed={r['id'] for r in payload['audit_contract']['coverage_requirements']}
                if not decision.get('requirement_ids') or not set(decision['requirement_ids'])<=allowed:
                    raise ReadingPackError('coverage violation lacks a frozen requirement ID')
            if status=='dismissed' and not decision['evidence']:
                raise ReadingPackError('dismissed audit decision lacks source evidence')
            if status in {'advisory','dismissed'} and not context['retrieval']['complete']:
                status='unresolved'
            revised={**finding,'reason':decision['reason'],'evidence':decision['evidence'] or finding['evidence'],
                     'criterion':decision['criterion'],'reader_impact':decision['reader_impact'],
                     'classification':status,'repair_scope':decision['repair_scope']}
            if decision.get('requirement_ids'):revised['requirement_ids']=decision['requirement_ids']
            if status in {'blocking','unresolved'}:result['blocking'].append(revised)
            else:result['advisories' if status=='advisory' else 'dismissed'].append(revised)
            result['decisions'].append({'finding':finding,'decision':decision,'effective_classification':status,
                                        'retrieval':context['retrieval']})
    return result


def protected_conflicts(project, language: str, canonical: dict, failures: list[dict]) -> list[dict]:
    from reading_pack_review.author_input import COLLECTION_MODULES
    contract=author_contract(project,language)
    modules={collection:module for module,collection in COLLECTION_MODULES.items()}
    records={r['id']:(collection,r) for collection,rows in compact_canonical(canonical).items() for r in rows}
    result=[]
    for finding in failures:
        if finding.get('classification')!='blocking' or finding.get('repair_scope')!='record':continue
        blocked=[]
        for rid in finding['record_ids']:
            if rid not in records:continue
            collection,record=records[rid]
            rule=contract.get(modules[collection],{'mode':'generate','protected_ids':[]})
            if record_protected(rule,rid):blocked.append(rid)
        if blocked:result.append({'record_ids':blocked,'finding':finding,'author_approval':False})
    return result
