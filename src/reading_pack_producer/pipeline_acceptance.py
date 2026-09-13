"""Bounded content, instruction and delivery inspections of a frozen candidate.

The workflow wraps these detailed reports in separate M1 author-gate records.
"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path

from reading_pack.errors import ReadingPackError
from reading_pack.hashing import file_hash
from reading_pack.profiles import PROFILES, load_quality_plan
from reading_pack.project import load_config, load_language_data
from reading_pack.validation import errors, validate_project
from .pipeline_contracts import ARTIFACT_CONTRACT_VERSION
from .work_ledger import artifact_hash


def _safe(root: Path, relative: str) -> Path:
    parts = relative.split('/')
    if not relative or any(p in {'', '.', '..'} for p in parts) or '\\' in relative or ':' in relative:
        raise ReadingPackError('unsafe inspection evidence path')
    path = root.joinpath(*parts)
    for parent in (path, *path.parents):
        if parent == root:
            break
        if parent.is_symlink():
            raise ReadingPackError('inspection evidence must not contain symlinks')
    if not path.resolve().is_relative_to(root.resolve()):
        raise ReadingPackError('inspection evidence escapes run')
    return path


def _save_once(root: Path, relative: str, value: dict) -> dict:
    from .pipeline import _seal, _unseal
    path = _safe(root, relative)
    if path.exists():
        if _unseal(path) != value:
            raise ReadingPackError('immutable inspection evidence changed: ' + relative)
    else:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        _seal(path, value)
    return {'path': relative, 'sha256': file_hash(path.read_bytes())}


def _read_ref(root: Path, reference: dict) -> dict:
    from .pipeline import _unseal
    path = _safe(root, reference['path'])
    try:
        if file_hash(path.read_bytes()) != reference['sha256']:
            raise ReadingPackError('inspection evidence hash changed')
        return _unseal(path)
    except OSError as exc:
        raise ReadingPackError('inspection evidence is missing or unreadable') from exc


def _verify_exchange(runner, batch: dict, reference: dict, response: dict, key: str) -> None:
    from .pipeline_evidence import worker_payload, resolve_references
    from jsonschema import Draft202012Validator
    value = _read_ref(runner.root, reference)
    request, envelope = value['request'], value['response']
    payload, spans = worker_payload({**batch['payload'], 'checks': batch['checks']})
    expected_model = runner.recipe['workers']['judge']['model']
    if (request['stage'] != batch.get('stage', 'artifact_content') or request['job'] != key or
            request['payload'] != payload or request['recipe_sha256'] != runner.recipe_sha256 or
            request['model'] != expected_model or
            request['request_id'] != artifact_hash({k: v for k, v in request.items() if k != 'request_id'}) or
            not isinstance(envelope, dict) or envelope.get('request_id') != request['request_id'] or
            envelope.get('model') != expected_model):
        raise ReadingPackError('inspection worker exchange binding changed')
    if list(Draft202012Validator(request['response_schema']).iter_errors(envelope)):
        raise ReadingPackError('inspection saved worker response is invalid')
    if resolve_references(envelope['result'], spans) != response:
        raise ReadingPackError('inspection response differs from its worker exchange')


def _result(check: dict, *, reason: str, evidence: list, defect=False, incomplete=False) -> dict:
    return {'check_id': check['id'], 'status': 'incomplete' if incomplete else 'complete',
            'outcome': 'defect' if defect else 'unresolved' if incomplete else 'pass',
            'reason': reason, 'evidence': evidence,
            'reader_impact': reason if defect or incomplete else ''}


def _local_plan(profile: str) -> tuple[list, list]:
    targets, checks = [], []
    criteria = ['project_structure', 'source_binding', 'source_locators', 'length_limits']
    criteria += ['module:' + name for name in PROFILES[profile].required_modules]
    for criterion in criteria:
        target = 'requirement:' + criterion
        targets.append({'id': target, 'kind': 'requirement', 'requirement_id': target})
        checks.append({'id': 'mechanical:' + criterion, 'target_id': target,
                       'criterion': criterion, 'method': 'mechanical', 'source_ranges': []})
    return targets, checks


def _mechanical(runner, project: Path, canonical: dict, checks: list[dict]) -> list[dict]:
    from .pipeline import evaluation_pack
    results = []
    config = load_config(project)
    _, _, issues = validate_project(project)
    structural = [issue.format() for issue in errors(issues)]
    primary = next(s for s in runner.manifest['sources'] if s['role'] == 'primary-book')
    limits = {s['name']: [] for s in runner.manifest['sources']}
    for source in runner.manifest['sources']:
        limits[source['name']].append([source['id'], max(c['end'] for c in runner.chunks if c['source_id'] == source['id'])])
    invalid_locations, unresolved_locations = [], []
    for collection, records in canonical.items():
        if not isinstance(records, list):
            continue
        for index, record in enumerate(records):
            if not isinstance(record, dict):
                continue
            locations = record.get('source_locations', [])
            if not isinstance(locations, list):
                invalid_locations.append({'target': f'{collection}/{index}', 'locator': locations})
                continue
            for locator in locations:
                if not isinstance(locator, str):
                    invalid_locations.append({'target': f'{collection}/{index}', 'locator': locator})
                    continue
                match = re.fullmatch(r'(.+)#normalized-text:(\d+)-(\d+)', locator)
                if match:
                    candidates = limits.get(match[1], [])
                    if len(candidates) != 1 or not 0 <= int(match[2]) < int(match[3]) <= candidates[0][1]:
                        invalid_locations.append({'target': f'{collection}/{index}', 'locator': locator})
                elif '#' in locator:
                    name, anchor = locator.split('#', 1)
                    sources = limits.get(name, [])
                    if len(sources) != 1 or not anchor:
                        unresolved_locations.append({'target': f'{collection}/{index}', 'locator': locator})
                    else:
                        # Literal presence is necessary, not sufficient to prove the
                        # semantic destination. The semantic source check owns that.
                        sid = sources[0][0]
                        if not any(anchor in c['text'] for c in runner.chunks if c['source_id'] == sid):
                            unresolved_locations.append({'target': f'{collection}/{index}', 'locator': locator})
                else:
                    unresolved_locations.append({'target': f'{collection}/{index}', 'locator': locator})
    for check in checks:
        criterion = check['criterion']
        details, defect, incomplete = {}, False, False
        reason = 'The fixed mechanical requirement was checked.'
        if criterion == 'project_structure':
            details = {'issues': structural}
            defect = bool(structural)
            reason = 'Project Schema and reference validation: ' + ('; '.join(structural) if structural else 'no structural errors')
        elif criterion == 'source_binding':
            details = {'candidate_source': canonical.get('source'), 'frozen_source': primary}
            source_value = canonical.get('source')
            defect = not isinstance(source_value, dict) or any(source_value.get(k) != primary.get(k) for k in ('name', 'sha256', 'format'))
            reason = 'Candidate source metadata ' + ('differs from' if defect else 'matches') + ' the frozen primary source.'
        elif criterion == 'source_locators':
            details = {'invalid': invalid_locations, 'unresolved': unresolved_locations,
                       'source_lengths': limits}
            defect, incomplete = bool(invalid_locations), bool(unresolved_locations)
            reason = ('A normalized-text locator is outside its registered source.' if defect else
                      'Some free-form or anchor locators cannot be established mechanically.' if incomplete else
                      'Every supplied normalized locator resolves; semantic correspondence is checked separately.')
        elif criterion == 'length_limits':
            try:
                pack = evaluation_pack(project, runner.lang, config, canonical)
                details = {'pack_characters': len(pack), 'max_pack_characters': runner.recipe['max_pack_characters'],
                           'max_summary_characters': runner.recipe['max_summary_characters'],
                           'overlong_chapters': [r['id'] for r in canonical.get('chapters', [])
                                                if len(r.get('summary', '')) > runner.recipe['max_summary_characters']]}
                defect = len(pack) > runner.recipe['max_pack_characters'] or bool(details['overlong_chapters'])
                reason = 'The proposed Pack and chapter summaries ' + ('exceed' if defect else 'satisfy') + ' the frozen length limits.'
            except (KeyError, TypeError, ReadingPackError) as exc:
                details, incomplete = {'render_error': str(exc)}, True
                reason = 'Invalid candidate content prevents the length check.'
        elif criterion.startswith('module:'):
            module = criterion.split(':', 1)[1]
            details = {'module': module, 'count': len(canonical.get(module, [])) if isinstance(canonical.get(module), list) else 0}
            defect = not details['count']
            reason = 'Required module ' + module + (' is empty or missing.' if defect else ' is present; semantic adequacy is checked separately.')
        elif criterion == 'metadata_binding':
            details = {'canonical_sha256': artifact_hash(canonical), 'language': runner.lang}
            # Project Schema/source checks cover metadata. This is a binding
            # check only, never semantic evidence for claims in the book.
            defect = bool(structural)
            reason = 'Metadata is bound to the frozen candidate and checked by project validation.'
        else:
            raise ReadingPackError('unknown mechanical inspection criterion: ' + criterion)
        results.append(_result(check, reason=reason, evidence=[{'method': 'controller', 'details': details}],
                               defect=defect, incomplete=incomplete))
    return results


def inspect_artifact(root: Path, project: Path, *, prepare_only=False, experimental=False,
                     retry_inflight=False, _runner=None, phase='development') -> dict:
    """Freeze a plan, then inspect through the existing bounded adapter transport.

    Resource envelopes reserve independent initial and final inspection phases.
    Explicit experiments still retain their call and transport limits.
    """
    from .pipeline import Runner, _lock, _inventory, _copy_seed, _unseal, PipelineStop
    from .pipeline_acceptance_content import build_content_plan, validate_content_results
    from .pipeline_acceptance_ledger import freeze_inventory, new_ledger, apply_results, summary
    from .pipeline_resources import ResourceLimit
    root, project = root.resolve(), project.resolve()
    from contextlib import nullcontext
    with (_lock(root) if _runner is None else nullcontext()):
        runner = _runner or Runner(root, retry_inflight=retry_inflight)
        if runner.contract_version != ARTIFACT_CONTRACT_VERSION:
            raise ReadingPackError('direct inspection requires artifact-acceptance-1; old runs remain unchanged')
        if not prepare_only and not runner.recipe.get('operating_envelope') and not experimental:
            raise ReadingPackError('inspection without an operating envelope requires --experimental')
        profile = runner.recipe['profile']
        if profile not in PROFILES:
            raise ReadingPackError('inspection requires a fixed profile; auto selection is not supported')
        if load_quality_plan(project)['profile'] != profile:
            raise ReadingPackError('candidate profile differs from the frozen recipe')
        files = _inventory(project, exclude=('.git', '.reading-pack', 'dist', '__pycache__'))
        if not files:
            raise ReadingPackError('empty inspection candidate')
        identifier = artifact_hash({'candidate_files': files, 'run_manifest': artifact_hash(runner.manifest), 'phase': phase})
        base = 'acceptance/inspections/' + identifier
        candidate = _safe(root, base + '/candidate')
        if not candidate.exists():
            candidate.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            _copy_seed(project, candidate)
        if _inventory(candidate) != files:
            raise ReadingPackError('inspection candidate snapshot changed')
        canonical = load_language_data(candidate, runner.lang)
        plan = build_content_plan(runner, canonical)
        from .pipeline_acceptance_delivery import delivery_plan
        template = delivery_plan(runner, candidate, canonical, plan)
        initial_batches = list(plan['batches'])
        for batch in initial_batches:
            retry = copy.deepcopy(batch)
            retry.update(id='adjudicate-' + batch['id'], adjudicates=batch['id'])
            plan['batches'].append(retry)
        targets, checks = _local_plan(profile)
        delivery_target = 'requirement:final_delivery_and_selected_routes'
        targets.append({'id': delivery_target, 'kind': 'requirement', 'requirement_id': delivery_target})
        checks.append({'id': 'delivery:final', 'target_id': delivery_target, 'criterion': 'final_delivery_and_selected_routes', 'method': 'mechanical', 'source_ranges': []})
        delivery_path = _safe(root, base + '/delivery')
        def mechanical():
            from .pipeline_acceptance_delivery import check_delivery
            ordinary = [c for c in inventory['checks'] if c['method'] == 'mechanical' and c['id'] != 'delivery:final']
            values = _mechanical(runner, candidate, canonical, ordinary)
            delivery_check = next(c for c in inventory['checks'] if c['id'] == 'delivery:final')
            try:
                evidence = check_delivery(runner, candidate, canonical, delivery_path)
                values.append(_result(delivery_check, reason='; '.join(evidence['defects']) or 'Selected local delivery artifacts verified against the canonical candidate.', evidence=[evidence], defect=bool(evidence['defects'])))
            except ReadingPackError as exc:
                if 'changed' in str(exc):
                    raise
                values.append(_result(delivery_check, reason=str(exc), evidence=[{'error': str(exc)}], incomplete=True))
            return values
        bindings = {'contract_version': ARTIFACT_CONTRACT_VERSION,
                    'candidate_files': files, 'canonical_sha256': artifact_hash(canonical),
                    'run_manifest_sha256': artifact_hash(runner.manifest),
                    'sources': copy.deepcopy(runner.manifest['sources']),
                    'recipe_sha256': runner.recipe_sha256,
                    'instruction_template': template, 'delivery_manifest': base + '/delivery/manifest.json'}
        inventory = freeze_inventory(plan['targets'] + targets, plan['checks'] + checks, bindings)
        inventory_ref = _save_once(root, base + '/inventory.json', inventory)
        plan_ref = _save_once(root, base + '/plan.json', plan)
        ledger = new_ledger(inventory)
        reports_dir = _safe(root, base + '/reports')
        previous_paths = sorted(reports_dir.glob('*.json')) if reports_dir.exists() else []
        previous = None
        if previous_paths:
            last_path = previous_paths[-1]
            previous = {'path': last_path.relative_to(root).as_posix(), 'sha256': file_hash(last_path.read_bytes())}
            prior = _read_ref(root, previous)
            if prior['inventory'] != inventory_ref or prior['plan'] != plan_ref or prior['acceptance'] != summary(inventory, prior['ledger']):
                raise ReadingPackError('inspection previous report binding changed')
            for attempt in prior['ledger']['attempts']:
                _read_ref(root, attempt['evidence_ref'])
        evidence_dir = _safe(root, base + '/evidence')
        evidence_dir.mkdir(exist_ok=True, mode=0o700)
        evidence_refs = []
        completed_batches = set()
        batches = {b['id']: b for b in plan['batches']}
        for path in sorted(evidence_dir.glob('*.json')):
            ref = {'path': path.relative_to(root).as_posix(), 'sha256': file_hash(path.read_bytes())}
            value = _read_ref(root, ref)
            if value['inventory_sha256'] != inventory['sha256']:
                raise ReadingPackError('inspection result inventory binding changed')
            batch_id = value['batch_id']
            if batch_id in completed_batches:
                raise ReadingPackError('duplicate inspection batch evidence')
            if batch_id == 'mechanical':
                expected = mechanical()
            elif batch_id == 'context-blocked':
                expected = plan['blocked']
            elif batch_id in batches:
                _verify_exchange(runner, batches[batch_id], value['worker_exchange'], value['response'],
                                 'artifact/' + phase + '/' + identifier + '/' + batch_id)
                expected = validate_content_results(batches[batch_id], value['response'])
            else:
                raise ReadingPackError('unknown inspection batch evidence')
            if value['results'] != expected:
                raise ReadingPackError('inspection stored results differ from verified evidence')
            ledger = apply_results(inventory, ledger, expected, ref)
            evidence_refs.append(ref)
            completed_batches.add(batch_id)

        if previous_paths and ledger['attempts'][:len(prior['ledger']['attempts'])] != prior['ledger']['attempts']:
            raise ReadingPackError('inspection evidence history was removed or reordered')

        def report(execution='ready', stop_reason=''):
            nonlocal previous
            for reference in (inventory_ref, plan_ref, *evidence_refs):
                _read_ref(root, reference)
            if _inventory(candidate) != files or runner.manifest['inputs'] != _inventory(root / 'inputs'):
                raise ReadingPackError('inspection source or candidate changed during execution')
            value = {'kind': 'artifact_content_inspection', 'contract_version': ARTIFACT_CONTRACT_VERSION,
                     'inspection_id': identifier, 'previous_report': previous, 'inventory': inventory_ref, 'plan': plan_ref,
                     'ledger': ledger, 'acceptance': summary(inventory, ledger),
                     'execution': {'status': execution, 'reason': stop_reason},
                     'model_diagnostics': {'status': 'not_run'},
                     'author_approval': {'status': 'not_requested'},
                     'production_supported': True, 'candidate': base + '/candidate', 'delivery': base + '/delivery/manifest.json', 'calls_reserved': runner.calls}
            snapshots = _safe(root, base + '/reports')
            snapshots.mkdir(exist_ok=True, mode=0o700)
            reference = _save_once(root, base + f'/reports/{len(list(snapshots.glob("*.json"))):06d}.json', value)
            previous = reference
            return {**value, 'report': reference}

        if prepare_only:
            return report()

        def persist(batch_id, results, response=None):
            nonlocal ledger
            value = {'inventory_sha256': inventory['sha256'], 'batch_id': batch_id,
                     'results': results, 'response': response,
                     'worker_exchange': runner._artifact_last_job if response is not None else None}
            relative = base + '/evidence/' + f'{len(evidence_refs):06d}-' + artifact_hash(batch_id)[:20] + '.json'
            reference = _save_once(root, relative, value)
            ledger = apply_results(inventory, ledger, results, reference)
            evidence_refs.append(reference)
            completed_batches.add(batch_id)
            report('running')

        report('running')
        from contextlib import ExitStack
        guards = ExitStack()
        try:
            if runner.recipe.get('operating_envelope') and not runner.resources.active:
                runner.resources.begin()
            if _runner is None:
                guards.enter_context(runner.resources.deadline_guard())
            if 'mechanical' not in completed_batches:
                persist('mechanical', mechanical())
            if plan['blocked'] and 'context-blocked' not in completed_batches:
                persist('context-blocked', plan['blocked'])
            if len(initial_batches) > runner.recipe.get('artifact_inspection_batch_limit', 128):
                raise PipelineStop('phase_budget_exhausted', 'candidate requires more inspection batches than the frozen maximum')
            runner._artifact_inspection_active = True
            for batch in plan['batches']:
                if batch['id'] in completed_batches:
                    continue
                if batch.get('adjudicates'):
                    used = sum(b.startswith('adjudicate-') for b in completed_batches)
                    unresolved = {r['check_id'] for r in summary(inventory, ledger)['unresolved_suspicions']}
                    if used >= runner.recipe.get('artifact_adjudication_limit', 1) or not unresolved.intersection(c['id'] for c in batch['checks']):
                        continue
                if len(json.dumps(batch['payload'], ensure_ascii=False).encode()) > 850000:
                    raise ReadingPackError('inspection request exceeds the fixed transport input bound')
                response = runner.call(batch.get('stage', 'artifact_content'), {**batch['payload'], 'checks': batch['checks']},
                                       'artifact/' + phase + '/' + identifier + '/' + batch['id'])
                _verify_exchange(runner, batch, runner._artifact_last_job, response,
                                 'artifact/' + phase + '/' + identifier + '/' + batch['id'])
                results = validate_content_results(batch, response)
                persist(batch['id'], results, response)
        except (PipelineStop, ResourceLimit) as exc:
            return report('stopped', exc.state + ': ' + exc.reason)
        except (ReadingPackError, OSError) as exc:
            return report('stopped', str(exc))
        except KeyboardInterrupt:
            report('stopped', 'interrupted; completed evidence retained')
            raise
        finally:
            guards.close()
            runner._artifact_inspection_active = False
            if _runner is None:
                runner.resources.finish()
        return report('completed')
