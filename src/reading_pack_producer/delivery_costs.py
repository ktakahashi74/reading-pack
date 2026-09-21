"""Non-overlapping cost history, including failed and recovered calls."""
from decimal import Decimal
from pathlib import Path
from reading_pack.errors import ReadingPackError


def own_costs(plan, state):
    known = Decimal(0); reserve = Decimal(0); unknown = 0; calls = 0
    for job in state['jobs'].values():
        if job.get('carried') or not job.get('started'):
            continue
        calls += 1
        cost = (job.get('receipt') or {}).get('actual_cost_usd')
        if cost is None:
            unknown += 1
            reserve += Decimal(str(plan['recipe'].get('unknown_call_reserve_usd',
                job.get('allowance_usd', plan['recipe']['call_allowance_usd']))))
        else:
            known += Decimal(str(cost))
    return {'run_id': plan['id'], 'known_usd': str(known), 'unknown_cost_calls': unknown,
            'unknown_reserve_usd': str(reserve), 'calls_started': calls}


def history(root, plan, state):
    """Freeze history at successor preparation; authenticate legacy predecessor links."""
    from .delivery import _load, digest
    if 'cost_history' in plan:
        return plan['cost_history'] + [own_costs(plan, state)], plan['cost_base_prior_usd']
    rows = [own_costs(plan, state)]; seen = {Path(root).resolve()}
    while plan.get('predecessor'):
        ref = plan['predecessor']; previous = Path(ref['run']).resolve()
        if previous in seen:
            raise ReadingPackError('cyclic delivery cost history')
        seen.add(previous)
        if digest(previous/'delivery-plan.json') != ref['plan_sha256'] or digest(previous/'delivery-state.json') != ref['state_sha256']:
            raise ReadingPackError('predecessor cost evidence changed')
        plan, state = _load(previous)
        if plan['id'] != ref['id']:
            raise ReadingPackError('predecessor cost identity changed')
        if 'cost_history' in plan:
            return plan['cost_history'] + [own_costs(plan, state)] + rows, plan['cost_base_prior_usd']
        rows.insert(0, own_costs(plan, state))
    return rows, str(plan['recipe']['prior_cost_usd'])


def totals(rows, base):
    known = sum((Decimal(r['known_usd']) for r in rows), Decimal(0))
    reserve = sum((Decimal(r['unknown_reserve_usd']) for r in rows), Decimal(0))
    return {'chain_known_usd': str(known), 'chain_unknown_cost_calls': sum(r['unknown_cost_calls'] for r in rows),
            'chain_unknown_reserve_usd': str(reserve), 'chain_calls_started': sum(r['calls_started'] for r in rows),
            'base_prior_declared_usd': str(base),
            'base_plus_chain_known_usd': str(Decimal(base)+known),
            'base_plus_chain_and_reserve_usd': str(Decimal(base)+known+reserve),
            'unknown_reserve_is_actual_cost': False, 'history': rows}
