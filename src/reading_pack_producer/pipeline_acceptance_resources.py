"""Worst-case finite stage allocations for direct artifact production."""
from decimal import Decimal
from .work_ledger import artifact_hash


def artifact_operating_plan(manifest, chunks, *, layout_chapters=0, seed_populated=False):
    from .pipeline_generation import generation_chunks
    recipe = manifest['recipe']
    envelope = recipe.get('operating_envelope')
    limit = recipe.get('artifact_inspection_batch_limit', 128)
    extra = recipe.get('artifact_adjudication_limit', 1)
    size = recipe.get('generation_chunk_characters', 12000)
    repair_size = recipe.get('repair_chunk_characters', recipe['chunk_characters'])
    # Independent review can split into one call per returned candidate.
    per_unit = 1 + recipe.get('max_generation_candidates', 24)
    minimum = {'preparation': 2 * layout_chapters + int(recipe['profile'] == 'auto'),
               'generation': 0 if seed_populated else len(generation_chunks(chunks, size)) * per_unit,
               'development': limit + extra,
               'repair': len(generation_chunks(chunks, repair_size)) * per_unit if recipe['max_rounds'] > 1 else 0,
               'final': limit + extra if recipe['max_rounds'] > 1 else 0}
    reasons = []
    if recipe['max_rounds'] > 2 or recipe['max_attempts'] != 1:
        reasons.append('artifact production permits at most one repair and one transport attempt')
    if recipe['profile'] == 'auto':
        reasons.append('artifact production currently requires a fixed profile')
    if envelope:
        caps = envelope['phase_call_limits']
        reasons += [f'{phase}: worst-case {count} calls exceeds allocation {caps[phase]}'
                    for phase, count in minimum.items() if count > caps[phase]]
        total = sum(caps.values())
        if total > recipe['max_calls']:
            reasons.append('phase allocations exceed total call limit')
        if Decimal(total) * Decimal(str(envelope['call_allowance_usd'])) > Decimal(str(envelope['max_cost_usd'])):
            reasons.append('phase reservations exceed cumulative monetary limit')
        if total * recipe['timeout_seconds'] + envelope['local_reserve_seconds'] > envelope['max_wall_seconds']:
            reasons.append('timeout allocations and local reserve exceed deadline')
    else:
        caps = minimum
        total = sum(caps.values())
    return {'contract_version': 'artifact-acceptance-1', 'mode': envelope['mode'] if envelope else 'experimental',
            'admitted': not reasons, 'reasons': reasons, 'quality_qualified': False,
            'engine_sha256': manifest['engine_sha256'], 'recipe_sha256': artifact_hash(recipe),
            'minimum_phase_calls': minimum, 'phase_call_limits': caps, 'maximum_calls': total,
            'inspection_batch_limit': limit, 'adjudication_limit': extra, 'reader_questions': 0,
            'repair_round_limit': min(1, recipe['max_rounds'] - 1),
            'final_reserved_calls': caps['final'], 'final_reserved_seconds': caps['final'] * recipe['timeout_seconds'],
            'maximum_wall_seconds': envelope['max_wall_seconds'] if envelope else None,
            'maximum_call_allowances_usd': str(Decimal(total) * Decimal(str(envelope['call_allowance_usd']))) if envelope else None,
            'planning_limit': 'Admission reserves bounded work, not semantic success or measured completion time.'}
