"""Preregistered, measured workflow qualification; never a unit-test certificate.

Clean inputs measure completion and false rejection. Deliberately defective
seeds measure detection independently of whether a subsequent repair succeeds.
Every trial is retained; no selecting only successful runs or reusing answers.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

from reading_pack.errors import ReadingPackError
from .work_ledger import artifact_hash
from .pipeline_resources import ResourceLimit, resource_status


def workflow_signature(manifest: dict) -> str:
    from .pipeline_contracts import manifest_contract
    contract_version = manifest_contract(manifest)
    recipe = copy.deepcopy(manifest['recipe'])
    if recipe.get('operating_envelope'):
        recipe['operating_envelope'].pop('mode', None)
    for worker in recipe['workers'].values():
        command = worker['command']
        for flag in ('--audit-dir',):
            if flag in command:
                command[command.index(flag) + 1] = '<per-run-directory>'
    identity = {'engine': manifest['engine_sha256'], 'recipe': recipe,
                'adapters': manifest['adapter_files'],
                'reader_utility_contract': manifest.get('reader_utility_contract'),
                'benchmark_selection_contract': manifest.get('benchmark_selection_contract')}
    # Keep signatures of historical manifests byte-for-byte compatible.
    if 'contract_version' in manifest:
        identity['contract_version'] = contract_version
    return artifact_hash(identity)


def input_identity(manifest: dict) -> str:
    return artifact_hash({'inputs': manifest['inputs'], 'seed': manifest['seed'],
                          'sources': manifest['sources'], 'language': manifest['language']})


def validate_suite(suite: dict) -> None:
    if suite.get("contract_version") == "artifact-acceptance-1":
        from .pipeline_acceptance_study import validate_study
        return validate_study(suite)
    required = {'schema_version', 'workflow_signature', 'repetitions', 'minimum_distinct_books',
                'minimum_defect_classes', 'minimum_completion_rate', 'cases'}
    if set(suite) != required or suite['schema_version'] != 1:
        raise ReadingPackError('invalid qualification suite fields')
    if type(suite['repetitions']) is not int or suite['repetitions'] < 2:
        raise ReadingPackError('qualification needs at least two fresh trials per case')
    if type(suite['minimum_distinct_books']) is not int or suite['minimum_distinct_books'] < 2:
        raise ReadingPackError('qualification needs at least two distinct books')
    if type(suite['minimum_defect_classes']) is not int or suite['minimum_defect_classes'] < 3:
        raise ReadingPackError('qualification needs at least three defect classes')
    threshold = suite['minimum_completion_rate']
    if type(threshold) not in (int, float) or not math.isfinite(threshold) or not 0.5 <= threshold <= 1:
        raise ReadingPackError('qualification requires a fixed observed completion target')
    ids = []
    for case in suite['cases']:
        if set(case) != {'id', 'book_id', 'input_identity', 'expectation', 'defect_class', 'record_ids'}:
            raise ReadingPackError('invalid qualification case fields')
        if not all(isinstance(case[k], str) and case[k] for k in ('id', 'book_id', 'input_identity')):
            raise ReadingPackError('qualification cases require stable input and book identities')
        if case['expectation'] not in {'complete', 'detect-defect'}:
            raise ReadingPackError('invalid qualification expectation')
        if case['expectation'] == 'detect-defect' and (not case['defect_class'] or not case['record_ids']):
            raise ReadingPackError('defect controls require a class and affected record IDs')
        ids.append(case['id'])
    if len(ids) != len(set(ids)) or not ids:
        raise ReadingPackError('qualification case IDs must be unique')


def register_trial(root: Path, suite_path: Path, case_id: str, repetition: int) -> dict:
    from .pipeline import _seal, _unseal, _lock
    suite = json.loads(suite_path.read_text())
    validate_suite(suite)
    with _lock(root):
        manifest, state = (_unseal(root / name) for name in ('manifest.json', 'state.json'))
        if manifest['recipe'].get('operating_envelope', {}).get('mode') != 'qualification':
            raise ReadingPackError('fresh trials require qualification operating mode')
        if state['state'] != 'ready' or any((root / 'jobs').glob('*.json')) or (root / 'qualification-trial.json').exists():
            raise ReadingPackError('qualification must be registered before any worker call')
        if 'restart_origin' in manifest or 'reassessment_of' in manifest or (root / 'reused-preparation.json').exists():
            raise ReadingPackError('qualification trials must start fresh')
        from .pipeline_contracts import manifest_contract
        if (manifest_contract(manifest) == 'artifact-acceptance-1') != (suite.get('contract_version') == 'artifact-acceptance-1'):
            raise ReadingPackError('study contract differs from run')
        if workflow_signature(manifest) != suite['workflow_signature']:
            raise ReadingPackError('qualification workflow identity differs')
        case = next((c for c in suite['cases'] if c['id'] == case_id), None)
        if case is None or input_identity(manifest) != case['input_identity']:
            raise ReadingPackError('qualification input identity differs')
        if type(repetition) is not int or not 0 <= repetition < suite['repetitions']:
            raise ReadingPackError('invalid qualification repetition')
        record = {'suite': suite, 'suite_sha256': artifact_hash(suite),
                  'case_id': case_id, 'repetition': repetition,
                  'manifest_sha256': artifact_hash(manifest)}
        _seal(root / 'qualification-trial.json', record)
        return record


def wilson_lower(successes: int, total: int) -> float:
    """One-sided 95% lower bound; descriptive, not a generalization guarantee."""
    if not total:
        return 0.0
    z = 1.6448536269514722
    p = successes / total
    return (p + z*z/(2*total) - z*math.sqrt(p*(1-p)/total + z*z/(4*total*total))) / (1+z*z/total)


def _percentile(values: list[float], fraction: float) -> float | None:
    return sorted(values)[max(0, math.ceil(len(values)*fraction)-1)] if values else None


def assess_qualification(suite: dict, roots: list[Path]) -> dict:
    from .pipeline import _unseal, verify_candidate, Runner
    from .pipeline_resources import adapter_receipt
    validate_suite(suite)
    if suite.get('contract_version') == 'artifact-acceptance-1':
        from .pipeline_acceptance_study import assess_study
        return assess_study(suite, roots)
    expected = {(c['id'], n): c for c in suite['cases'] for n in range(suite['repetitions'])}
    trials, seen, evidence, reasons = [], set(), {}, []
    usage_receipts = set()
    source_modes = defaultdict(list)
    for root in roots:
        root = root.resolve()
        trial = _unseal(root / 'qualification-trial.json')
        manifest = _unseal(root / 'manifest.json')
        Runner(root)  # Revalidate frozen source bytes, seed, normalized chunks and adapter files.
        key = (trial['case_id'], trial['repetition'])
        if key not in expected or key in seen or trial['suite_sha256'] != artifact_hash(suite) or trial['suite'] != suite:
            raise ReadingPackError('unexpected, duplicate or rebound qualification trial')
        if trial['manifest_sha256'] != artifact_hash(manifest) or workflow_signature(manifest) != suite['workflow_signature']:
            raise ReadingPackError('qualification manifest changed')
        case = expected[key]
        if input_identity(manifest) != case['input_identity']:
            raise ReadingPackError('qualification source changed')
        seen.add(key)
        state = _unseal(root / 'state.json')
        resources = resource_status(root)
        jobs = [_unseal(p) for p in (root / 'jobs').glob('*.json')]
        ledger = _unseal(root / 'resource-ledger.json') if resources['measured'] else None
        fresh = bool(jobs) and not any(j.get('reused_from') for j in jobs) and 'restart_origin' not in manifest
        live = bool(ledger and ledger['reservations']) and fresh
        attempted = Counter(j['request']['request_id'] for j in jobs for _ in j['attempts'])
        reserved = Counter(i['request_id'] for i in (ledger or {}).get('reservations', []))
        if attempted != reserved:
            live = False
        for item in (ledger or {}).get('reservations', []):
            job = next((j for j in jobs if j['request']['request_id'] == item['request_id']), None)
            if job is None or not item.get('receipt_path') or not item.get('receipt_valid'):
                live = False
                continue
            path = Path(item['receipt_path'])
            if path.resolve() in usage_receipts:
                raise ReadingPackError('qualification trials reused a provider receipt')
            usage_receipts.add(path.resolve())
            if hashlib.sha256(path.read_bytes()).hexdigest() != item['receipt_sha256']:
                raise ReadingPackError('qualification usage receipt changed')
            # Recheck the actual raw stream through the trusted adapter audit.
            receipt = adapter_receipt(['--audit-dir', str(path.parent.parent)], item['request_id'])
            if receipt['actual_cost_usd'] != item['actual_cost_usd'] or not receipt['receipt_valid']:
                raise ReadingPackError('qualification cost receipt changed')
            evidence[str(path)] = item['receipt_sha256']
        completed = state['state'] == 'awaiting_author_approval'
        if completed:
            verify_candidate(root)
            final = _unseal(root / 'final-evaluation.json')
            completed = bool(final['passed'] and not final['failures'] and not final['critical_errors'])
        findings = [f for report in state.get('rounds', []) for f in report['failures']]
        detected = any(f.get('criterion', f.get('category')) == case['defect_class'] and
                       set(case['record_ids']) <= set(f.get('record_ids', [])) for f in findings)
        within = bool(resources['measured'] and ledger['finished_at'] is not None and
                      resources['unknown_cost_calls'] == 0 and
                      resources['elapsed_seconds'] <= manifest['recipe']['operating_envelope']['max_wall_seconds'] and
                      float(resources['known_reported_usd']) <= manifest['recipe']['operating_envelope']['max_cost_usd'] and
                      not any(i['actual_cost_usd'] is not None and float(i['actual_cost_usd']) > float(i['allowance_usd']) for i in ledger['reservations']))
        accepted = live and within and (completed if case['expectation'] == 'complete' else detected)
        trials.append({'case_id': key[0], 'repetition': key[1], 'book_id': case['book_id'],
                       'expectation': case['expectation'], 'state': state['state'], 'fresh_live_measurement': live,
                       'complete': completed, 'defect_detected': detected, 'within_envelope': within,
                       'accepted': accepted, 'elapsed_seconds': resources.get('elapsed_seconds'),
                       'reported_usd': resources.get('known_reported_usd'), 'run': str(root)})
        if case['expectation'] == 'complete' and accepted:
            chunks = _unseal(root / 'chunks.json')['chunks']
            mode = artifact_hash({'format': manifest['sources'][0]['format'], 'language': manifest['language'],
                'roles': sorted(s['role'] for s in manifest['sources'][1:]), 'seeded': manifest['seed'] is not None})
            source_modes[mode].append({'characters':sum(c['end']-c['start'] for c in chunks),
                'profile':state.get('effective_profile',manifest['recipe']['profile'])})
        for name in ('manifest.json', 'qualification-trial.json', 'resource-ledger.json', 'state.json'):
            path = root / name
            if path.exists():
                evidence[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    clean = [r for r in trials if r['expectation'] == 'complete']
    controls = [r for r in trials if r['expectation'] == 'detect-defect']
    successes = sum(r['accepted'] for r in clean)
    lower = wilson_lower(successes, len(clean))
    books = {c['book_id'] for c in suite['cases'] if c['expectation'] == 'complete'}
    identities = {c['input_identity'] for c in suite['cases'] if c['expectation'] == 'complete'}
    classes = {c['defect_class'] for c in suite['cases'] if c['expectation'] == 'detect-defect'}
    if set(expected) != seen:
        reasons.append('preregistered trials are incomplete; omitted and failed trials cannot be excluded')
    if min(len(books), len(identities)) < suite['minimum_distinct_books']:
        reasons.append('insufficient distinct clean inputs')
    if len(classes) < suite['minimum_defect_classes']:
        reasons.append('insufficient defect classes')
    if not trials or not all(r['accepted'] for r in controls) or not all(r['fresh_live_measurement'] and r['within_envelope'] for r in trials):
        reasons.append('completion, defect detection, fresh measurement or resource target failed')
    if not clean or successes / len(clean) < suite['minimum_completion_rate']:
        reasons.append('preregistered observed completion target was not met')
    return {'schema_version': 1, 'qualified': not reasons, 'reasons': reasons,
            'suite': suite, 'suite_sha256': artifact_hash(suite),
            'workflow_signature': suite['workflow_signature'], 'trials': trials,
            'completion_rate': successes / len(clean) if clean else None,
            'completion_rate_lower_95_descriptive_only': lower, 'defect_detection_rate':
                sum(r['accepted'] for r in controls)/len(controls) if controls else None,
            'p50_seconds': _percentile([r['elapsed_seconds'] for r in clean if r['elapsed_seconds'] is not None], .5),
            'p95_seconds': _percentile([r['elapsed_seconds'] for r in clean if r['elapsed_seconds'] is not None], .95),
            'observed_max_reported_usd': max((float(r['reported_usd']) for r in trials if r['reported_usd'] is not None), default=None),
            'supported_input_modes': {k: {'maximum_measured_source_characters': max(x['characters'] for x in v),
                'profiles':sorted({x['profile'] for x in v})} for k, v in source_modes.items()},
            'evidence_sha256': evidence,
            'limits': 'Qualification applies only to the measured recipe, engine, adapters and input envelope. Repeated trials on the same books are correlated. The Wilson value assumes independent trials and is descriptive only, never an admission criterion or evidence of generalization to arbitrary books.',
            'author_adoption': False}


def attach_qualification(root: Path, report_path: Path) -> None:
    from .pipeline import _seal, _unseal, _lock
    with _lock(root):
        if _unseal(root / 'state.json')['state'] != 'ready' or any((root / 'jobs').glob('*.json')):
            raise ReadingPackError('attach qualification before execution')
        if (root / 'workflow-qualification.json').exists():
            raise ReadingPackError('refusing to overwrite workflow qualification')
        _seal(root / 'workflow-qualification.json', _unseal(report_path))


def verify_qualification(runner) -> dict:
    from .pipeline import _unseal
    path = runner.root / 'workflow-qualification.json'
    if not path.exists():
        raise ResourceLimit('workflow_unqualified', 'no measured workflow qualification; production sends are disabled')
    report = _unseal(path)
    if workflow_signature(runner.manifest) != report['workflow_signature']:
        raise ResourceLimit('workflow_unqualified', 'engine, model, adapter or operating recipe differs from qualification')
    actual = assess_qualification(report['suite'], [Path(t['run']) for t in report['trials']])
    if actual != report or not actual['qualified']:
        raise ResourceLimit('workflow_unqualified', 'qualification is incomplete, failed, changed or not based on fresh model trials')
    manifest = runner.manifest
    mode = artifact_hash({'format': manifest['sources'][0]['format'], 'language': manifest['language'],
        'roles': sorted(s['role'] for s in manifest['sources'][1:]), 'seeded': manifest['seed'] is not None})
    measured = report['supported_input_modes'].get(mode)
    size = sum(c['end']-c['start'] for c in runner.chunks)
    if not measured or size > measured['maximum_measured_source_characters']:
        raise ResourceLimit('input_outside_envelope', 'input format, source roles or size exceed measured qualification')
    return {'report':report,'mode':mode}
