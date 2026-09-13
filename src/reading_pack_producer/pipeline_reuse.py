"""Explicit, content-bound reuse of preparation exchanges after an engine update.

Preparation import excludes generated records, reader answers and grades.
Explicit restart can replay work before holdout; records pass current admission
and dependency checks again. Every reused exchange must match the complete
current stage contract and input.
"""
from __future__ import annotations

import copy
import shutil
from pathlib import Path

from reading_pack.errors import ReadingPackError
from .work_ledger import artifact_hash

PREPARATION = {'profile', 'structure_select', 'structure_review', 'benchmark', 'benchmark_review'}


def exchange_key(request: dict) -> str:
    return artifact_hash({k: v for k, v in request.items() if k not in {'request_id', 'recipe_sha256'}})


def import_preparation(previous: Path, destination: Path, *, include_work: bool = False, checkpoint_round: int | None = None, working_checkpoint: bool = False) -> dict:
    from .pipeline import _unseal, _seal, _inventory, ROLE
    old, new = (_unseal(p / 'manifest.json') for p in (previous, destination))
    from .pipeline_contracts import manifest_contract
    if manifest_contract(old) != manifest_contract(new):
        raise ReadingPackError('preparation reuse cannot change contract_version')
    for key in ('inputs', 'language', 'title', 'author', 'sources'):
        if old[key] != new[key]:
            raise ReadingPackError('preparation reuse input binding changed: ' + key)
    if checkpoint_round is None and not working_checkpoint:
        if old['seed'] != new['seed']:
            raise ReadingPackError('preparation reuse input binding changed: seed')
    else:
        project, receipt = checked_checkpoint(previous, 'working' if working_checkpoint else checkpoint_round)
        expected = _inventory(project, exclude=('.git', '.reading-pack', 'dist', '__pycache__'))
        if new['seed'] != expected or new.get('restart_origin', {}).get('checkpoint') != receipt:
            raise ReadingPackError('checkpoint seed binding changed')
    for key in ('profile', 'language', 'chunk_characters'):
        if old['recipe'][key] != new['recipe'][key]:
            raise ReadingPackError('preparation reuse contract changed: ' + key)
    if old['inputs'] != _inventory(previous / 'inputs') or (old['seed'] is not None and old['seed'] != _inventory(previous / 'seed')):
        raise ReadingPackError('preparation source snapshot changed')
    if _unseal(previous / 'chunks.json') != _unseal(destination / 'chunks.json'):
        raise ReadingPackError('preparation normalized source changed')
    jobs = [(p, _unseal(p)) for p in sorted((previous / 'jobs').glob('*.json'))]
    if any('/holdout/' in j['request']['job'] for _, j in jobs):
        raise ReadingPackError('cannot reuse a suite after held-out evaluation has begun')
    stages = set(ROLE) if include_work else PREPARATION
    exchanges = {}
    for path, job in jobs:
        request, response = job['request'], job['response']
        if request['stage'] not in stages or response is None:
            continue
        if response.get('request_id') != request['request_id'] or response.get('model') != request['model']:
            raise ReadingPackError('preparation response identity changed')
        exchanges[exchange_key(request)] = {'request': request, 'response': response,
            'origin': str(path.resolve()), 'origin_sha256': artifact_hash(job)}
    receipt = {'origin_run': str(previous.resolve()), 'origin_manifest_sha256': artifact_hash(old),
               'prior_calls_reserved': sum(len(j['attempts']) for _, j in jobs),
               'exchanges': exchanges, 'stages': sorted(stages), 'author_approval': False}
    path = destination / 'reused-preparation.json'
    if path.exists():
        raise ReadingPackError('refusing to overwrite preparation receipt')
    if any((destination / 'jobs').glob('*.json')):
        raise ReadingPackError('preparation must be imported before dispatch')
    _seal(path, receipt)
    return {'exchanges': len(exchanges), 'prior_calls_reserved': receipt['prior_calls_reserved']}


def reused_response(root: Path, request: dict) -> tuple[dict, dict] | None:
    from .pipeline import _unseal
    path = root / 'reused-preparation.json'
    if not path.exists():
        return None
    receipt = _unseal(path)
    if request['stage'] not in receipt.get('stages', PREPARATION):
        return None
    entry = receipt['exchanges'].get(exchange_key(request))
    if entry is None:
        return None
    if artifact_hash(_unseal(Path(entry['origin']))) != entry['origin_sha256']:
        raise ReadingPackError('reused preparation origin changed')
    response = copy.deepcopy(entry['response'])
    response['request_id'] = request['request_id']
    return response, {k: entry[k] for k in ('origin', 'origin_sha256')}


def chapter_structure_hash(project: Path, language: str) -> str:
    from reading_pack.project import load_language_data
    from .candidates import CHAPTER_STRUCTURAL_FIELDS
    return artifact_hash([{k: c.get(k) for k in CHAPTER_STRUCTURAL_FIELDS}
                          for c in load_language_data(project, language)['chapters']])


def checked_checkpoint(previous: Path, number: int | str) -> tuple[Path, dict]:
    from .pipeline import _unseal, _inventory, evaluation_pack
    from reading_pack.validation import validate_project, errors
    from reading_pack.hashing import file_hash
    working = number == 'working'
    if working:
        state = _unseal(previous / 'state.json')
        if state['state'] not in {'blocked_execution', 'failed_quality', 'needs_author_input', 'blocked_source_evidence', 'deadline_exceeded', 'phase_budget_exhausted', 'budget_exhausted'}:
            raise ReadingPackError('working checkpoint requires a stopped run')
        if type(state.get('round')) is not int or state['round'] < 0:
            raise ReadingPackError('working checkpoint round is invalid')
        project = previous / 'working'
        inventory = {'files': _inventory(project)}
        report = None
    else:
        if type(number) is not int or number < 0:
            raise ReadingPackError('checkpoint round must be a nonnegative integer')
        path = previous / 'rounds' / str(number)
        report = _unseal(path / 'report.json')
        inventory = _unseal(path / 'inventory.json')
        project = path / 'project'
        if report['round'] != number or inventory['files'] != _inventory(project):
            raise ReadingPackError('checkpoint project changed')
    try:
        config, data, issues = validate_project(project)
    except (TypeError, KeyError) as exc:
        raise ReadingPackError('checkpoint project failed validation: malformed data') from exc
    if errors(issues):
        raise ReadingPackError('checkpoint project failed validation')
    language = _unseal(previous / 'manifest.json')['language']
    preview_hash = file_hash(evaluation_pack(project, language, config, data[language]).encode())
    if report and preview_hash != report['pack_sha256']:
        raise ReadingPackError('checkpoint preview binding changed')
    artifacts = {}
    for name in ('source-preflight.json', 'source-import-plan.json', 'source-layout.json',
                 'source-text-outline.json', 'structure-reviews.json', 'reviewed-import-plan.json',
                 'seed-reconciled-import-plan.json'):
        if (previous / name).exists():
            artifacts[name] = artifact_hash(_unseal(previous / name))
    preflight = _unseal(previous / 'source-preflight.json')
    if any(artifacts.get(name) != digest for name, digest in preflight['artifacts'].items()):
        raise ReadingPackError('checkpoint source preflight changed')
    return project, {'round': state.get('round') if working else number,
                     'kind': 'unverified-working-draft' if working else 'completed-round',
                     'state_sha256': artifact_hash(state) if working else None,
                     'report_sha256': artifact_hash(report) if report else None,
                     'pack_sha256': preview_hash,
                     'inventory_sha256': artifact_hash(inventory),
                     'chapter_structure_sha256': chapter_structure_hash(project, language),
                     'source_artifacts': artifacts, 'author_approval': False}


def restart_pipeline(previous: Path, destination: Path, recipe: dict, *, checkpoint_round: int | None = None, working_checkpoint: bool = False, permit_draft_records: list[str] | None = None, authorization_file: Path | None = None) -> dict:
    """Rebuild before holdout using exact saved exchanges and current admission rules.

    The origin timestamp preserves candidate identities for exact independent
    review reuse. Restart time is recorded separately; no old run is modified.
    """
    from .pipeline import _unseal, _seal, start_pipeline, _lock, _inventory
    permit_draft_records = permit_draft_records or []
    if bool(permit_draft_records) != bool(authorization_file):
        raise ReadingPackError('draft records and explicit authorization file are required together')
    authorization = None
    if permit_draft_records:
        if checkpoint_round is None and not working_checkpoint:
            raise ReadingPackError('draft permissions require an explicit checkpoint')
        authorization = authorization_file.read_bytes()
        if not authorization or len(authorization) > 65536:
            raise ReadingPackError('authorization file must contain 1 to 65536 bytes')
    if checkpoint_round is not None and working_checkpoint:
        raise ReadingPackError('choose either a completed round or the working draft')
    with _lock(previous):
        old = _unseal(previous / 'manifest.json')
        from .pipeline_contracts import restart_contract_recipe
        recipe = restart_contract_recipe(old, recipe)
        resource_ledger = previous / 'resource-ledger.json'
        if resource_ledger.exists():
            if (previous / 'resource-successor.json').exists():
                raise ReadingPackError('operating envelope already transferred to a successor; branching would duplicate budget')
            if old['recipe'].get('operating_envelope') != recipe.get('operating_envelope'):
                raise ReadingPackError('restart cannot reset or enlarge the operating envelope')
        if old['inputs'] != _inventory(previous / 'inputs') or (old['seed'] is not None and old['seed'] != _inventory(previous / 'seed')):
            raise ReadingPackError('restart source snapshot changed')
        jobs = [_unseal(p) for p in (previous / 'jobs').glob('*.json')]
        if any('/holdout/' in j['request']['job'] for j in jobs):
            raise ReadingPackError('cannot restart production after held-out evaluation has begun')
        project = previous / 'seed' if old['seed'] is not None else None
        checkpoint = None
        if checkpoint_round is not None or working_checkpoint:
            project, checkpoint = checked_checkpoint(previous, 'working' if working_checkpoint else checkpoint_round)
        sources = old['sources']
        start_pipeline(destination, previous / sources[0]['path'], recipe,
            supplements=[(previous / s['path'], s['role']) for s in sources[1:]],
            project=project,
            title=old['title'], author=old['author'], source_format=sources[0]['format'])
        manifest = _unseal(destination / 'manifest.json')
        if 'contract_version' in old:
            manifest['contract_version'] = old['contract_version']
        else:
            manifest.pop('contract_version', None)
        if 'benchmark_selection_contract' in old:
            manifest['benchmark_selection_contract']=copy.deepcopy(old['benchmark_selection_contract'])
        else:
            manifest.pop('benchmark_selection_contract',None)
        # A restart must not retroactively judge saved answers under a new
        # purpose contract. Changing evaluation scope requires a separate study.
        if 'reader_utility_contract' in old:
            manifest['reader_utility_contract'] = copy.deepcopy(old['reader_utility_contract'])
        else:
            manifest.pop('reader_utility_contract', None)
        manifest['restarted_at'] = manifest['created_at']
        manifest['created_at'] = old['created_at']
        manifest['restart_origin'] = {'run': str(previous.resolve()), 'manifest_sha256': artifact_hash(old)}
        ledger = previous / 'resource-ledger.json'
        if ledger.exists():
            if old['recipe'].get('operating_envelope') != recipe.get('operating_envelope'):
                raise ReadingPackError('restart cannot reset or enlarge the operating envelope')
            manifest['restart_origin']['resources'] = {'path':str(ledger.resolve()), 'sha256':artifact_hash(_unseal(ledger))}
        if checkpoint:
            from .pipeline_resolution import consolidate_findings
            known = copy.deepcopy(old.get('restart_origin', {}).get('known_findings', []))
            reports = []
            for path in sorted((previous / 'rounds').glob('*/report.json')):
                report = _unseal(path)
                if report['round'] > checkpoint['round']:
                    continue
                known.extend(f for f in report['failures'] if f.get('classification') in {'blocking','unresolved'})
                reports.append({'path':str(path.resolve()),'report_sha256':artifact_hash(report)})
            if known:
                manifest['restart_origin']['known_findings'] = consolidate_findings(known)
                manifest['restart_origin']['known_findings_reports'] = reports
        if checkpoint:
            manifest['restart_origin']['checkpoint'] = checkpoint
            for name in checkpoint['source_artifacts']:
                shutil.copyfile(previous / name, destination / name)
        _seal(destination / 'manifest.json', manifest)
        result = import_preparation(previous, destination, include_work=True, checkpoint_round=checkpoint_round, working_checkpoint=working_checkpoint)
        if permit_draft_records:
            from reading_pack.hashing import file_hash
            from .pipeline_drafts import authorize_draft_revisions
            digest = file_hash(authorization)
            count = authorize_draft_revisions(destination / 'seed', old['language'], permit_draft_records,
                                             origin='explicit-authorization:' + digest)
            (destination / 'draft-revision-authorization.txt').write_bytes(authorization)
            manifest['seed'] = _inventory(destination / 'seed')
            manifest['restart_origin']['draft_permission'] = {
                'record_ids': sorted(permit_draft_records), 'authorization_sha256': digest, 'author_approval': False}
            _seal(destination / 'manifest.json', manifest)
            result['draft_permissions'] = count
        if resource_ledger.exists():
            _seal(previous / 'resource-successor.json', {'successor':str(destination.resolve()),
                'ledger_sha256':artifact_hash(_unseal(resource_ledger))})
        return result
