"""Finite direct production: generation, inspection, at most one repair, author packet."""
from __future__ import annotations

import copy
import shutil

from reading_pack.errors import ReadingPackError
from reading_pack.project import load_language_data
from .pipeline_acceptance import inspect_artifact, _read_ref, _save_once
from .pipeline_acceptance_records import write_record, verify_record
from .work_ledger import artifact_hash


def repair_findings(runner, report):
    inventory = _read_ref(runner.root, report['inventory'])
    checks = {c['id']: c for c in inventory['checks']}
    targets = {t['id']: t for t in inventory['targets']}
    findings = []
    for defect in report['acceptance']['confirmed_defects']:
        check = checks[defect['check_id']]
        target = targets[check['target_id']]
        # Instruction/delivery defects require developer/author decisions. They
        # must not silently grant a content worker permission to edit templates.
        if check['method'] != 'semantic' or check['criterion'].startswith('instruction'):
            continue
        record = target.get('record', {})
        rid = target.get('record_id') or record.get('id')
        refs = [e for e in defect['evidence'] if e.get('source_id') in {s['id'] for s in runner.manifest['sources']}]
        if not refs: continue
        findings.append({'category': 'missing_coverage' if check['criterion'] == 'chapter_orientation' else 'other',
                         'criterion': 'required_coverage' if check['criterion'] == 'chapter_orientation' else check['criterion'],
                         'classification': 'blocking', 'record_ids': [rid] if rid else [],
                         'reason': defect['reason'], 'evidence': refs, 'reader_impact': defect['reader_impact'],
                         'requirement_ids': [check['id']]})
    return findings


def _snapshot(runner, working, name):
    from .pipeline import _inventory, _unseal
    target = runner.root / 'acceptance' / 'production' / name
    marker = target.parent / (name + '-inventory.json')
    if marker.exists():
        saved = _unseal(marker)
        if _inventory(target) != saved['files']:
            raise ReadingPackError('production checkpoint changed')
    else:
        if target.exists(): raise ReadingPackError('incomplete production checkpoint')
        temporary = target.with_name(target.name + '-saving')
        if temporary.exists(): shutil.rmtree(temporary)
        target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        shutil.copytree(working,temporary)
        temporary.rename(target)
        _save_once(runner.root, marker.relative_to(runner.root).as_posix(), {'files': _inventory(target)})
    return target


def run_artifact_workflow(runner):
    from .pipeline import _unseal, pipeline_status, PipelineStop
    from .pipeline_resources import ResourceLimit, operating_plan, populated_seed
    if runner.state.get('acceptance_record'):
        verify_record(runner.root, runner.state['acceptance_record'])
        if runner.state['state'] in {'awaiting_author_approval','artifact_completed','needs_author_decision'}:
            return pipeline_status(runner.root)
    report = None
    repair = {'performed': False}
    runner.save('running')
    try:
        planned = operating_plan(runner.manifest, runner.chunks, seed_populated=populated_seed(runner.root, runner.manifest))
        if not planned['admitted']:
            raise ResourceLimit('input_outside_envelope', '; '.join(planned['reasons']))
        first = runner.root / 'acceptance' / 'production' / 'initial'
        if not first.exists():
            working = runner.root / 'artifact-working'
            if working.exists(): shutil.rmtree(working)
            runner.bootstrap(working)
            runner.reconcile_source_structure(working)
            runner.generate(working, 0, [])
            first = _snapshot(runner, working, 'initial')
        else:
            first = _snapshot(runner, first, 'initial')
        report = inspect_artifact(runner.root, first, experimental=True, _runner=runner)
        if report['execution']['status'] == 'completed' and report['acceptance']['coverage'] == 'complete' and report['acceptance']['confirmed_defects'] and runner.recipe['max_rounds'] > 1:
            findings = repair_findings(runner, report)
            from .pipeline_generation import author_contract, protection_reason
            canonical = load_language_data(first, runner.lang)
            contract = author_contract(first,runner.lang)
            indexed = {r['id']:(collection,r) for collection, rows in canonical.items() if isinstance(rows,list) for r in rows if isinstance(r,dict) and 'id' in r}
            # A protected target is escalated on the single final form, never
            # converted into an automatic author-input modification.
            def fixed(rid):
                if rid not in indexed:
                    return False
                collection, record = indexed[rid]
                permission = any(rid in rule.get('draft_revision_ids', []) for rule in contract.values())
                if record.get('status') == 'approved' and not permission:
                    return True
                if protection_reason({'collection': collection, 'record': record}, canonical, contract):
                    return True
                if collection == 'chapters':
                    from reading_pack_review.author_input import FIELD_MODULES
                    from .pipeline_generation import record_protected
                    return any(module in contract and record_protected(contract[module], rid)
                               for module in FIELD_MODULES)
                return False
            editable = [f for f in findings if not any(fixed(rid) for rid in f['record_ids'])]
            if editable:
                frozen = {'initial_report': report['inspection_id'], 'findings': editable,
                          'repair_rounds': 1, 'reinspection_scope': 'entire candidate; dependency completeness not assumed'}
                _save_once(runner.root, 'acceptance/repair-plan.json', frozen)
                repaired = runner.root / 'acceptance' / 'production' / 'repaired'
                if not repaired.exists():
                    working = runner.root / 'artifact-repair-working'
                    if working.exists(): shutil.rmtree(working)
                    shutil.copytree(first,working)
                    runner.generate(working, 1, editable)
                    repaired = _snapshot(runner,working,'repaired')
                else:
                    repaired = _snapshot(runner,repaired,'repaired')
                before = load_language_data(first,runner.lang)
                after = load_language_data(repaired,runner.lang)
                repair = {'performed': True, 'rounds': 1, 'before_sha256':artifact_hash(before),
                          'after_sha256':artifact_hash(after), 'reinspection_scope':'entire candidate',
                          'changes': {k:{'before':before.get(k),'after':after.get(k)} for k in set(before)|set(after) if before.get(k)!=after.get(k)}}
                _save_once(runner.root, 'acceptance/repair-result.json', repair)
                report = inspect_artifact(runner.root,repaired,experimental=True,_runner=runner,phase='final')
        ref = write_record(runner,report,repair=repair,ask_author=True)
        saved = verify_record(runner.root,ref)
        state = 'awaiting_author_approval' if saved['author_approval']['status']=='pending' else 'needs_author_decision'
        if saved['execution']['status'] == 'stopped':
            state = 'artifact_stopped'
        runner.save(state,acceptance_record=ref,acceptance=saved['acceptance'],execution=saved['execution'],
                    author_approval=saved['author_approval'],model_diagnostics=saved['model_diagnostics'])
    except (PipelineStop,ResourceLimit,ReadingPackError,OSError) as exc:
        reason = getattr(exc,'state','input_constraint')
        if reason not in {'deadline_exceeded','budget_exhausted','phase_budget_exhausted','cost_unknown','cost_allowance_exceeded','superseded'}:
            reason = 'integrity_error' if 'changed' in str(exc) else 'input_constraint'
        ref = write_record(runner,report,stop=reason,repair=repair,ask_author=report is not None)
        saved = verify_record(runner.root,ref)
        runner.save('artifact_stopped',str(exc),acceptance_record=ref,acceptance=saved['acceptance'],
                    execution=saved['execution'],author_approval=saved['author_approval'])
    return pipeline_status(runner.root)
