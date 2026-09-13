"""Finite, preregistered observations of artifact workflows, separate from Pack acceptance."""
from __future__ import annotations

import hashlib
import re
from collections import Counter
from pathlib import Path
from reading_pack.errors import ReadingPackError
from .work_ledger import artifact_hash


def validate_study(suite):
    if set(suite) != {'schema_version','contract_version','workflow_signature','repetitions','cases'} or suite['schema_version'] != 2 or suite['contract_version'] != 'artifact-acceptance-1':
        raise ReadingPackError('invalid artifact workflow study')
    if type(suite['repetitions']) is not int or not 1 <= suite['repetitions'] <= 100:
        raise ReadingPackError('study repetitions must be fixed between 1 and 100')
    if not isinstance(suite['workflow_signature'],str) or not re.fullmatch('[a-f0-9]{64}',suite['workflow_signature']):
        raise ReadingPackError('study needs a fixed workflow signature')
    if not isinstance(suite['cases'],list) or not suite['cases']:
        raise ReadingPackError('study needs a finite nonempty case list')
    ids=[]
    for case in suite['cases']:
        if set(case) != {'id','book_id','input_identity','expectation','defect_class','record_ids'} or case['expectation'] not in {'complete','detect-defect'}:
            raise ReadingPackError('invalid artifact study case')
        if any(not isinstance(case[k],str) or not case[k] for k in ('id','book_id','input_identity')):
            raise ReadingPackError('study case needs fixed identities')
        if not isinstance(case['record_ids'],list) or any(not isinstance(v,str) or not v for v in case['record_ids']):
            raise ReadingPackError('study record IDs must be strings')
        if case['expectation']=='detect-defect' and (not case['defect_class'] or not case['record_ids']):
            raise ReadingPackError('defect study needs concrete targets')
        ids.append(case['id'])
    if len(ids)!=len(set(ids)): raise ReadingPackError('duplicate artifact study case')


def assess_study(suite, roots):
    from .pipeline import Runner, _unseal
    from .pipeline_qualification import workflow_signature,input_identity
    from .pipeline_acceptance_records import verify_record
    from .pipeline_resources import resource_status, adapter_receipt
    validate_study(suite)
    expected={(c['id'],n):c for c in suite['cases'] for n in range(suite['repetitions'])}
    seen=set();trials=[];receipts=set()
    for root in roots:
        root=Path(root).resolve();runner=Runner(root)
        trial=_unseal(root/'qualification-trial.json');key=(trial['case_id'],trial['repetition'])
        if key in seen or key not in expected or trial['suite']!=suite or trial['suite_sha256']!=artifact_hash(suite):
            raise ReadingPackError('unexpected, duplicate or changed artifact study trial')
        if trial['manifest_sha256']!=artifact_hash(runner.manifest) or workflow_signature(runner.manifest)!=suite['workflow_signature'] or input_identity(runner.manifest)!=expected[key]['input_identity']:
            raise ReadingPackError('artifact study input or workflow changed')
        if runner.contract_version!='artifact-acceptance-1' or runner.manifest.get('reassessment_of'):
            raise ReadingPackError('reassessments do not measure fresh production')
        seen.add(key);state=runner.state
        record=verify_record(root,state['acceptance_record']) if state.get('acceptance_record') else None
        completed=bool(record and record['acceptance']['status']=='pass' and record['author_approval']['review'])
        resources=resource_status(root)
        jobs=[_unseal(p) for p in (root/'jobs').glob('*.json')]
        ledger=_unseal(root/'resource-ledger.json') if (root/'resource-ledger.json').exists() else {'reservations':[]}
        actual=ledger['reservations'];live=bool(actual) and not any(j.get('reused_from') for j in jobs)
        if Counter(j['request']['request_id'] for j in jobs for _ in j['attempts'])!=Counter(i['request_id'] for i in actual):live=False
        for item in actual:
            if not item.get('receipt_path') or not item.get('receipt_valid'):
                live=False;continue
            path=Path(item['receipt_path'])
            if path.resolve() in receipts: raise ReadingPackError('study reused provider receipt')
            receipts.add(path.resolve())
            if hashlib.sha256(path.read_bytes()).hexdigest()!=item['receipt_sha256']:
                raise ReadingPackError('study receipt changed')
            receipt=adapter_receipt(['--audit-dir',str(path.parent.parent)],item['request_id'])
            if not receipt['receipt_valid'] or receipt['actual_cost_usd']!=item['actual_cost_usd']:
                raise ReadingPackError('study receipt differs from provider evidence')
        detected_ids=set()
        for folder in (root/'acceptance/inspections').glob('*'):
            reports=sorted((folder/'reports').glob('*.json'))
            if not reports: continue
            from .pipeline_acceptance import _read_ref
            from .pipeline_acceptance_ledger import summary
            inspected=_unseal(reports[-1])
            inventory=_read_ref(root,inspected['inventory'])
            measured=summary(inventory,inspected['ledger'])
            if measured!=inspected['acceptance']:
                raise ReadingPackError('study inspection summary changed')
            checks={c['id']:c for c in inventory['checks']}
            targets={t['id']:t for t in inventory['targets']}
            for defect in measured['confirmed_defects']:
                evidence=_read_ref(root,defect['evidence_ref'])
                if not any(r['check_id']==defect['check_id'] and r['outcome']=='defect' for r in evidence['results']):
                    raise ReadingPackError('study defect evidence changed')
                target=targets[checks[defect['check_id']]['target_id']]
                if target.get('record_id'): detected_ids.add(target['record_id'])
        expected_ids=set(expected[key]['record_ids'])
        detected=bool(expected_ids) and expected_ids<=detected_ids
        trials.append({'case_id':key[0],'repetition':key[1],'run':str(root),
                       'expectation':expected[key]['expectation'], 'completed':completed,
                       'detected_defect':detected, 'detected_record_ids':sorted(detected_ids),
                       'acceptance':record['acceptance']['status'] if record else 'not_run',
                       'execution':record['execution'] if record else {'status':'ready'},
                       'fresh_live_measurement':live,'resources':resources,
                       'record':state.get('acceptance_record')})
    total=sum(c['expectation']=='complete' for c in expected.values())
    successes=sum(t['completed'] and t['expectation']=='complete' for t in trials)
    return {'schema_version':2,'kind':'artifact-workflow-observation','suite':suite,
            'suite_sha256':artifact_hash(suite),'workflow_signature':suite['workflow_signature'],
            'trials':trials,'missing_trials':[{'case_id':k[0],'repetition':k[1]} for k in sorted(set(expected)-seen)],
            'qualified':False,'pack_acceptance_gate':False,'author_adoption':False,
            'observed_completion_rate':successes/total if total else None,
            'defect_control_outcomes':[{'case_id':t['case_id'],'repetition':t['repetition'],'detected':t['detected_defect']} for t in trials if t['expectation']=='detect-defect'],
            'fresh_live_complete_study':bool(trials) and set(expected)==seen and all(t['fresh_live_measurement'] for t in trials),
            'limits':'Finite observations only. Missing trials stay in the denominator. Synthetic responses and existing-candidate reassessments do not establish live production performance. No individual Pack requires a minimum book, repetition or defect-class count.'}
