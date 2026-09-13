"""A frozen operating envelope, shared across resumes and explicit restarts.

Reservations are deliberately not refunds: a failed or interrupted call still
consumes its full allowance. This bounds the planned work independently of a
provider's cache accounting. Actual provider usage is reported separately.
"""
from __future__ import annotations

import copy
import json
import hashlib
import math
import time
import signal
import threading
from contextlib import contextmanager
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from reading_pack.errors import ReadingPackError
from .work_ledger import artifact_hash

PHASES = ('preparation', 'generation', 'development', 'repair', 'final')
MODEL_STAGES = {'profile', 'structure_select', 'structure_review', 'benchmark', 'benchmark_review'}


def adapter_receipt(command: list[str], request_id: str) -> dict | None:
    """Read a hash-bound CLI receipt, never fabricate usage for another adapter."""
    if '--audit-dir' not in command:
        return None
    index = command.index('--audit-dir')
    if index + 1 >= len(command):
        return None
    root = Path(command[index + 1]) / request_id
    path = root / 'audit.json'
    if not path.exists():
        return None
    audit = json.loads(path.read_text())
    request = json.loads((root / 'input.json').read_text())
    if request.get('request_id') != request_id:
        raise ReadingPackError('usage receipt request binding changed')
    if audit.get('raw_sha256') and hashlib.sha256((root / 'raw.jsonl').read_bytes()).hexdigest() != audit['raw_sha256']:
        raise ReadingPackError('usage receipt raw output changed')
    cost = audit.get('total_cost_usd')
    if cost is not None and (type(cost) not in (int, float) or not math.isfinite(cost) or cost < 0):
        raise ReadingPackError('invalid provider usage cost')
    receipt = {'actual_cost_usd': cost, 'receipt_path': str(path.resolve()),
               'receipt_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
               'receipt_valid': audit.get('valid', False), 'usage_origin': 'claude-cli',
               'elapsed_seconds': audit.get('elapsed_seconds')}
    if audit.get('salvaged_additional_properties'):
        receipt['salvaged_additional_properties'] = list(audit['salvaged_additional_properties'])
    return receipt


def phase_for(stage: str, key: str) -> str:
    if key.startswith('artifact/final/'):
        return 'final'
    if '/holdout/' in key:
        return 'final'
    if stage in MODEL_STAGES:
        return 'preparation'
    if stage == 'repair' or (key.split('/', 1)[0].isdigit() and int(key.split('/', 1)[0]) > 0):
        return 'repair'
    if stage in {'generate', 'review'}:
        return 'generation'
    return 'development'


def selected_benchmark_chunks(chunks: list[dict], maximum: int | None) -> list[dict]:
    """A deterministic, source-role preserving sample; old suites stay intact."""
    if maximum is None or len(chunks) <= maximum:
        return chunks
    groups = {}
    for chunk in chunks:
        groups.setdefault(chunk['source_id'], []).append(chunk)
    if len(groups) > maximum:
        raise ReadingPackError('benchmark capacity cannot represent every source; input is outside this envelope')
    # Every supplied source participates. Spend remaining slots on the primary
    # manuscript, spread across its text rather than on more footnote questions.
    selected = {values[0]['id'] for values in groups.values()}
    primary = next(values for values in groups.values() if values[0]['role'] == 'primary-book')
    remaining = maximum - len(selected)
    if remaining:
        for index in range(1, remaining + 1):
            selected.add(primary[round(index * (len(primary) - 1) / remaining)]['id'])
    for chunk in chunks:
        if len(selected) >= maximum:
            break
        selected.add(chunk['id'])
    return [chunk for chunk in chunks if chunk['id'] in selected]


def populated_seed(root: Path, manifest: dict) -> bool:
    if manifest['seed'] is None:
        return False
    from reading_pack.project import load_language_data
    from reading_pack.profiles import PROFILES, load_quality_plan
    profile = manifest['recipe']['profile']
    if profile == 'auto':
        profile = load_quality_plan(root / 'seed')['profile']
    data = load_language_data(root / 'seed', manifest['language'])
    return bool(data['chapters'] and all(c.get('summary') for c in data['chapters']) and
                all(data.get(m) for m in PROFILES[profile].required_modules))


def operating_plan(manifest: dict, chunks: list[dict], *, layout_chapters: int = 0,
                   seed_populated: bool = False) -> dict:
    from .pipeline_contracts import ARTIFACT_CONTRACT_VERSION, manifest_contract
    if manifest_contract(manifest) == ARTIFACT_CONTRACT_VERSION:
        from .pipeline_acceptance_resources import artifact_operating_plan
        return artifact_operating_plan(manifest, chunks, layout_chapters=layout_chapters, seed_populated=seed_populated)
    recipe = manifest['recipe']
    envelope = recipe.get('operating_envelope')
    if not envelope:
        return {'mode': 'experimental', 'admitted': False,
                'reason': 'No end-to-end operating envelope or measured qualification.',
                'quality_qualified': False}
    from .pipeline_generation import generation_chunks
    chosen = selected_benchmark_chunks(chunks, recipe.get('benchmark_chunk_limit'))
    questions = len(chosen)
    repetitions = recipe['answer_repetitions']
    batches = math.ceil(questions / recipe.get('evaluation_batch_size', 1))
    # Reserve the entire final examination, including one uncertain-grade
    # adjudication per question. Earlier work cannot borrow these slots.
    final_calls = repetitions * (questions + batches + questions)
    development_calls = len(chunks) + repetitions * (questions + batches)
    minimum = {
        'preparation': 2 * questions + 2 * layout_chapters + int(recipe['profile'] == 'auto' and manifest['seed'] is None),
        'generation': 0 if seed_populated else
            2 * len(generation_chunks(chunks, recipe.get('generation_chunk_characters', 12000))),
        'development': development_calls,
        # Even one local repair needs generation, independent review and the
        # complete development recheck. Do not admit a repair loop with no
        # room to evaluate its result. More affected chunks require more slots.
        'repair': 2 + development_calls if recipe['max_rounds'] == 2 else 0,
        'final': final_calls,
    }
    caps = envelope['phase_call_limits']
    ceiling = Decimal(str(envelope['call_allowance_usd']))
    total_calls = sum(caps.values())
    reasons = [f'{phase}: minimum {count} calls exceeds allocation {caps[phase]}'
               for phase, count in minimum.items() if count > caps[phase]]
    if total_calls > recipe['max_calls']:
        reasons.append('phase allocations exceed the overall call limit')
    if Decimal(total_calls) * ceiling > Decimal(str(envelope['max_cost_usd'])):
        reasons.append('phase allowances exceed the overall monetary allowance')
    if total_calls * recipe['timeout_seconds'] + envelope['local_reserve_seconds'] > envelope['max_wall_seconds']:
        reasons.append('phase timeout allowances and local reserve exceed the end-to-end deadline')
    if recipe['max_rounds'] > 2 or recipe['max_attempts'] != 1:
        reasons.append('predictable production permits one repair round and no automatic transport retry')
    return {'mode': envelope['mode'], 'admitted': not reasons, 'reasons': reasons,
            'engine_sha256': manifest['engine_sha256'], 'recipe_sha256': artifact_hash(recipe),
            'source_characters': sum(c['end'] - c['start'] for c in chunks),
            'source_chunks': len(chunks), 'benchmark_chunk_ids': [c['id'] for c in chosen],
            'development_questions': questions, 'final_questions': questions,
            'minimum_phase_calls': minimum, 'phase_call_limits': copy.deepcopy(caps),
            'planning_limit': 'Minimums exclude extra review batches, disputed findings and preparation revisions. Phase caps bound those branches; only measured trials establish completion within the allocations.',
            'maximum_calls': total_calls, 'maximum_call_allowances_usd': str(Decimal(total_calls) * ceiling),
            'maximum_wall_seconds': envelope['max_wall_seconds'],
            'final_reserved_calls': final_calls,
            'final_reserved_seconds': final_calls * recipe['timeout_seconds'],
            'cost_kind': 'provider-reported USD allowance; not an invoice or a hard provider billing guarantee',
            'quality_qualified': False}


def _now() -> float:
    return time.time()


@contextmanager
def initialization_guard(recipe: dict):
    envelope = recipe.get('operating_envelope')
    if not envelope:
        yield
        return
    if threading.current_thread() is not threading.main_thread() or not hasattr(signal, 'setitimer'):
        raise ReadingPackError('bounded input preparation requires a dedicated POSIX main process')
    if signal.getitimer(signal.ITIMER_REAL) != (0.0, 0.0):
        raise ReadingPackError('refusing to replace an existing process timer')
    previous = signal.getsignal(signal.SIGALRM)
    def expired(signum, frame):
        raise ReadingPackError('input preparation exceeded its reserved time; no model call was made')
    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, max(0.001, envelope['local_reserve_seconds']))
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


class ResourceLimit(Exception):
    def __init__(self, state: str, reason: str):
        self.state, self.reason = state, reason


class Resources:
    def __init__(self, runner):
        self.runner = runner
        self.root = runner.root
        self.envelope = runner.recipe.get('operating_envelope')
        self.started_monotonic = time.monotonic()
        self.started_wall = _now()
        self.active = False

    def begin(self) -> None:
        if not self.envelope:
            return
        from .pipeline import _seal, _unseal
        if (self.root / 'resource-successor.json').exists():
            raise ResourceLimit('superseded', 'resource envelope transferred to its recorded successor; this run cannot resume')
        layout = self.root / 'source-layout.json'
        plan = operating_plan(self.runner.manifest, self.runner.chunks,
            layout_chapters=len(_unseal(layout)['chapters']) if layout.exists() else 0,
            seed_populated=populated_seed(self.root,self.runner.manifest))
        path = self.root / 'operating-plan.json'
        if path.exists() and _unseal(path) != plan:
            raise ReadingPackError('frozen operating plan changed')
        _seal(path, plan)
        if not plan['admitted']:
            raise ResourceLimit('input_outside_envelope', '; '.join(plan['reasons']))
        ledger = self.root / 'resource-ledger.json'
        if not ledger.exists():
            inherited = self.runner.manifest.get('restart_origin', {}).get('resources')
            if inherited:
                old = _unseal(Path(inherited['path']))
                if artifact_hash(old) != inherited['sha256']:
                    raise ReadingPackError('restart resource ledger changed')
                if old['envelope_sha256'] != artifact_hash(self.envelope):
                    raise ReadingPackError('restart cannot change the cumulative operating envelope')
                value = copy.deepcopy(old)
            else:
                started = self.started_wall - self.runner.manifest.get('initialization_seconds', 0)
                value = {'envelope_sha256': artifact_hash(self.envelope), 'started_at': started,
                         'deadline': started + self.envelope['max_wall_seconds'],
                         'reservations': [], 'finished_at': None}
            _seal(ledger, value)
        else:
            value = _unseal(ledger)
            if value['envelope_sha256'] != artifact_hash(self.envelope):
                raise ReadingPackError('operating envelope changed')
            value['finished_at'] = None
            _seal(ledger, value)
        self.active = True
        if self.envelope['mode'] == 'production' and self.runner.contract_version != 'artifact-acceptance-1':
            from .pipeline_qualification import verify_qualification
            self.qualification = verify_qualification(self.runner)
        self.check_deadline()

    def check_profile(self, profile: str) -> None:
        if self.envelope and self.envelope['mode'] == 'production' and self.runner.contract_version != 'artifact-acceptance-1':
            measured = self.qualification['report']['supported_input_modes'][self.qualification['mode']]
            if profile not in measured['profiles']:
                raise ResourceLimit('input_outside_envelope', 'selected book profile was not measured in this qualification')

    @contextmanager
    def deadline_guard(self):
        if not self.envelope:
            yield
            return
        if threading.current_thread() is not threading.main_thread() or not hasattr(signal, 'setitimer'):
            raise ResourceLimit('blocked_execution', 'end-to-end deadline requires a dedicated POSIX main process')
        if signal.getitimer(signal.ITIMER_REAL) != (0.0, 0.0):
            raise ResourceLimit('blocked_execution', 'refusing to replace an existing process timer')
        previous = signal.getsignal(signal.SIGALRM)
        def expired(signum, frame):
            raise ResourceLimit('deadline_exceeded', 'end-to-end deadline reached during execution')
        signal.signal(signal.SIGALRM, expired)
        signal.setitimer(signal.ITIMER_REAL, max(0.001, self.remaining()))
        try:
            yield
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous)

    def remaining(self) -> float:
        from .pipeline import _unseal
        ledger = _unseal(self.root / 'resource-ledger.json')
        # A backwards wall-clock adjustment must not create runtime credit.
        now = max(_now(), self.started_wall + time.monotonic() - self.started_monotonic)
        return ledger['deadline'] - now

    def check_deadline(self) -> None:
        if self.envelope and self.active and self.remaining() <= 0:
            raise ResourceLimit('deadline_exceeded', 'end-to-end deadline reached; no automatic restart')

    def reserve(self, stage: str, key: str, request_id: str) -> dict | None:
        if not self.envelope:
            return None
        from .pipeline import _seal, _unseal
        if not (self.root / 'resource-ledger.json').exists():
            self.begin()
        self.check_deadline()
        ledger = _unseal(self.root / 'resource-ledger.json')
        phase = phase_for(stage, key)
        counts = Counter(item['phase'] for item in ledger['reservations'])
        if counts[phase] >= self.envelope['phase_call_limits'][phase]:
            raise ResourceLimit('phase_budget_exhausted', f'{phase} call allocation exhausted; remaining phases retain their allocations')
        allowance = Decimal(str(self.envelope['call_allowance_usd']))
        spent = sum((Decimal(item['allowance_usd']) for item in ledger['reservations']), Decimal(0))
        if spent + allowance > Decimal(str(self.envelope['max_cost_usd'])):
            raise ResourceLimit('budget_exhausted', 'cumulative monetary allowance exhausted')
        plan = _unseal(self.root / 'operating-plan.json')
        reserve_time = (0 if phase == 'final' else plan['final_reserved_seconds']) + self.envelope['local_reserve_seconds']
        timeout = min(self.runner.recipe['timeout_seconds'], self.remaining() - reserve_time)
        if timeout <= 0:
            raise ResourceLimit('deadline_exceeded', 'remaining time is reserved for final evaluation and local delivery')
        item = {'request_id': request_id, 'job': key, 'phase': phase,
                'allowance_usd': str(allowance), 'timeout_seconds': timeout, 'reserved_at': _now(),
                'state': 'reserved', 'actual_cost_usd': None}
        ledger['reservations'].append(item)
        _seal(self.root / 'resource-ledger.json', ledger)
        return item

    def finish_call(self, reservation: dict | None, outcome: str, receipt: dict | None = None) -> None:
        if not reservation:
            return
        from .pipeline import _seal, _unseal
        ledger = _unseal(self.root / 'resource-ledger.json')
        item = next(r for r in ledger['reservations'] if r['request_id'] == reservation['request_id'] and r['reserved_at'] == reservation['reserved_at'])
        item.update(state=outcome, returned_at=_now())
        if receipt:
            item.update(receipt)
        _seal(self.root / 'resource-ledger.json', ledger)
        if item['actual_cost_usd'] is not None and Decimal(str(item['actual_cost_usd'])) > Decimal(item['allowance_usd']):
            raise ResourceLimit('cost_allowance_exceeded', 'provider reported cost above its requested call allowance')
        self.check_deadline()

    def finish(self) -> None:
        if not self.active or not self.envelope or not (self.root / 'resource-ledger.json').exists():
            return
        from .pipeline import _seal, _unseal
        value = _unseal(self.root / 'resource-ledger.json')
        value['finished_at'] = _now()
        _seal(self.root / 'resource-ledger.json', value)


def resource_status(root: Path) -> dict:
    from .pipeline import _unseal
    path = root / 'resource-ledger.json'
    if not path.exists():
        return {'measured': False}
    ledger = _unseal(path)
    values = ledger['reservations']
    known = [Decimal(str(v['actual_cost_usd'])) for v in values if v['actual_cost_usd'] is not None]
    return {'measured': True, 'started_at_utc': datetime.fromtimestamp(ledger['started_at'], timezone.utc).isoformat(),
            'deadline_utc': datetime.fromtimestamp(ledger['deadline'], timezone.utc).isoformat(),
            'elapsed_seconds': max(0, (ledger['finished_at'] or _now()) - ledger['started_at']),
            'phase_calls': dict(Counter(v['phase'] for v in values)),
            'reserved_usd': str(sum((Decimal(v['allowance_usd']) for v in values), Decimal(0))),
            'known_reported_usd': str(sum(known, Decimal(0))),
            'unknown_cost_calls': len(values) - len(known),
            'cost_kind': 'CLI/provider report, not an invoice'}
