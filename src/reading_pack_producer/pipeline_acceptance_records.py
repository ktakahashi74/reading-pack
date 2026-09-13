"""M1 report serialization and hash-bound, local author decisions."""
from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path

from reading_pack.errors import ReadingPackError
from reading_pack.hashing import file_hash
from reading_pack.schema_validation import require_structure
from .pipeline_acceptance import _save_once, _read_ref, _safe
from .work_ledger import artifact_hash


def reference(root, path):
    relative = path.relative_to(root).as_posix()
    path = _safe(root, relative)
    return {'path': relative, 'sha256': file_hash(path.read_bytes())}


def verify_record(root, ref):
    from .pipeline import _inventory
    from .pipeline_acceptance_ledger import summary
    value = _read_ref(root, ref)
    require_structure("artifact-acceptance-report.schema.json", value, label='artifact report')
    datetime.fromisoformat(value['created_at'].replace('Z', '+00:00'))
    def references(node):
        if isinstance(node, dict):
            if set(node) == {'path', 'sha256'}:
                path = _safe(root, node['path'])
                if not path.is_file() or file_hash(path.read_bytes()) != node['sha256']:
                    raise ReadingPackError('acceptance referenced evidence changed or missing')
            else:
                for child in node.values(): references(child)
        elif isinstance(node, list):
            for child in node: references(child)
    references(value)
    candidate_ref = value['bindings']['candidate_manifest']
    if candidate_ref:
        candidate = _read_ref(root, candidate_ref)
        if _inventory(_safe(root, candidate['root'])) != candidate['files']:
            raise ReadingPackError('acceptance candidate manifest changed')
    sources = _read_ref(root, value['bindings']['source_manifest'])
    if _inventory(root / 'inputs') != sources['files']:
        raise ReadingPackError('acceptance source manifest changed')
    delivery_ref = value['bindings']['delivery_manifest']
    if delivery_ref:
        delivery = _read_ref(root, delivery_ref)
        directory = _safe(root, delivery_ref['path']).parent / 'project'
        if _inventory(directory) != delivery['project_files']:
            raise ReadingPackError('acceptance delivery manifest changed')
    if value['acceptance']['report']:
        inspection = _read_ref(root, value['acceptance']['report'])
        inventory = _read_ref(root, inspection['inventory'])
        _read_ref(root, inspection['plan'])
        measured = summary(inventory, inspection['ledger'])
        for attempt in inspection['ledger']['attempts']:
            evidence = _read_ref(root, attempt['evidence_ref'])
            if evidence['results'] != attempt['results']:
                raise ReadingPackError('acceptance ledger evidence differs')
            if evidence.get('worker_exchange'):
                _read_ref(root, evidence['worker_exchange'])
        if inspection['acceptance'] != measured or any(value['acceptance'][k] != measured[k] for k in ('status','coverage')):
            raise ReadingPackError('acceptance summary differs from inspection evidence')
        if value['acceptance']['confirmed_defect_ids'] != finding_ids(measured['confirmed_defects'], 'defect') or value['acceptance']['unresolved_ids'] != finding_ids(measured['unresolved_suspicions'], 'unresolved'):
            raise ReadingPackError('acceptance finding IDs differ')
    author = value['author_approval']
    if author['status'] != 'not_requested':
        if author['candidate_manifest_sha256'] != candidate_ref['sha256']:
            raise ReadingPackError('author decision candidate binding differs')
        packet = _read_ref(root, author['review'])
        if packet['candidate_manifest'] != candidate_ref:
            raise ReadingPackError('author review candidate differs')
        if author['decision']:
            decision = _read_ref(root, author['decision'])
            if decision['candidate_manifest_sha256'] != candidate_ref['sha256'] or decision['decision'] != author['status']:
                raise ReadingPackError('author decision evidence differs')
    return value


def finding_ids(values, kind):
    return list(dict.fromkeys(kind + '-' + artifact_hash(v)[:24] for v in values))


def write_record(runner, inspection=None, *, stop=None, repair=None, ask_author=False):
    from .pipeline import _inventory, _unseal
    from .pipeline_acceptance_delivery import template_binding
    root = runner.root
    paths = sorted((root / 'acceptance' / 'records').glob('*.json'))
    previous = reference(root, paths[-1]) if paths else None
    if previous:
        verify_record(root, previous)
    number = len(paths)
    base = f'acceptance/evidence/{number:06d}'
    bindings = {'run_manifest': reference(root, root / 'manifest.json'),
                'candidate_manifest': None,
                'source_manifest': _save_once(root, base + '/sources.json', {'sources': runner.manifest['sources'], 'files': runner.manifest['inputs']}),
                'instruction_template': _save_once(root, base + '/template.json', template_binding(runner.lang)),
                'acceptance_criteria': _save_once(root, base + '/criteria.json', {'contract_version': runner.contract_version, 'recipe': runner.recipe}),
                'delivery_manifest': None}
    acceptance = {'status': 'not_run', 'coverage': 'not_run', 'confirmed_defect_ids': [], 'unresolved_ids': [], 'report': None}
    approval = {'status': 'not_requested', 'candidate_manifest_sha256': None, 'review': None, 'decision': None, 'question_ids': []}
    execution = {'status': 'stopped' if stop else 'ready', 'stop_reasons': [stop] if stop else []}
    if inspection:
        candidate = _safe(root, inspection['candidate'])
        bindings['candidate_manifest'] = _save_once(root, base + '/candidate.json', {'root': inspection['candidate'], 'files': _inventory(candidate)})
        delivery = _safe(root, inspection['delivery'])
        if delivery.exists(): bindings['delivery_manifest'] = reference(root, delivery)
        measured = inspection['acceptance']
        acceptance.update(status=measured['status'], coverage=measured['coverage'],
            confirmed_defect_ids=finding_ids(measured['confirmed_defects'], 'defect'),
            unresolved_ids=finding_ids(measured['unresolved_suspicions'], 'unresolved'),
            report=inspection['report'] if measured['coverage'] != 'not_run' else None)
        if not stop:
            execution['status'] = inspection['execution']['status']
            if execution['status'] == 'stopped':
                reason = inspection['execution']['reason'].split(':', 1)[0]
                execution['stop_reasons'] = [reason if reason in {'deadline_exceeded','budget_exhausted','phase_budget_exhausted','cost_unknown','cost_allowance_exceeded','superseded'} else 'provider_error']
        if ask_author and measured['status'] != 'not_run':
            questions = acceptance['confirmed_defect_ids'] + acceptance['unresolved_ids'] + ['pending-' + c for c in measured['pending_check_ids']]
            packet = {'candidate_manifest': bindings['candidate_manifest'], 'inspection': inspection['report'],
                      'acceptance': measured, 'repair': repair or {'performed': False},
                      'questions': questions, 'automatic_approval': False,
                      'decision_format': {'candidate_manifest_sha256': bindings['candidate_manifest']['sha256'],
                          'reviewer': '', 'decided_at': '', 'decision': 'approved|rejected|changes_requested',
                          'scope': 'this exact candidate only; no publication'}}
            packet_ref = _save_once(root, base + '/author-review.json', packet)
            approval.update(status='pending' if measured['status'] == 'pass' else 'needs_decision',
                            candidate_manifest_sha256=bindings['candidate_manifest']['sha256'],
                            review=packet_ref, question_ids=questions)
    ledger_path = root / 'resource-ledger.json'
    ledger = _unseal(ledger_path) if ledger_path.exists() else {'operating_envelope': None,
        'calls_reserved': runner.calls, 'actual_cost_usd': None, 'cost_kind': 'unknown; no provider receipt'}
    resources = {'budget_scope_id': runner.manifest.get('budget_scope_id', artifact_hash(runner.manifest['sources'])),
                 'ledger': _save_once(root, base + '/resources.json', ledger)}
    value = {'contract_version': runner.contract_version, 'record_id': f'{number:06d}',
             'created_at': datetime.now(timezone.utc).isoformat(), 'previous_record': previous,
             'reassessment_of': runner.manifest.get('reassessment_of'),
             'bindings': bindings, 'acceptance': acceptance, 'execution': execution,
             'model_diagnostics': {'status': 'not_run', 'report': None}, 'author_approval': approval,
             'resources': resources}
    require_structure("artifact-acceptance-report.schema.json", value, label='artifact report')
    ref = _save_once(root, f'acceptance/records/{number:06d}.json', value)
    verify_record(root, ref)
    return ref


def record_author_decision(root, decision_file):
    from .pipeline import _lock, Runner, pipeline_status
    with _lock(root):
        runner = Runner(root)
        ref = runner.state.get('acceptance_record')
        if not ref: raise ReadingPackError('no artifact author packet')
        old = verify_record(root, ref)
        if old['author_approval']['status'] not in {'pending', 'needs_decision'}:
            raise ReadingPackError('no pending author decision')
        if decision_file is None:
            return pipeline_status(root)
        decision = json.loads(decision_file.read_text())
        if set(decision) != {'reviewer','decided_at','decision','scope','candidate_manifest_sha256'} or any(not isinstance(v,str) or not v.strip() for v in decision.values()):
            raise ReadingPackError('author decision requires reviewer, date, scope, exact candidate hash and decision')
        if decision['decision'] not in {'approved','rejected','changes_requested'}:
            raise ReadingPackError('invalid author decision')
        when = datetime.fromisoformat(decision['decided_at'].replace('Z','+00:00'))
        if when.utcoffset() is None: raise ReadingPackError('author decision date needs timezone')
        if decision['candidate_manifest_sha256'] != old['bindings']['candidate_manifest']['sha256']:
            raise ReadingPackError('author decision targets another candidate')
        paths = sorted((root / 'acceptance' / 'records').glob('*.json'))
        number = len(paths)
        evidence = _save_once(root, f'acceptance/evidence/{number:06d}/author-decision.json', decision)
        value = copy.deepcopy(old)
        value.update(record_id=f'{number:06d}', created_at=datetime.now(timezone.utc).isoformat(), previous_record=ref)
        value['author_approval'].update(status=decision['decision'], decision=evidence)
        new_ref = _save_once(root, f'acceptance/records/{number:06d}.json', value)
        verify_record(root,new_ref)
        runner.save('artifact_completed', acceptance_record=new_ref, acceptance=value['acceptance'],
                    author_approval=value['author_approval'], execution=value['execution'])
        return pipeline_status(root)
