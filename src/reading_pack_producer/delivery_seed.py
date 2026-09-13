"""Seeded deliveries: keep author-provided modules, generate only chapter content.

A seed is an existing Reading Pack project (typically the canonical project with
author-reviewed misreadings, certainty, claims, names, glossary, references and
policies).  Generation fills chapter summaries, chapter terms and section
overviews; every other module is carried unchanged and its completeness is
measured, so a delivery can never silently drop an author-provided layer.
"""
from __future__ import annotations

import copy
import json
import re
import unicodedata
from pathlib import Path

from reading_pack.errors import ReadingPackError
from reading_pack.project import _toml_string, load_config, load_language_data

AUXILIARY = ('certainty', 'claims', 'misreadings', 'policies', 'names', 'glossary', 'references')
CONTENT = ('summaries', 'chapter_terms')
MODULES = ('chapters',) + CONTENT + AUXILIARY
POLICIES = ('preserve', 'regenerate')


def normalize_title(value: str) -> str:
    """Compare headings ignoring whitespace and compatibility forms, never meaning."""
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', value))


def load_seed(root: Path, language: str) -> tuple[dict, dict]:
    config = load_config(root)
    if language not in config.get('languages', []):
        raise ReadingPackError('seed project does not define the delivery language')
    data = load_language_data(root, language)
    if not data['chapters']:
        raise ReadingPackError('seed project has no chapters; declare the chapter structure before delivery')
    return config, data


def author_input_modes(root: Path, language: str) -> dict | None:
    path = root / 'author-input-state.json'
    if not path.is_file():
        return None
    try:
        state = json.loads(path.read_text(encoding='utf-8'))
        modules = state['languages'][language]['modules']
        return {name: modules[name]['mode'] for name in sorted(modules)}
    except (ValueError, KeyError, TypeError):
        return None


def parse_chapter_map(value) -> dict:
    if value is None:
        return {}
    if (not isinstance(value, dict) or not value or len(value) > 1024
            or any(not isinstance(k, str) or not isinstance(v, str) or not k or not v for k, v in value.items())):
        raise ReadingPackError('chapter map must be a non-empty JSON object of seed chapter id to manuscript heading')
    return dict(value)


def align_units(units: list[dict], chapters: list[dict], chapter_map: dict) -> list[dict]:
    """Bind manuscript chapters to seed chapter ids one-to-one, in order, without guessing."""
    seed_ids = [c['id'] for c in chapters]
    unknown = sorted(set(chapter_map) - set(seed_ids))
    if unknown:
        raise ReadingPackError('chapter map names unknown seed chapters: ' + ', '.join(unknown))
    if len(units) != len(chapters):
        raise ReadingPackError(f'seed declares {len(chapters)} chapters but the manuscript outline has {len(units)}; '
                               'restructure the manuscript or the seed explicitly')
    aligned = []
    for unit, chapter in zip(units, chapters):
        expected = chapter_map.get(chapter['id'])
        if expected is not None:
            if unit['title'] != expected:
                raise ReadingPackError(f"chapter map expects {chapter['id']} = {expected!r} but the manuscript "
                                       f"has {unit['title']!r} at that position")
        elif normalize_title(unit['title']) != normalize_title(chapter['title']):
            raise ReadingPackError(f"seed chapter {chapter['id']} {chapter['title']!r} does not match manuscript "
                                   f"heading {unit['title']!r}; supply an explicit chapter map")
        levels = [s['level'] for s in unit['sections']]
        top = min(levels) if levels else None
        immediate = [s for s in unit['sections'] if s['level'] == top]
        suffix = chapter['id'][3:]
        sections = []
        for j, s in enumerate(immediate):
            end = immediate[j + 1]['start'] if j + 1 < len(immediate) else unit['end']
            sections.append({'level': s['level'], 'title': s['title'], 'manuscript_title': s['title'],
                             'start': s['start'], 'end': end, 'id': f'S{suffix}-{j + 1:02d}'})
        declared = chapter.get('sections') or []
        if declared:
            if [normalize_title(t) for t in declared] != [normalize_title(s['title']) for s in sections]:
                raise ReadingPackError(f"seed chapter {chapter['id']} section titles do not match the manuscript's "
                                       'immediate subheadings; restructure explicitly before any model call')
            for s, title in zip(sections, declared):
                s['title'] = title
        aligned.append({'id': chapter['id'], 'title': chapter['title'], 'manuscript_title': unit['title'],
                        'start': unit['start'], 'end': unit['end'], 'sections': sections,
                        'merged_subheadings': len(unit['sections']) - len(immediate)})
    return aligned


def _locator(unit: dict) -> str:
    return f"source.txt#normalized-text:{unit['start']}-{unit['end']}"


def merge_seed_data(seed: dict, plan: dict, generated: dict, source: dict) -> tuple[dict, dict]:
    """Return the delivery data and a per-chapter log of what came from the seed or a model."""
    data = copy.deepcopy(seed)
    data['source'] = source
    policy = plan['seed']['policy']
    units = {u['id']: u for u in plan['units']}
    log, chapters = {}, []
    for record in data['chapters']:
        unit = units[record['id']]
        content = generated.get(unit['id'])
        original, record = record, dict(record)
        if not record.get('sections') and unit['sections']:
            record['sections'] = [s['title'] for s in unit['sections']]
        entry = {}
        for field, default in (('summary', ''), ('terms', [])):
            seed_value = record.get(field) or default
            if content is not None and (policy == 'regenerate' or not seed_value):
                record[field] = content[field]
                entry[field] = 'generated' if content[field] else 'empty'
                if content[field] != seed_value:
                    record['status'] = 'draft'
            else:
                record[field] = seed_value
                entry[field] = 'seed' if seed_value else 'empty'
        if record != original:
            # A changed record is this delivery's content: it needs a new review and points at this source.
            record['status'] = 'draft'
            record['source_locations'] = [_locator(unit)]
        entry['status'] = record['status']
        log[record['id']] = entry
        chapters.append(record)
    data['chapters'] = chapters
    existing = {c['id'] for c in data['claims']}
    for unit in plan['units']:
        content = generated.get(unit['id'])
        for section in unit['sections']:
            cid = 'CP-' + section['id']
            if cid in existing:
                raise ReadingPackError(f'seed already defines {cid}; section overviews never replace seed claims')
            statement = content['sections'][section['id']]['statement'] if content else ''
            if statement:
                data['claims'].append({'id': cid, 'layer': 'descriptive', 'kind': 'section_overview',
                    'statement': statement, 'chapter_ids': [unit['id']], 'reader_note': section['title'],
                    'status': 'draft', 'source_locations': [_locator(section)]})
    return data, log


def completeness(seed: dict | None, data: dict, plan: dict, log: dict | None) -> dict:
    """Count every module of the delivered data against the seed; losses are regressions."""
    def ids(records):
        return [r['id'] for r in records]
    modules = {}
    for key in ('chapters',) + AUXILIARY:
        s, o = ids(seed.get(key, [])) if seed else [], ids(data.get(key, []))
        modules[key] = {'seed': len(s), 'output': len(o),
                        'lost_ids': [i for i in s if i not in o], 'added_ids': [i for i in o if i not in s]}
    for key, field in (('summaries', 'summary'), ('chapter_terms', 'terms')):
        entries = [e[field] for e in (log or {}).values()]
        modules[key] = {'seed': sum(bool(c.get(field)) for c in (seed['chapters'] if seed else [])),
                        'output': sum(bool(c.get(field)) for c in data['chapters']),
                        'preserved': entries.count('seed'), 'generated': entries.count('generated'),
                        'empty': sum(not c.get(field) for c in data['chapters'])}
    regressions = [k for k in ('chapters',) + AUXILIARY if modules[k]['lost_ids']]
    result = {'seed': None, 'modules': modules, 'chapters': log or {}, 'lost_modules': regressions,
              'chapter_map': [{'id': u['id'], 'title': u['title'], 'manuscript_title': u.get('manuscript_title', u['title']),
                               'start': u['start'], 'end': u['end'], 'sections': len(u['sections']),
                               'merged_subheadings': u.get('merged_subheadings', 0)} for u in plan['units']]}
    conflicts = []
    if seed is not None:
        modes = plan['seed'].get('author_input_modes') or {}
        added = modules['claims']['added_ids']
        if added and modes.get('claims') == 'provided':
            conflicts.append({'module': 'claims', 'seed_mode': 'provided', 'added': len(added),
                              'note': 'The seed declares claims as a complete provided set; this delivery added '
                                      'draft section overviews. Author review must accept them (augment) or drop them.'})
        if modes.get('summaries') == 'provided' and any(e['summary'] == 'generated' for e in (log or {}).values()):
            conflicts.append({'module': 'summaries', 'seed_mode': 'provided',
                              'added': sum(e['summary'] == 'generated' for e in log.values()),
                              'note': 'Generated summaries replaced or filled provided ones; they are drafts pending review.'})
    result['author_input_conflicts'] = conflicts
    if seed is None:
        result['note'] = ('No seed project: ' + ', '.join(AUXILIARY) + ' are empty by construction. '
                          'Supply --seed to carry author-provided modules; this is a scope limit, not a measured quality.')
    else:
        info = plan['seed']
        result['seed'] = {'path': info['path'], 'language': info['language'], 'policy': info['policy'],
                          'files': len(info['inventory']), 'config': info['config'], 'source': info['source'],
                          'source_matches_delivery': info['source'].get('sha256') == plan['source_sha256'],
                          'author_input_modes': info['author_input_modes'], 'workflow_reset_to_pending': True}
    return result


def draft_config_text(text: str, language: str, book: dict) -> str:
    """Never inherit the seed's publication status or review approvals for new content."""
    lines, section, out = text.splitlines(), None, []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('[') and stripped.endswith(']'):
            section = stripped[1:-1]
        elif section is None:
            if re.match(r'^version\s*=', line):
                version = re.search(r'"([^"]*)"', line)
                if version and not version[1].endswith('-draft'):
                    line = f'version = "{version[1]}-draft"'
            elif re.match(r'^status\s*=', line):
                line = 'status = "draft"'
            elif re.match(r'^languages\s*=', line):
                line = f'languages = ["{language}"]'
            elif re.match(r'^primary_language\s*=', line):
                line = f'primary_language = "{language}"'
        elif section == 'workflow' and re.match(r'^[A-Za-z_]+\s*=', line):
            line = re.sub(r'=.*$', '= "pending"', line, count=1)
        elif section == 'book' and re.match(r'^(title|author)\s*=', line):
            # The delivery language's own book fields become the primary ones.
            key = line.split('=', 1)[0].strip()
            line = f'{key} = {_toml_string(str(book.get(key, "")))}'
        out.append(line)
    return '\n'.join(out) + '\n'
