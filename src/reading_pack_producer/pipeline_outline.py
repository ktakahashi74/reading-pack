"""Source-bound text outlines, including back matter, for seeded production."""
from __future__ import annotations

import copy
import re
import unicodedata

from reading_pack.errors import ReadingPackError
from reading_pack.importers import _clean_heading, _implicit_heading_title
from reading_pack.project import load_language_data
from reading_pack.staging import _computed_plan_id, _existing_kind, _planned_records, apply_import_plan, validate_import_plan
from .candidates import _source_text_snapshot, normalize_text
from .pipeline_generation import author_contract


def title_key(value: str) -> str:
    return ''.join(c for c in unicodedata.normalize('NFKC', value).casefold()
                   if not c.isspace() and not unicodedata.category(c).startswith('P'))


def text_heading_units(plan: dict, text: str, fmt: str) -> list[dict]:
    """Use the importer's exact heading rules; retain normalized source offsets."""
    headings, offset, fenced = [], 0, False
    for line in text.splitlines(keepends=True):
        if fmt == 'markdown' and re.match(r'^\s*(```|~~~)', line):
            fenced = not fenced
            offset += len(line)
            continue
        pattern = r'^(#{1,6})\s+(.+?)\s*$' if fmt == 'markdown' else r'^(\*{1,8})\s+(?:TODO\s+|DONE\s+)?(.+?)\s*$'
        match = None if fenced else re.match(pattern, line)
        if match and _clean_heading(match.group(2)):
            headings.append({'level': len(match.group(1)), 'title': _clean_heading(match.group(2)), 'source_start': offset})
        offset += len(line)
    explicit = fmt == 'org' and re.search(r'^#\+TITLE:', text, re.I | re.M)
    if not explicit and _implicit_heading_title([(h['level'], h['title']) for h in headings])[1]:
        headings = headings[1:]
    units = [u for u in plan['units'] if u['kind'] != 'book']
    if len(headings) != len(units) or any(normalize_text(h['title']) != normalize_text(u['title']) for h, u in zip(headings, units)):
        raise ReadingPackError('parsed text outline does not match normalized source headings')
    return [{**h, 'unit_id': u['staging_id'], 'parent_id': u['parent_id'], 'kind': u['kind']}
            for h, u in zip(headings, units)]


def reconcile_text_outline(runner, project) -> None:
    from .pipeline import _unseal, _seal
    source = runner.manifest['sources'][0]
    plan = _unseal(runner.root / 'source-import-plan.json')
    _, text = _source_text_snapshot(runner.root / source['path'], source_format=source['format'])
    headings = text_heading_units(plan, text, source['format'])
    aliases = []
    if runner.manifest['seed'] is not None:
        canonical = load_language_data(project, runner.lang)
        rule = author_contract(project, runner.lang).get('chapters', {'mode': 'generate', 'protected_ids': []})
        if rule['mode'] in {'provided', 'omit'} or rule['protected_ids']:
            # Never override an author's authoritative structural set. The audit
            # can expose the disagreement for the author to resolve.
            _seal(runner.root / 'source-text-outline.json', {'headings': headings,
                'source_sha256': source['sha256'], 'author_structure_preserved': True, 'aliases': []})
            return
        mapped = copy.deepcopy(plan)
        containers = [u for u in mapped['units'] if u['kind'] not in {'book', 'section'}]
        for unit in containers:
            candidates = [c for c in canonical['chapters'] if _existing_kind(c) == unit['kind'] and title_key(c['title']) == title_key(unit['title'])]
            if not candidates and unit['kind'] in {'frontmatter', 'afterword', 'appendix', 'notes'}:
                same_kind = [c for c in canonical['chapters'] if _existing_kind(c) == unit['kind']]
                if len(same_kind) == 1 and sum(u['kind'] == unit['kind'] for u in containers) == 1:
                    candidate = same_kind[0]
                    if title_key(candidate['title']).startswith(title_key(unit['title'])):
                        candidates = [candidate]
            if len(candidates) > 1:
                raise ReadingPackError('ambiguous canonical/source heading alias')
            if not candidates:
                continue
            old = candidates[0]
            if unit['title'] != old['title']:
                aliases.append({'unit_id': unit['staging_id'], 'source_title': unit['title'], 'canonical_title': old['title'], 'canonical_id': old['id']})
                unit['title'] = old['title']
                unit['provenance'][0]['method'] = 'reading-pack.pipeline.canonical-title-alias'
            for section in mapped['units']:
                if section['parent_id'] != unit['staging_id']:
                    continue
                titles = [t for t in old['sections'] if title_key(t) == title_key(section['title'])]
                if len(titles) > 1:
                    raise ReadingPackError('ambiguous canonical/source section alias')
                if titles and titles[0] != section['title']:
                    aliases.append({'unit_id': section['staging_id'], 'source_title': section['title'], 'canonical_title': titles[0], 'canonical_id': old['id']})
                    section['title'] = titles[0]
                    section['provenance'][0]['method'] = 'reading-pack.pipeline.canonical-title-alias'
        mapped['plan_id'] = _computed_plan_id(mapped)
        validate_import_plan(mapped)
        proposed = _planned_records(mapped)
        before_structure = [(_existing_kind(c), c['title'], c.get('pages', ''), c['sections']) for c in canonical['chapters']]
        after_structure = [(c['kind'], c['title'], c['pages'], c['sections']) for c in proposed]
        if before_structure != after_structure:
            apply_import_plan(project, mapped, runner.lang, runner.root / source['path'])
        from .pipeline_drafts import register_draft_revisions
        register_draft_revisions(project, runner.lang, canonical, origin='pipeline:source-outline')
        after = load_language_data(project, runner.lang)
        old_by_id = {c['id']: c for c in canonical['chapters']}
        for chapter in after['chapters']:
            if chapter['id'] in old_by_id:
                old = old_by_id[chapter['id']]
                if chapter['summary'] != old['summary'] or chapter['terms'] != old['terms']:
                    raise ReadingPackError('outline reconciliation changed supplied prose')
        _seal(runner.root / 'seed-reconciled-import-plan.json', mapped)
    after = load_language_data(project, runner.lang)
    alias_titles = {a['unit_id']: a['canonical_title'] for a in aliases}
    for heading in headings:
        if heading['kind'] == 'section':
            continue
        matches = [c for c in after['chapters'] if _existing_kind(c) == heading['kind'] and
                   title_key(c['title']) == title_key(alias_titles.get(heading['unit_id'], heading['title']))]
        if len(matches) == 1:
            heading['canonical_id'] = matches[0]['id']
    _seal(runner.root / 'source-text-outline.json', {'headings': headings,
        'source_sha256': source['sha256'], 'aliases': aliases, 'author_structure_preserved': False})
