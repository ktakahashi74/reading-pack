"""Finite generator comparisons over the existing artifact workflow studies.

Registration precedes every model call. A comparison never grants acceptance,
author adoption, extra repairs, or permission to exceed a cumulative budget.
"""
from __future__ import annotations

import copy
import itertools
import math
import time
from collections import Counter
from decimal import Decimal
from pathlib import Path

from reading_pack.errors import ReadingPackError
from .work_ledger import artifact_hash


def _number(value, name, *, zero=False):
    if type(value) not in (float, int) or not math.isfinite(value) or value < 0 or (not zero and value == 0):
        raise ReadingPackError(f'invalid comparison {name}')
    return Decimal(str(value))


def controlled_signature(manifest):
    """Allow only generator model identity and per-run audit location to vary."""
    from .pipeline_qualification import workflow_signature
    value = copy.deepcopy(manifest)
    worker = value['recipe']['workers']['generator']
    model = worker['model']
    command = worker['command']
    if '--model' in command:
        index = command.index('--model') + 1
        if index >= len(command) or command[index] != model:
            raise ReadingPackError('generator adapter model differs from recipe')
        command[index] = '<generator-model>'
    worker['model'] = '<generator-model>'
    return workflow_signature(value)


def _inspect_definition(definition, *, fresh):
    from .pipeline import Runner, _unseal
    from .pipeline_qualification import input_identity, workflow_signature
    from .pipeline_resources import operating_plan, populated_seed
    required = {'schema_version', 'variants', 'cases', 'repetitions', 'trials',
                'book_budgets', 'max_cost_usd', 'max_wall_seconds',
                'minimum_completion_rate', 'equivalence_margin'}
    if set(definition) != required or definition['schema_version'] != 1:
        raise ReadingPackError('invalid comparison definition fields')
    repetitions = definition['repetitions']
    if type(repetitions) is not int or not 1 <= repetitions <= 100:
        raise ReadingPackError('comparison repetitions must be fixed between 1 and 100')
    for name in ('minimum_completion_rate', 'equivalence_margin'):
        if not 0 < _number(definition[name], name) < 1:
            raise ReadingPackError(f'comparison {name} must be between zero and one')
    max_cost = _number(definition['max_cost_usd'], 'max_cost_usd')
    max_time = _number(definition['max_wall_seconds'], 'max_wall_seconds')
    variants, cases = definition['variants'], definition['cases']
    if not isinstance(variants, dict) or len(variants) < 2 or not all(isinstance(k,str) and k and isinstance(v,str) and v for k,v in variants.items()):
        raise ReadingPackError('comparison needs at least two named model variants')
    if len(set(variants.values())) != len(variants):
        raise ReadingPackError('comparison model identities must be distinct')
    if not isinstance(cases, dict) or not cases or not all(isinstance(k,str) and k and isinstance(v,str) and v for k,v in cases.items()):
        raise ReadingPackError('comparison cases map case IDs to book IDs')
    budgets = definition['book_budgets']
    if not isinstance(budgets,dict) or set(budgets) != set(cases.values()):
        raise ReadingPackError('every comparison book needs its cumulative budget')
    for budget in budgets.values():
        if set(budget) != {'prior_cost_usd','max_cost_usd','basis'} or not isinstance(budget['basis'],str) or not budget['basis'].strip():
            raise ReadingPackError('book budget needs prior cost, limit and evidence basis')
        _number(budget['prior_cost_usd'], 'prior cost', zero=True)
        _number(budget['max_cost_usd'], 'book limit')
    expected = set(itertools.product(variants, cases, range(repetitions)))
    seen, roots, identities, controls = set(), set(), {}, {}
    trials, cost_by_book, seconds = [], Counter(), Decimal(0)
    for trial in definition['trials']:
        if set(trial) != {'variant','case','repetition','run'} or type(trial['repetition']) is not int:
            raise ReadingPackError('invalid comparison trial')
        key = (trial['variant'],trial['case'],trial['repetition'])
        root = Path(trial['run']).resolve()
        if key not in expected or key in seen or root in roots:
            raise ReadingPackError('unexpected or duplicate comparison trial')
        seen.add(key); roots.add(root)
        runner = Runner(root)
        manifest, recipe = runner.manifest, runner.recipe
        if runner.contract_version != 'artifact-acceptance-1' or recipe.get('operating_envelope',{}).get('mode') != 'qualification':
            raise ReadingPackError('comparison requires bounded artifact qualification runs')
        if any(k in manifest for k in ('restart_origin','reassessment_of')) or (root/'reused-preparation.json').exists():
            raise ReadingPackError('comparison requires fresh production, not reassessment or replay')
        if fresh and (runner.state['state'] != 'ready' or any((root/'jobs').glob('*.json')) or (root/'qualification-trial.json').exists()):
            raise ReadingPackError('comparison must be registered before any calls or trial registration')
        if recipe['workers']['generator']['model'] != variants[key[0]]:
            raise ReadingPackError('comparison generator differs from declared model')
        identity, control = input_identity(manifest), controlled_signature(manifest)
        case = key[1]
        if identities.setdefault(case,identity) != identity or controls.setdefault(case,control) != control:
            raise ReadingPackError('comparison changed input, judge, workflow or resource limits within a case')
        layout = root/'source-layout.json'
        plan = operating_plan(manifest,runner.chunks,
                              layout_chapters=len(_unseal(layout)['chapters']) if layout.exists() else 0,
                              seed_populated=populated_seed(root,manifest))
        if not plan['admitted']:
            raise ReadingPackError('comparison trial not admitted: ' + '; '.join(plan['reasons']))
        envelope = recipe['operating_envelope']
        cost_by_book[cases[case]] += Decimal(str(envelope['max_cost_usd']))
        seconds += Decimal(str(envelope['max_wall_seconds']))
        suite = {'schema_version':2,'contract_version':'artifact-acceptance-1',
                 'workflow_signature':workflow_signature(manifest),'repetitions':repetitions,
                 'cases':[{'id':case,'book_id':cases[case],'input_identity':identity,
                           'expectation':'complete','defect_class':'','record_ids':[]}]}
        trials.append({**trial,'run':str(root),'manifest_sha256':artifact_hash(manifest),'suite':suite})
    if seen != expected:
        raise ReadingPackError('comparison must register every planned trial, including all repetitions')
    reasons = []
    if sum(cost_by_book.values()) > max_cost: reasons.append('trial allowances exceed comparison cost limit')
    if seconds > max_time: reasons.append('trial deadlines exceed comparison wall-time limit')
    for book,amount in cost_by_book.items():
        if amount + Decimal(str(budgets[book]['prior_cost_usd'])) > Decimal(str(budgets[book]['max_cost_usd'])):
            reasons.append(f'{book}: trial allowances exceed remaining cumulative book budget')
    return {'definition':definition,'trials':trials,'admitted':not reasons,'reasons':reasons,
            'reserved_usd':str(sum(cost_by_book.values())), 'reserved_by_book_usd':{k:str(v) for k,v in cost_by_book.items()},
            'maximum_wall_seconds':str(seconds),'controlled_signatures':controls}


def register_comparison(definition, destination):
    from .pipeline import _seal
    from .pipeline_qualification import register_trial
    # Validate the entire matrix before writing trial registration or sending.
    plan = _inspect_definition(definition, fresh=True)
    destination = Path(destination).resolve()
    if destination.exists(): raise ReadingPackError('refusing to overwrite comparison')
    destination.mkdir(parents=True,mode=0o700)
    _seal(destination/'comparison.json',plan)
    if plan['admitted']:
        from reading_pack.project import write_json
        for index,trial in enumerate(plan['trials']):
            path = destination/f'suite-{index}.json'
            write_json(path,trial['suite'])
            register_trial(Path(trial['run']),path,trial['case'],trial['repetition'])
    return plan


def _verified_plan(root):
    from .pipeline import _unseal
    plan = _unseal(root/'comparison.json')
    current = _inspect_definition(plan['definition'],fresh=False)
    if current != plan:
        raise ReadingPackError('comparison inputs, configuration or manifest changed')
    return plan


def run_comparison(root):
    from .pipeline import _lock, _seal, _unseal, resume_pipeline
    root = Path(root).resolve()
    with _lock(root):
        plan = _verified_plan(root)
        if not plan['admitted']:
            return report_comparison(root)
        state_path = root/'execution.json'
        state = _unseal(state_path) if state_path.exists() else {'started_at':time.time(),'attempts':[]}
        _seal(state_path,state)
        deadline = state['started_at'] + plan['definition']['max_wall_seconds']
        attempted = {entry['index'] for entry in state['attempts']}
        for index,trial in enumerate(plan['trials']):
            if index in attempted: continue  # Includes interrupted/unknown outcomes; no automatic retry.
            manifest = _unseal(Path(trial['run'])/'manifest.json')
            allowance = manifest['recipe']['operating_envelope']['max_wall_seconds']
            if time.time() + allowance > deadline:
                state['stop_reason'] = 'comparison_deadline'; break
            entry = {'index':index,'started_at':time.time(),'status':'inflight'}
            state['attempts'].append(entry); _seal(state_path,state)
            try:
                result = resume_pipeline(Path(trial['run']))
                entry.update(status='returned',state=result['state'])
            except (ReadingPackError,OSError) as exc:
                entry.update(status='error',error=str(exc))
            entry['finished_at'] = time.time(); _seal(state_path,state)
            ledger_path = Path(trial['run'])/'resource-ledger.json'
            if ledger_path.exists():
                reservations = _unseal(ledger_path)['reservations']
                if any(v['actual_cost_usd'] is not None and Decimal(str(v['actual_cost_usd'])) > Decimal(v['allowance_usd']) for v in reservations):
                    state['stop_reason'] = 'provider_call_allowance_exceeded'; break
        _seal(state_path,state)
        return report_comparison(root)


def completion_comparisons(rows, variants, margin, minimum_rate):
    """Conservative simultaneous paired Hoeffding intervals for a binary endpoint.

    Assumes independent repeated executions conditional on these fixed cases.
    This is not equivalence of all aspects of quality or untested manuscripts.
    """
    pairs = list(itertools.combinations(variants,2)); output = []
    for left,right in pairs:
        l = {(r['case'],r['repetition']):r for r in rows if r['variant']==left}
        r = {(r['case'],r['repetition']):r for r in rows if r['variant']==right}
        keys = sorted(set(l)&set(r)); n = len(keys)
        difference = sum(int(l[k]['completed'])-int(r[k]['completed']) for k in keys)/n if n else None
        radius = math.sqrt(2*math.log(2*len(pairs)/0.05)/n) if n else None
        interval = [max(-1,difference-radius),min(1,difference+radius)] if n else None
        live = bool(keys) and all(l[k]['fresh_live_measurement'] and r[k]['fresh_live_measurement'] for k in keys)
        lower_radius = math.sqrt(math.log(2*len(variants)/0.05)/(2*n)) if n else None
        target = n and all(sum(v[k]['completed'] for k in keys)/n-lower_radius>=minimum_rate for v in (l,r))
        equivalent = bool(live and target and interval and interval[0]>=-margin and interval[1]<=margin)
        output.append({'left':left,'right':right,'planned_pairs':n,'completion_rate_difference':difference,
                       'simultaneous_95_percent_interval':interval,'status':'within_margin' if equivalent else 'inconclusive',
                       'absolute_target_supported':bool(target),'fresh_live_pairs':live})
    return output


def report_comparison(root):
    from .pipeline import _unseal
    from .pipeline_acceptance_study import assess_study
    from .pipeline_acceptance_records import verify_record
    root = Path(root).resolve(); plan = _verified_plan(root); rows = []; receipts = set()
    for trial in plan['trials']:
        run = Path(trial['run'])
        if plan['admitted']:
            report = assess_study(trial['suite'],[run])
            measured = report['trials'][0]
            # assess_study checks the individual registration, not just copied summaries.
            row = {k:measured[k] for k in ('completed','acceptance','execution','fresh_live_measurement','resources','record')}
            record = verify_record(run,row['record']) if row['record'] else None
            row['inspection'] = record['acceptance'] if record else None
            ledger = _unseal(run/'resource-ledger.json') if (run/'resource-ledger.json').exists() else None
            for reservation in (ledger or {}).get('reservations',[]):
                if reservation.get('receipt_path'):
                    receipt = str(Path(reservation['receipt_path']).resolve())
                    if receipt in receipts: raise ReadingPackError('comparison reused a provider receipt')
                    receipts.add(receipt)
            recipe = _unseal(run/'manifest.json')['recipe']
            envelope = recipe['operating_envelope']
            row['within_limits_completed'] = bool(row['completed'] and ledger and ledger['finished_at'] is not None
                and ledger['finished_at'] <= ledger['deadline'] and not row['resources']['unknown_cost_calls']
                and Decimal(row['resources']['known_reported_usd']) <= Decimal(str(envelope['max_cost_usd'])))
        else:
            row = {'completed':False,'acceptance':'not_run','execution':{'status':'not_run'},
                   'fresh_live_measurement':False,'within_limits_completed':False,'inspection':None,
                   'resources':{'measured':False},'record':None}
        rows.append({**{k:trial[k] for k in ('variant','case','repetition','run')},**row})
    variants = {}
    for name,model in plan['definition']['variants'].items():
        selected = [r for r in rows if r['variant']==name]
        costs = [r['resources'] for r in selected]
        variants[name] = {'model':model,'planned':len(selected),'completed':sum(r['completed'] for r in selected),
                          'completion_rate':sum(r['completed'] for r in selected)/len(selected),
                          'within_limits_completion_rate':sum(r['within_limits_completed'] for r in selected)/len(selected),
                          'acceptance_counts':dict(Counter(r['acceptance'] for r in selected)),
                          'known_reported_usd':str(sum((Decimal(r.get('known_reported_usd','0')) for r in costs),Decimal(0))),
                          'unknown_cost_calls':sum(r.get('unknown_cost_calls',0) for r in costs),
                          'elapsed_seconds':[r.get('elapsed_seconds') for r in costs]}
    return {'schema_version':1,'kind':'artifact-model-comparison','comparison_sha256':artifact_hash(plan),
            'admitted':plan['admitted'],'reasons':plan['reasons'],'variants':variants,'trials':rows,
            'execution':_unseal(root/'execution.json') if (root/'execution.json').exists() else {'attempts':[]},
            'comparisons':completion_comparisons([{**r,'completed':r['within_limits_completed']} for r in rows],list(variants),plan['definition']['equivalence_margin'],plan['definition']['minimum_completion_rate']),
            'quality_saturation':'not_established','pack_acceptance_gate':False,'author_adoption':False,
            'limits':'Fixed cases and model configurations only. Binary completion equivalence is not full quality saturation. '
                     'Missing and failed trials remain in the denominator. Provider costs are not invoices. '
                     'Independent source inspection is needed to rule out shared judge omissions.'}
