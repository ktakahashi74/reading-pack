"""Separate reassessment with immutable origin evidence and cumulative resources."""
from __future__ import annotations

import copy
import shutil
from pathlib import Path

from reading_pack.errors import ReadingPackError
from .pipeline_acceptance_records import reference, write_record
from .work_ledger import artifact_hash


def reassess_artifact(previous: Path, destination: Path, project: Path, recipe: dict):
    from .pipeline import _lock, _unseal, _seal, _inventory, start_pipeline, Runner, pipeline_status
    previous, destination, project = previous.resolve(), destination.resolve(), project.resolve()
    if destination == previous or destination.is_relative_to(previous) or previous.is_relative_to(destination):
        raise ReadingPackError('reassessment requires a separate non-overlapping run')
    if destination.exists(): raise ReadingPackError('refusing to overwrite reassessment run')
    if not project.is_relative_to(previous):
        raise ReadingPackError('reassessment candidate must be a saved project inside the origin run')
    if recipe.get('contract_version') != 'artifact-acceptance-1':
        raise ReadingPackError('reassessment requires artifact-acceptance-1')
    with _lock(previous):
        old = _unseal(previous / 'manifest.json')
        if (previous / 'resource-successor.json').exists():
            raise ReadingPackError('origin already has a resource successor; budget cannot branch')
        if old['inputs'] != _inventory(previous / 'inputs'):
            raise ReadingPackError('old source evidence changed')
        if recipe.get('operating_envelope') != old['recipe'].get('operating_envelope'):
            raise ReadingPackError('reassessment must preserve cumulative envelope and original deadline')
        if recipe['max_calls'] > old['recipe']['max_calls']:
            raise ReadingPackError('reassessment cannot increase cumulative call limit')
        old_calls = sum(len(_unseal(p)['attempts']) for p in (previous / 'jobs').glob('*.json')) + old.get('prior_calls',0)
        ledger = previous / 'resource-ledger.json'
        if old_calls and old['recipe'].get('operating_envelope') and not ledger.exists():
            raise ReadingPackError('prior monetary cost is unknown; cannot initialize a new zero-cost ledger')
        # Preserve complete old evidence before recording the successor marker.
        baseline = _inventory(previous, exclude=('.pipeline.lock', '__pycache__'))
        sources = old['sources']
        start_pipeline(destination, previous / sources[0]['path'], copy.deepcopy(recipe),
                       supplements=[(previous/s['path'],s['role']) for s in sources[1:]],
                       project=project, title=old['title'], author=old['author'], source_format=sources[0]['format'])
        origin = destination / 'acceptance' / 'origin'
        for name in baseline:
            target = origin / name
            target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            shutil.copyfile(previous/name,target)
            target.chmod(0o600)
        if _inventory(origin) != baseline or _inventory(previous,exclude=('.pipeline.lock','__pycache__')) != baseline:
            raise ReadingPackError('origin evidence changed during reassessment snapshot')
        manifest = _unseal(destination/'manifest.json')
        manifest.update(reassessment_of=reference(destination,origin/'manifest.json'),
                        prior_calls=old_calls, budget_scope_id=old.get('budget_scope_id',artifact_hash(old['sources'])),
                        origin_evidence={'root':'acceptance/origin','files':baseline})
        if ledger.exists():
            saved_ledger = _unseal(origin/'resource-ledger.json')
            manifest['restart_origin'] = {'resources': {'path':str(origin/'resource-ledger.json'), 'sha256':artifact_hash(saved_ledger)}}
        _seal(destination/'manifest.json',manifest)
        # Only source-identity facts may cross contracts. Never import worker
        # judgments, benchmark passes or author approval into the new verdict.
        for name in ('source-text-outline.json','source-layout.json'):
            if (origin/name).exists():
                value = _unseal(origin/name)
                if value.get('source_sha256') != sources[0]['sha256']:
                    raise ReadingPackError('saved outline does not bind the old source')
                shutil.copyfile(origin/name,destination/name)
        _seal(previous/'resource-successor.json',{'run':str(destination),'manifest_sha256':artifact_hash(manifest),
            'reason':'separate artifact reassessment; old decisions and evidence retained'})
        runner = Runner(destination)
        ref = write_record(runner)
        runner.save('ready',acceptance_record=ref,acceptance={'status':'not_run'},
                    reason='separate reassessment; old approvals do not transfer')
        return pipeline_status(destination)
