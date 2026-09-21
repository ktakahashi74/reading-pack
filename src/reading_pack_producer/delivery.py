"""Bounded generation and quantitative reports; adoption belongs to the user."""
from __future__ import annotations

import copy
import hashlib
import json
import shutil
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from jsonschema import Draft202012Validator
from reading_pack.errors import ReadingPackError
from reading_pack.project import create_project, empty_language_data, load_config, write_json
from reading_pack.rendering import render_pack
from reading_pack.schema_validation import require_structure
from .candidates import _source_text_snapshot, run_local_adapter
from .delivery_contract import (VERSION, RUBRIC, GENERATION_PROMPT, EVALUATION_PROMPT,
                                GLOBAL_PROMPT, GLOBAL_SCHEMA, obj, generation_schema, evaluation_schema)
from .delivery_outline import outline
from .pipeline import _seal, _unseal, _lock, _engine_hash, _copy_seed, _inventory
from .delivery_seed import (POLICIES, align_units, author_input_modes, completeness, draft_config_text,
                            load_seed, merge_seed_data, parse_chapter_map)
from .delivery_fresh import (full_schema, full_evaluation_schema, module_records, chapter_records,
                             full_completeness, PROMPT as FRESH_PROMPT)
from .pipeline_resources import adapter_receipt
from .work_ledger import artifact_hash
from .delivery_mechanical import freeze_inputs, enrich
from .delivery_costs import history as cost_history, totals as cost_totals, own_costs


def _validate_recipe(value):
    try:
        json.dumps(value, allow_nan=False)
    except (ValueError, TypeError) as exc:
        raise ReadingPackError('delivery recipe requires finite JSON values') from exc
    require_structure("delivery-recipe.schema.json", value, label='delivery recipe')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def recipe(generator, evaluator, *, generator_model, evaluator_model, max_cost_usd,
           call_allowance_usd, max_wall_seconds, timeout_seconds=300, prior_cost_usd=0,
           cumulative_cost_limit_usd=None, language='ja', scope='Supplied manuscript only',
           global_call_allowance_usd=None, evaluator_timeout_seconds=None, global_timeout_seconds=None,
           unknown_call_reserve_usd=None, repair_rounds=0, repair_call_allowance_usd=None):
    value = {'contract_version': VERSION, 'workers': {
        'generator': {'command': generator, 'model': generator_model},
        'evaluator': {'command': evaluator, 'model': evaluator_model}},
        'language': language, 'scope': scope, 'max_cost_usd': max_cost_usd, 'repair_rounds': repair_rounds,
        'call_allowance_usd': call_allowance_usd, 'max_wall_seconds': max_wall_seconds,
        'timeout_seconds': timeout_seconds, 'local_reserve_seconds': 120,
        'prior_cost_usd': prior_cost_usd,
        'cumulative_cost_limit_usd': cumulative_cost_limit_usd if cumulative_cost_limit_usd is not None else prior_cost_usd + max_cost_usd,
        'max_chapter_characters': 50000, 'max_pack_characters': 100000,
        'max_chapters': 128, 'max_sections_per_chapter': 64}
    for key, item in (('global_call_allowance_usd', global_call_allowance_usd),
                      ('evaluator_timeout_seconds', evaluator_timeout_seconds),
                      ('global_timeout_seconds', global_timeout_seconds),
                      ('unknown_call_reserve_usd', unknown_call_reserve_usd),
                      ('repair_call_allowance_usd', repair_call_allowance_usd)):
        if item is not None:
            value[key] = item
    _validate_recipe(value)
    return value


def _limits(value, key, role):
    """Per-call allowance and controller timeout; the whole-Pack call may differ."""
    if key in ('evaluate/global', 'reevaluate/global'):
        return (value.get('global_call_allowance_usd', value['call_allowance_usd']),
                value.get('global_timeout_seconds', value['timeout_seconds']))
    if key.startswith('repair/'):
        return value.get('repair_call_allowance_usd', value['call_allowance_usd']), value['timeout_seconds']
    if role == 'evaluator':
        return value['call_allowance_usd'], value.get('evaluator_timeout_seconds', value['timeout_seconds'])
    return value['call_allowance_usd'], value['timeout_seconds']


def _reservation(value, work):
    """Fixed calls, USD and wall time for a list of (job key, role); nothing is estimated."""
    reservation, seconds = Decimal(0), 0
    for key, role in work:
        allowance, timeout = _limits(value, key, role)
        reservation += Decimal(str(allowance))
        seconds += timeout
    return len(work), reservation, seconds + value['local_reserve_seconds']


def _work(units, repair_rounds=0):
    work = ([('generate/' + u['id'], 'generator') for u in units]
            + [('evaluate/' + u['id'], 'evaluator') for u in units] + [('evaluate/global', 'evaluator')])
    if repair_rounds:
        work += [('repair/' + u['id'], 'generator') for u in units]
        work += [('reevaluate/' + u['id'], 'evaluator') for u in units] + [('reevaluate/global', 'evaluator')]
    return work


def _bindings(workers):
    result = {}
    for worker in workers.values():
        command = worker['command']
        executable = shutil.which(command[0])
        if executable is None:
            raise ReadingPackError('configured adapter executable not found')
        for name in [executable] + command[1:]:
            path = Path(name)
            try:
                is_file = path.is_file()
            except OSError:
                is_file = False  # An ordinary command argument need not be a path.
            if is_file:
                result[str(path.resolve())] = digest(path)
    return result


def prepare(root, source, value, *, title=None, author='', source_format=None, chapter_level=None,
            seed=None, chapter_map=None, seed_policy='preserve', inherit_seed=False, output=None, mechanical_inputs=None):
    root, source = Path(root).absolute(), Path(source).resolve()
    output = _output_path(root, output)
    if seed is not None and not inherit_seed:
        raise ReadingPackError('--seed requires explicit --inherit-seed; omit both to regenerate all modules')
    if inherit_seed and seed is None:
        raise ReadingPackError('--inherit-seed requires --seed')
    if seed_policy not in POLICIES:
        raise ReadingPackError('seed policy must be one of ' + ', '.join(POLICIES))
    if seed is None and (chapter_map is not None or seed_policy != 'preserve'):
        raise ReadingPackError('chapter map and seed policy apply only with --seed')
    seed_config = seed_data = None
    if seed is not None:
        seed = Path(seed).resolve()
        if not seed.is_dir():
            raise ReadingPackError('seed must be an existing Reading Pack project directory')
        seed_config, seed_data = load_seed(seed, value['language'])
        title = title or seed_config['book']['title']
        author = author or seed_config['book'].get('author', '')
    if root.exists():
        raise ReadingPackError('refusing to overwrite delivery run; resume its frozen plan instead')
    _validate_recipe(value)
    if seed is not None and value.get('repair_rounds', 0):
        raise ReadingPackError('repair round requires fresh generation, not inherited seed content')
    fmt = source_format or {'.md': 'markdown', '.org': 'org', '.txt': 'text'}.get(source.suffix.lower())
    if fmt not in {'markdown', 'org', 'text'}:
        raise ReadingPackError('delivery requires a Markdown, Org or text manuscript; extract other formats first')
    if chapter_level is not None and (type(chapter_level) is not int or not 1 <= chapter_level <= 8):
        raise ReadingPackError('chapter level must be between 1 and 8')
    title = title or source.stem
    for name, text_value in [('title', title), ('author', author)]:
        if len(text_value) > 500 or any(ord(c) < 32 for c in text_value):
            raise ReadingPackError(name + ' must be a short single line')
    raw, text = _source_text_snapshot(source, source_format=fmt)
    if not text.strip():
        raise ReadingPackError('empty manuscript')
    units = outline(text, fmt, title, chapter_level)
    if seed_data is not None:
        units = align_units(units, seed_data['chapters'], parse_chapter_map(chapter_map))
    if len(units) > value['max_chapters'] or any(len(u['sections']) > value['max_sections_per_chapter'] for u in units):
        raise ReadingPackError('outline exceeds frozen chapter/section limits')
    if any(u['end'] - u['start'] > value['max_chapter_characters'] for u in units):
        raise ReadingPackError('chapter exceeds context limit; explicitly split the source before preparing')
    mechanical = freeze_inputs(mechanical_inputs, text, units)
    if seed is not None and mechanical is not None:
        raise ReadingPackError('mechanical inputs apply to fresh deliveries, not inherited seed content')
    calls, reservation, seconds = _reservation(value, _work(units, value.get('repair_rounds', 0)))
    _admit(value, calls, reservation, seconds)
    bindings = _bindings(value['workers'])
    root.mkdir(parents=True, mode=0o700)
    (root / 'source.bin').write_bytes(raw)
    (root / 'source.txt').write_text(text, encoding='utf-8')
    seed_plan = None
    if seed is not None:
        _copy_seed(seed, root / 'seed')
        seed_plan = {'path': str(seed), 'inventory': _inventory(root / 'seed'), 'language': value['language'],
                     'policy': seed_policy, 'chapter_map': parse_chapter_map(chapter_map) if chapter_map else {},
                     'config': {k: seed_config.get(k) for k in ('version', 'status', 'level', 'workflow')},
                     'source': seed_data['source'], 'author_input_modes': author_input_modes(seed, value['language'])}
    plan = {'contract_version': VERSION, 'id': uuid.uuid4().hex, 'engine_sha256': _engine_hash(),
            'recipe': copy.deepcopy(value), 'source_name': source.name, 'source_format': fmt,
            'source_sha256': digest(root / 'source.bin'), 'normalized_sha256': digest(root / 'source.txt'),
            'title': title, 'author': author, 'chapter_level': chapter_level, 'units': units, 'seed': seed_plan,
            'content_mode': 'inherit' if seed is not None else 'fresh',
            'mechanical_inputs': mechanical, 'cost_history': [], 'cost_base_prior_usd': str(value['prior_cost_usd']),
            'output': str(output),
            'rubric': copy.deepcopy(RUBRIC), 'adapter_files': bindings,
            'maximum_calls': calls, 'reserved_usd': str(reservation),
            'repair_calls': len(units) if value.get('repair_rounds', 0) else 0, 'automatic_retries': 0, 'quality_gate': False}
    _seal(root / 'delivery-plan.json', plan)
    _seal(root / 'delivery-state.json', {'state': 'prepared', 'started_at': None, 'jobs': {}, 'outputs': {}})
    return status(root)


def _admit(value, calls, reservation, seconds):
    if reservation > Decimal(str(value['max_cost_usd'])):
        raise ReadingPackError(f'full generation/evaluation reservation needs {calls} calls / USD {reservation}')
    if Decimal(str(value['prior_cost_usd'])) + reservation > Decimal(str(value['cumulative_cost_limit_usd'])):
        raise ReadingPackError('full reservation exceeds cumulative cost limit')
    if seconds > value['max_wall_seconds']:
        raise ReadingPackError('full generation/evaluation timeout reservation exceeds wall limit')


def prepare_successor(root, predecessor, value, *, output=None):
    """Redo only the jobs a finished delivery left incomplete; carry its completed exchanges unchanged."""
    root, predecessor = Path(root).absolute(), Path(predecessor).absolute()
    output = _output_path(root, output)
    if root.exists():
        raise ReadingPackError('refusing to overwrite delivery run; resume its frozen plan instead')
    _validate_recipe(value)
    old_plan, old_state = _load(predecessor)
    if old_state['state'] not in {'delivered', 'delivered_partial', 'generation_failed'}:
        raise ReadingPackError('a successor requires a finished predecessor delivery')
    uncertain = [key for key, job in old_state['jobs'].items()
                 if job.get('status') != 'completed' and (job.get('status') == 'outcome_unknown'
                 or (job.get('started') and job.get('receipt')
                     and job['receipt'].get('usage_origin') == 'claude-cli'
                     and not job['receipt'].get('receipt_valid')
                     and job['receipt'].get('actual_cost_usd') is None))]
    if uncertain:
        raise ReadingPackError('predecessor has unknown call outcomes/costs; resolve or recover saved outputs '
                               'before preparing a successor: ' + ', '.join(uncertain))
    old = old_plan['recipe']
    if value['language'] != old['language'] or value['scope'] != old['scope']:
        raise ReadingPackError('successor must keep the predecessor language and scope')
    for role in ('generator', 'evaluator'):
        if value['workers'][role]['model'] != old['workers'][role]['model']:
            raise ReadingPackError('successor must keep the predecessor models; compare models in a new delivery instead')
    if value.get('repair_rounds', 0) != old.get('repair_rounds', 0):
        raise ReadingPackError('successor must keep repair round policy; use a new delivery for changed production scope')
    if old.get('repair_rounds', 0) and old_state['state'] == 'delivered':
        raise ReadingPackError('predecessor delivery has nothing to redo')
    units = copy.deepcopy(old_plan['units'])
    completed = {k for k, j in old_state['jobs'].items() if j.get('status') == 'completed'}
    # A partial repair snapshot freezes available chapters and the initial judge.
    deferred_generation = ([u['id'] for u in units if 'generate/'+u['id'] not in completed]
        if value.get('repair_available_chapters') and old_state.get('repair_snapshot') else [])
    redo = [(k, role) for k, role in _work(units, value.get('repair_rounds', 0))
            if k not in completed and k.split('/', 1)[1] not in deferred_generation]
    if any(k.startswith('generate/') for k, _ in redo) and ('evaluate/global', 'evaluator') not in redo:
        redo.append(('evaluate/global', 'evaluator'))  # a regenerated chapter changes the whole Pack
    if not redo:
        raise ReadingPackError('predecessor delivery has nothing to redo')
    if value.get('repair_rounds', 0) and any(k.startswith(('generate/', 'repair/')) for k, _ in redo):
        if ('reevaluate/global', 'evaluator') not in redo:
            redo.append(('reevaluate/global', 'evaluator'))
    carried = sorted(completed - {k for k, _ in redo})
    rows, base = cost_history(predecessor, old_plan, old_state)
    costs = cost_totals(rows, base)
    value = copy.deepcopy(value)
    # Keep any extra caller reservation while preventing omitted predecessor costs.
    previous_minimum = Decimal(cost_totals(rows[:-1], base)['base_plus_chain_and_reserve_usd'])
    prior_extra = max(Decimal(0), Decimal(str(old['prior_cost_usd'])) - previous_minimum)
    value['prior_cost_usd'] = float(max(Decimal(str(value['prior_cost_usd'])),
        Decimal(costs['base_plus_chain_and_reserve_usd']) + prior_extra))
    calls, reservation, seconds = _reservation(value, redo)
    _admit(value, calls, reservation, seconds)
    bindings = _bindings(value['workers'])
    root.mkdir(parents=True, mode=0o700)
    for name in ('source.bin', 'source.txt'):
        shutil.copyfile(predecessor / name, root / name)
    seed_plan = None
    if old_plan.get('seed') is not None:
        shutil.copytree(predecessor / 'seed', root / 'seed')
        if _inventory(root / 'seed') != old_plan['seed']['inventory']:
            raise ReadingPackError('predecessor seed snapshot changed')
        seed_plan = copy.deepcopy(old_plan['seed'])
    (root / 'jobs').mkdir(mode=0o700)
    jobs = {}
    for key in carried:
        job = copy.deepcopy(old_state['jobs'][key])
        name = job['request_id'] + '.json'
        shutil.copyfile(predecessor / 'jobs' / name, root / 'jobs' / name)
        if digest(root / 'jobs' / name) != job.get('exchange_sha256'):
            raise ReadingPackError('predecessor exchange changed')
        job['carried'] = old_plan['id']
        jobs[key] = job
    plan = {'contract_version': VERSION, 'id': uuid.uuid4().hex, 'engine_sha256': _engine_hash(),
            'recipe': copy.deepcopy(value), 'source_name': old_plan['source_name'], 'source_format': old_plan['source_format'],
            'source_sha256': old_plan['source_sha256'], 'normalized_sha256': old_plan['normalized_sha256'],
            'title': old_plan['title'], 'author': old_plan['author'], 'chapter_level': old_plan['chapter_level'],
            'content_mode': old_plan.get('content_mode', 'legacy'),
            'mechanical_inputs': copy.deepcopy(old_plan.get('mechanical_inputs')),
            'cost_history': rows, 'cost_base_prior_usd': base,
            'units': units, 'seed': seed_plan, 'rubric': copy.deepcopy(old_plan['rubric']), 'adapter_files': bindings,
            'predecessor': {'run': str(predecessor), 'id': old_plan['id'], 'state': old_state['state'],
                            'plan_sha256': digest(predecessor / 'delivery-plan.json'),
                            'state_sha256': digest(predecessor / 'delivery-state.json'),
                            'engine_sha256': old_plan['engine_sha256']},
            'carried': carried, 'redo': [k for k, _ in redo], 'output': str(output),
            'deferred_generation': deferred_generation,
            'maximum_calls': calls, 'reserved_usd': str(reservation),
            'repair_calls': len(units) if value.get('repair_rounds', 0) else 0, 'automatic_retries': 0, 'quality_gate': False}
    if digest(root / 'source.bin') != plan['source_sha256'] or digest(root / 'source.txt') != plan['normalized_sha256']:
        raise ReadingPackError('predecessor source changed')
    repair_snapshot = None
    if value.get('repair_rounds', 0) and old_state.get('repair_snapshot') and not any(k.startswith('generate/') for k, _ in redo):
        repair_snapshot = copy.deepcopy(old_state['repair_snapshot'])
        for name, expected in repair_snapshot.items():
            shutil.copyfile(predecessor/name, root/name)
            if digest(root/name) != expected:
                raise ReadingPackError('first-pass repair snapshot changed')
    if not any(k.startswith('generate/') for k, _ in redo):
        # Evaluations must inspect the exact predecessor artifact, even on another date/engine.
        lang = value['language']; name = f'reading-pack.{lang}.md'
        shutil.copytree(predecessor / 'project', root / 'project')
        shutil.copyfile(predecessor / name, root / name)
        if repair_snapshot:
            shutil.copyfile(root/f'first-pass-reading-pack.{lang}.md', root/name)
            shutil.copyfile(root/f'first-pass-reading-pack.{lang}.md', root/'project'/'dist'/name)
            shutil.copyfile(root/f'first-pass-pack.{lang}.json', root/'project'/'data'/f'pack.{lang}.json')
        plan['frozen_artifact'] = {'pack_sha256': digest(root / name),
            'project_inventory': _inventory(root / 'project'),
            'seed_log': json.loads((predecessor / 'quality-report.json').read_text()).get('completeness', {}).get('chapters')}
    _seal(root / 'delivery-plan.json', plan)
    _seal(root / 'delivery-state.json', {'state': 'prepared', 'started_at': None, 'jobs': jobs, 'outputs': {}, 'repair_snapshot': repair_snapshot})
    return status(root)


def _output_path(root, output):
    """Deliverables never stay buried inside the private run; default to a sibling folder."""
    output = Path(output).absolute() if output else root.parent / (root.name + '-delivery')
    if output == root or root in output.parents or output in root.parents:
        raise ReadingPackError('delivery output must be a separate directory outside the run')
    return output


EXPORTED = ('reading-pack.{lang}.md', 'quality-report.{lang}.md', 'quality-report.json', 'project/data/pack.{lang}.json',
            'repair-report.json', 'first-pass-reading-pack.{lang}.md', 'first-pass-pack.{lang}.json', 'first-pass-quality-report.json')
WITHHELD = ('source.bin', 'source.txt', 'jobs/', 'seed/', 'project/ (except data/pack.<lang>.json)', 'delivery-plan.json', 'delivery-state.json')


def export(root, output=None):
    """Copy only the deliverables plus a hash manifest; manuscript, exchanges and seed stay private."""
    root = Path(root).absolute()
    plan, state = _load(root)
    if state['state'] not in {'delivered', 'delivered_partial', 'generation_failed'}:
        raise ReadingPackError('export requires a finished delivery')
    output = _output_path(root, output or plan.get('output'))
    lang = plan['recipe']['language']
    files = {}
    for pattern in EXPORTED:
        name = pattern.format(lang=lang)
        if (root / name).is_file():
            files[Path(name).name] = root / name
    hashes = {name: digest(path) for name, path in files.items()}
    report = json.loads((root / 'quality-report.json').read_text(encoding='utf-8'))
    evaluation = report['evaluation']
    manifest = {'contract_version': VERSION, 'delivery_id': plan['id'], 'run': str(root), 'state': state['state'],
        'language': lang, 'title': plan['title'], 'scope': plan['recipe']['scope'],
        'models': {role: w['model'] for role, w in plan['recipe']['workers'].items()},
        'seed': None if plan.get('seed') is None else {k: plan['seed'][k] for k in ('path', 'policy', 'config')},
        'predecessor': plan.get('predecessor'),
        'files': {name: {'sha256': hashes[name], 'bytes': files[name].stat().st_size} for name in sorted(files)},
        'withheld': list(WITHHELD),
        'generation': report['generation'],
        'evaluation': {k: evaluation[k] for k in ('completed_chapters', 'total_chapters', 'records_evaluated',
                                                    'records_expected', 'record_counts', 'coverage_counts')},
        'global_evaluation': None if evaluation['global'] is None else
            {k: v['score'] for k, v in evaluation['global']['dimensions'].items()},
        'completeness': None if report.get('completeness') is None else
            {'lost_modules': report['completeness']['lost_modules'],
             'author_input_conflicts': len(report['completeness'].get('author_input_conflicts', []))},
        'resources': {k: report['resources'][k] for k in ('model_calls_started', 'known_reported_usd',
                                                          'unknown_cost_calls', 'known_cumulative_usd')},
        'quality_gate': False, 'user_adoption': 'not_decided', 'publication': False}
    if output.exists():
        existing = output / 'delivery-manifest.json'
        if existing.is_file():
            old = json.loads(existing.read_text(encoding='utf-8'))
            if (old.get('files') == manifest['files'] and old.get('delivery_id') == plan['id']
                    and all((output / n).is_file() and digest(output / n) == h for n, h in hashes.items())):
                return {'output': str(output), 'files': sorted(files), 'unchanged': True}
        if any(output.iterdir()):
            raise ReadingPackError('refusing to export into a non-empty directory with different content')
    output.mkdir(parents=True, exist_ok=True)
    for name, path in files.items():
        shutil.copyfile(path, output / name)
        if digest(output / name) != hashes[name]:
            raise ReadingPackError('exported deliverable changed during copy')
    manifest['exported_at'] = datetime.now(timezone.utc).isoformat()
    write_json(output / 'delivery-manifest.json', manifest)
    return {'output': str(output), 'files': sorted(files), 'unchanged': False}


def _carried(root, state, key):
    """Replay a completed predecessor exchange; never a new judgment."""
    job = state['jobs'][key]
    path = root / 'jobs' / (job['request_id'] + '.json')
    if digest(path) != job.get('exchange_sha256'):
        raise ReadingPackError('carried delivery exchange changed')
    saved = _unseal(path)
    response = saved.get('response')
    if response is None or not Draft202012Validator(saved['request']['response_schema']).is_valid(response):
        raise ReadingPackError('carried job is not a completed exchange')
    return response['result']


def _load(root):
    plan, state = _unseal(root / 'delivery-plan.json'), _unseal(root / 'delivery-state.json')
    if plan['contract_version'] != VERSION:
        raise ReadingPackError('not a generation-report delivery')
    if digest(root / 'source.bin') != plan['source_sha256'] or digest(root / 'source.txt') != plan['normalized_sha256']:
        raise ReadingPackError('frozen delivery source changed')
    if plan.get('seed') is not None and _inventory(root / 'seed') != plan['seed']['inventory']:
        raise ReadingPackError('delivery seed snapshot changed')
    for name, expected in (state.get('repair_snapshot') or {}).items():
        if name not in {'first-pass-reading-pack.ja.md', 'first-pass-reading-pack.en.md', 'first-pass-pack.ja.json', 'first-pass-pack.en.json', 'first-pass-quality-report.json'} or digest(root/name) != expected:
            raise ReadingPackError('first-pass repair snapshot changed')
    if plan.get('frozen_artifact') and not state.get('repair_snapshot'):
        artifact = plan['frozen_artifact']
        if (digest(root / f"reading-pack.{plan['recipe']['language']}.md") != artifact['pack_sha256']
                or _inventory(root / 'project') != artifact['project_inventory']):
            raise ReadingPackError('frozen evaluation artifact changed')
    for name, expected in state['outputs'].items():
        if name not in {'reading-pack.ja.md', 'reading-pack.en.md', 'quality-report.json', 'quality-report.ja.md', 'quality-report.en.md', 'project/data/pack.ja.json', 'project/data/pack.en.json', 'repair-report.json', 'repaired-generation.json'}:
            raise ReadingPackError('unexpected delivery output binding')
        if digest(root / name) != expected:
            raise ReadingPackError('delivery output changed: ' + name)
    for job in state['jobs'].values():
        if job.get('exchange_sha256'):
            if digest(root / 'jobs' / (job['request_id'] + '.json')) != job['exchange_sha256']:
                raise ReadingPackError('delivery exchange changed')
    return plan, state


def status(root):
    root = Path(root)
    plan, state = _load(root)
    return {'contract_version': VERSION, 'state': state['state'], 'quality_gate': False,
            'scope': plan['recipe']['scope'], 'content_mode': plan.get('content_mode', 'legacy'),
            'source_name': plan['source_name'],
            'source_sha256': plan['source_sha256'], 'outline': plan['units'],
            'models': {role: worker['model'] for role, worker in plan['recipe']['workers'].items()},
            'maximum_wall_seconds': plan['recipe']['max_wall_seconds'],
            'prior_cost_usd': plan['recipe']['prior_cost_usd'],
            'cumulative_cost_limit_usd': plan['recipe']['cumulative_cost_limit_usd'],
            'maximum_calls': plan['maximum_calls'], 'repair_rounds': plan['recipe'].get('repair_rounds', 0),
            'calls_started': sum(bool(j.get('started')) and not j.get('carried') for j in state['jobs'].values()),
            'reserved_usd': plan['reserved_usd'], 'chapter_count': len(plan['units']),
            'section_count': sum(len(u['sections']) for u in plan['units']), 'outputs': state['outputs'],
            'seed': None if plan.get('seed') is None else {k: plan['seed'][k] for k in ('path', 'policy', 'language')},
            'predecessor': plan.get('predecessor'), 'carried_jobs': sorted(k for k, j in state['jobs'].items() if j.get('carried')),
            'output': plan.get('output'),
            'user_adoption': 'not_decided', 'publication': False}


def _request(plan, key, role, stage, payload, schema, prompt):
    allowance, _ = _limits(plan['recipe'], key, role)
    base = {'schema_version': 1, 'stage': stage, 'model': plan['recipe']['workers'][role]['model'],
            'job': key, 'delivery_id': plan['id'], 'prompt': prompt, 'payload': payload,
            'execution_budget': {'call_allowance_usd': allowance}}
    rid = artifact_hash({**base, 'result_schema': schema})
    return {**base, 'request_id': rid, 'response_schema': obj({'schema_version': {'const': 1},
            'request_id': {'const': rid}, 'model': {'const': base['model']}, 'result': schema})}


def _call(root, plan, state, key, role, stage, payload, schema, prompt):
    request = _request(plan, key, role, stage, payload, schema, prompt)
    path = root / 'jobs' / (request['request_id'] + '.json')
    existing = state['jobs'].get(key)
    if existing:
        if existing['request_id'] != request['request_id']:
            raise ReadingPackError('frozen delivery request changed')
        if path.exists():
            saved = _unseal(path)
            if saved['request'] != request:
                raise ReadingPackError('saved delivery exchange changed')
            response = saved.get('response')
            if response is not None and Draft202012Validator(request['response_schema']).is_valid(response):
                existing['status'] = 'completed'
                return response['result']
        if existing['status'] == 'inflight':
            existing.update(status='outcome_unknown', error='Interrupted call is not automatically retried')
            _seal(root / 'delivery-state.json', state)
        return None
    value = plan['recipe']
    allowance, timeout = _limits(value, key, role)
    job = {'request_id': request['request_id'], 'role': role, 'status': 'not_run', 'started': False, 'receipt': None,
           'allowance_usd': allowance, 'timeout_seconds': timeout}
    state['jobs'][key] = job
    elapsed = time.time() - state['started_at']
    spent = own_costs(plan, state)
    committed = Decimal(spent['known_usd']) + Decimal(spent['unknown_reserve_usd'])
    next_reservation = max(Decimal(str(allowance)), Decimal(str(value.get('unknown_call_reserve_usd', allowance))))
    if elapsed + timeout + value['local_reserve_seconds'] > value['max_wall_seconds']:
        job.update(status='not_run', error='deadline reservation unavailable')
    elif (committed + next_reservation > Decimal(str(value['max_cost_usd']))
          or Decimal(str(value['prior_cost_usd'])) + committed + next_reservation > Decimal(str(value['cumulative_cost_limit_usd']))):
        job.update(status='not_run', error='cumulative cost/reserve unavailable')
    elif len(json.dumps(request, ensure_ascii=False).encode()) > 1024 * 1024:
        job.update(status='not_run', error='request exceeds fixed 1 MiB transport bound')
    elif any(j.get('status') == 'outcome_unknown' or (not j.get('carried')
             and j.get('receipt') and j['receipt'].get('usage_origin') == 'claude-cli'
             and j['receipt'].get('actual_cost_usd') is None)
             for j in state['jobs'].values()):
        job.update(status='not_run', error='prior call outcome unknown')
    else:
        exceeded = [j for j in state['jobs'].values()
                    if not j.get('carried') and j.get('receipt') and j['receipt'].get('actual_cost_usd') is not None
                    and Decimal(str(j['receipt']['actual_cost_usd'])) > Decimal(str(j.get('allowance_usd', value['call_allowance_usd'])))]
        if exceeded:
            job.update(status='not_run', error='provider exceeded per-call allowance')
        else:
            job.update(status='inflight', started=True)
            _seal(path, {'request': request, 'response': None})
            _seal(root / 'delivery-state.json', state)
            worker = value['workers'][role]
            call_started = time.monotonic()
            try:
                response = run_local_adapter(worker['command'], request,
                    timeout=timeout, max_output=2 * 1024 * 1024)
                _seal(path, {'request': request, 'response': response})
                if not Draft202012Validator(request['response_schema']).is_valid(response):
                    raise ReadingPackError('adapter returned invalid result schema or identity')
                job['status'] = 'completed'
            except ReadingPackError as exc:
                job.update(status='error', error=str(exc)[:1000])
            finally:
                job['elapsed_seconds'] = time.monotonic() - call_started
                job['exchange_sha256'] = digest(path)
                try:
                    job['receipt'] = adapter_receipt(worker['command'], request['request_id'])
                except (ReadingPackError, OSError, ValueError) as exc:
                    job['receipt_error'] = str(exc)[:300]
                _seal(root / 'delivery-state.json', state)
            if job['status'] == 'completed':
                return response['result']
    _seal(root / 'delivery-state.json', state)
    return None


def _seed_data(root, plan):
    return None if plan.get('seed') is None else load_seed(root / 'seed', plan['seed']['language'])[1]


def _canonical(root, plan, generated):
    """Return (data, seed_log); seed modules are carried, never regenerated silently."""
    value = plan['recipe']
    # Locators name the frozen normalized-text artifact, never a model-authored alias.
    source = {'format': 'text', 'name': 'source.txt', 'sha256': plan['normalized_sha256']}
    if plan.get('seed') is not None:
        return merge_seed_data(_seed_data(root, plan), plan, generated, source)
    data = empty_language_data(value['language'], plan['title'], plan['author'])
    data['source'] = source
    if plan.get('content_mode') == 'fresh':
        data['book']['contents_note'] = value['scope']
        missing = [u['id'] for u in plan['units'] if u['id'] not in generated]
        if missing:
            done_sections = sum(len(u['sections']) for u in plan['units'] if u['id'] in generated)
            total_sections = sum(len(u['sections']) for u in plan['units'])
            names = ', '.join(missing[:12]) + (' ...' if len(missing)>12 else '')
            note = (f' 現在の生成済み範囲: {len(generated)}/{len(plan["units"])}章、{done_sections}/{total_sections}節。未生成章: {names}。対象範囲の宣言は制作完了を意味しない。'
                    if value['language']=='ja' else
                    f' Current generated coverage: {len(generated)}/{len(plan["units"])} chapters, {done_sections}/{total_sections} sections. Ungenerated: {names}. Declared scope is not completed coverage.')
            data['book']['contents_note'] += note
    for unit in plan['units']:
        content = generated.get(unit['id'])
        data['chapters'].append({'id': unit['id'], 'kind': 'chapter', 'title': unit['title'],
            'sections': [s['title'] for s in unit['sections']], 'summary': content['summary'] if content else '',
            'terms': content['terms'] if content else [], 'status': 'draft',
            'source_locations': [f"source.txt#normalized-text:{unit['start']}-{unit['end']}"]})
        for section in unit['sections']:
            statement = content['sections'][section['id']]['statement'] if content else ''
            if statement:
                data['claims'].append({'id': 'CP-' + section['id'], 'layer': 'descriptive',
                    'kind': 'section_overview', 'statement': statement, 'chapter_ids': [unit['id']],
                    'reader_note': section['title'], 'status': 'draft',
                    'source_locations': [f"source.txt#normalized-text:{section['start']}-{section['end']}"]})
    if plan.get('content_mode') == 'fresh':
        for unit in plan['units']:
            for name, records in module_records(unit, generated.get(unit['id'])).items():
                data[name].extend(records)
    enrich(data, plan)
    return data, None


def _render(root, plan, data):
    project = root / 'project'
    lang = plan['recipe']['language']
    if not project.exists() and plan.get('seed') is not None:
        shutil.copytree(root / 'seed', project, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        config_path = project / 'reading-pack.toml'
        config_path.write_text(draft_config_text(config_path.read_text(encoding='utf-8'), lang, data['book']), encoding='utf-8')
        config = load_config(project)
        if (config.get('status') != 'draft' or config.get('languages') != [lang] or config.get('primary_language') != lang
                or any(v != 'pending' for v in config.get('workflow', {}).values())):
            raise ReadingPackError('seed configuration could not be reset to a draft in the delivery language')
        for other in (project / 'data').glob('pack.*.json'):
            if other.name != f'pack.{lang}.json':
                other.unlink()  # Other-language data stays in seed/; the delivery declares one language.
        for name in ('author-input-state.json', 'author-review-state.json'):
            ledger = project / name
            if ledger.is_file():
                value = json.loads(ledger.read_text(encoding='utf-8'))
                if isinstance(value.get('languages'), dict) and lang in value['languages']:
                    value['languages'] = {lang: value['languages'][lang]}
                    write_json(ledger, value)
    elif not project.exists():
        create_project(project, title=plan['title'], author=plan['author'], languages=[lang],
                       primary_language=lang, profile='general-navigation', scope=plan['recipe']['scope'])
    write_json(project / 'data' / f'pack.{lang}.json', data)
    config = load_config(project)
    pack = render_pack(project, lang, config, data)
    (root / f'reading-pack.{lang}.md').write_text(pack, encoding='utf-8')
    (project / 'dist').mkdir(exist_ok=True)
    (project / 'dist' / f'reading-pack.{lang}.md').write_text(pack, encoding='utf-8')
    return pack


def _own_ids(units):
    """Records this delivery generates; seed-provided records are carried, not re-scored."""
    return {u['id'] for u in units} | {'CP-' + s['id'] for u in units for s in u['sections']}



def evaluation_payload(plan, unit, data, generated, text):
    payload = {'language': plan['recipe']['language'], 'source_text': text[unit['start']:unit['end']],
               'source_start': unit['start'], 'chapter': unit, 'rubric': plan['rubric'],
               'records': [c for c in data['chapters'] if c['id'] == unit['id']] +
                          [c for c in data['claims'] if c['id'] in _own_ids([unit])]}
    if plan.get('content_mode') == 'fresh':
        payload['records'] = chapter_records(data, unit, generated)
        payload['module_decisions'] = {name: {'items_count': len(module['items']), 'omission_reason': module['omission_reason']}
                                       for name, module in generated['modules'].items()}
    return payload


def evaluation_prompt(fresh):
    return EVALUATION_PROMPT + (' Inspect EVERY auxiliary record too, and each module selection or absence reason. '
        'Do not require arbitrary item counts. Flag unsupported omissions and missing central material in module_review. '
        'Use compact evidence anchors: for supported records keep reason and exact source_quote each within '
        'about 15 Japanese characters. For actual issues explain the specific qualification or discrepancy '
        'in about 30-60 characters, with the necessary exact quote. Covered-section reasons around 20 '
        'characters and adequate-module reasons around 30. Do not repeat the full record text. '
        'Preserve specific qualifications and explain every actual issue despite these brevity targets.' if fresh else '')


def run(root):
    root = Path(root)
    with _lock(root):
        plan, state = _load(root)
        if state['state'] not in {'prepared', 'running'}:
            return status(root)
        if plan['engine_sha256'] != _engine_hash():
            raise ReadingPackError('delivery engine changed; preserve the old run and use its frozen engine')
        for name, expected in plan['adapter_files'].items():
            if digest(Path(name)) != expected:
                raise ReadingPackError('frozen adapter file changed')
        value = plan['recipe']
        state.update(state='running', started_at=state['started_at'] or time.time(), outputs={})
        _seal(root / 'delivery-state.json', state)
        text = (root / 'source.txt').read_text(encoding='utf-8')
        generated, evaluations = {}, {}
        fresh = plan.get('content_mode') == 'fresh'
        carried = {k for k, j in state['jobs'].items() if j.get('carried')}
        for unit in plan['units']:
            if unit['id'] in plan.get('deferred_generation', []):
                continue
            payload = {'language': value['language'], 'source_text': text[unit['start']:unit['end']],
                       'source_start': unit['start'], 'chapter': unit, 'rubric': plan['rubric']}
            key = 'generate/' + unit['id']
            result = _carried(root, state, key) if key in carried else _call(
                root, plan, state, key, 'generator', 'generate', payload, full_schema(unit) if fresh else generation_schema(unit),
                GENERATION_PROMPT + (' ' + FRESH_PROMPT if fresh else ''))
            if result is not None:
                generated[unit['id']] = result
            # Deliver even before evaluation, including an explicit partial result.
            if not plan.get('frozen_artifact') and not state.get('repair_snapshot'):
                _render(root, plan, _canonical(root, plan, generated)[0])
        if state.get('repair_snapshot'):
            data = json.loads((root/f"first-pass-pack.{value['language']}.json").read_text())
            seed_log = None
            pack = (root/f"first-pass-reading-pack.{value['language']}.md").read_text()
            (root/f"reading-pack.{value['language']}.md").write_text(pack)
        elif plan.get('frozen_artifact'):
            data = json.loads((root / 'project' / 'data' / f"pack.{value['language']}.json").read_text())
            seed_log = plan['frozen_artifact'].get('seed_log')
            pack = (root / f"reading-pack.{value['language']}.md").read_text(encoding='utf-8')
        else:
            data, seed_log = _canonical(root, plan, generated)
            pack = _render(root, plan, data)
        for unit in plan['units']:
            if unit['id'] not in generated:
                continue
            payload = evaluation_payload(plan, unit, data, generated[unit['id']], text)
            key = 'evaluate/' + unit['id']
            result = _carried(root, state, key) if key in carried else _call(
                root, plan, state, key, 'evaluator', 'artifact_content', payload, full_evaluation_schema(unit, payload['records']) if fresh else evaluation_schema(unit),
                evaluation_prompt(fresh))
            if result is not None:
                evaluations[unit['id']] = result
        global_result = None
        if generated:
            global_result = _carried(root, state, 'evaluate/global') if 'evaluate/global' in carried else _call(
                root, plan, state, 'evaluate/global', 'evaluator', 'artifact_instructions',
                {'language': value['language'], 'pack': pack, 'rubric': plan['rubric']}, GLOBAL_SCHEMA, GLOBAL_PROMPT)
        from .delivery_report import build_report, render_report
        report = build_report(root, plan, state, data, generated, evaluations, global_result,
                              own_ids=None if fresh else _own_ids(plan['units']),
                              completeness=full_completeness(data, plan, generated) if fresh else
                              completeness(_seed_data(root, plan), data, plan, seed_log))
        if value.get('repair_rounds', 0):
            from .delivery_repair import perform
            data, generated, evaluations, global_result, repair = perform(
                root, plan, state, data, generated, evaluations, global_result, text, report)
            report = build_report(root, plan, state, data, generated, evaluations, global_result,
                                  own_ids=None, completeness=full_completeness(data, plan, generated))
            report['repair'] = repair
        write_json(root / 'quality-report.json', report)
        lang = value['language']
        (root / f'quality-report.{lang}.md').write_text(render_report(report, lang), encoding='utf-8')
        state['state'] = ('generation_failed' if not generated else
                          'delivered' if (len(generated) == len(plan['units']) and
                              (not fresh or (len(evaluations) == len(plan['units']) and global_result is not None
                              and report['mechanical']['nonempty_content']['present'] == report['mechanical']['nonempty_content']['expected']
                              and report.get('repair', {}).get('complete', True))))
                          else 'delivered_partial')
        names = [f'reading-pack.{lang}.md', 'quality-report.json', f'quality-report.{lang}.md', f'project/data/pack.{lang}.json']
        names += [name for name in ('repair-report.json', 'repaired-generation.json') if (root/name).exists()]
        state['outputs'] = {name: digest(root / name) for name in names}
        _seal(root / 'delivery-state.json', state)
    exported = export(root, plan.get('output'))
    return {**status(root), 'export': exported}
