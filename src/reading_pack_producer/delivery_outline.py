"""Explicit source-bound chapter boundaries, independent of model output."""
from __future__ import annotations

import re
from reading_pack.errors import ReadingPackError


def outline(text: str, fmt: str, title: str, chapter_level: int | None) -> list[dict]:
    if fmt == 'text':
        if chapter_level is not None:
            raise ReadingPackError('--chapter-level applies only to Markdown or Org')
        return [{'id': 'CH-01', 'title': title, 'start': 0, 'end': len(text), 'sections': []}]
    headings, offset, fence = [], 0, None
    for line in text.splitlines(keepends=True):
        match = re.match(r'^ {0,3}(`{3,}|~{3,})', line) if fmt == 'markdown' else None
        if match:
            marker = match[1]
            if fence is None:
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence):
                fence = None
            offset += len(line)
            continue
        pattern = r'^ {0,3}(#{1,6})\s+(.+?)\s*$' if fmt == 'markdown' else r'^(\*{1,8})\s+(.+?)\s*$'
        match = re.match(pattern, line) if fence is None else None
        if match:
            heading = match[2]
            if fmt == 'markdown':
                heading = re.sub(r'\s+#+\s*$', '', heading)
            headings.append({'level': len(match[1]), 'title': heading, 'start': offset})
        offset += len(line)
    if not headings:
        if chapter_level is not None:
            raise ReadingPackError('no headings at the requested chapter level')
        return [{'id': 'CH-01', 'title': title, 'start': 0, 'end': len(text), 'sections': []}]
    minimum = min(h['level'] for h in headings)
    if chapter_level is None:
        if sum(h['level'] == minimum for h in headings) == 1 and any(h['level'] > minimum for h in headings):
            raise ReadingPackError('ambiguous book-title/chapter heading: supply --chapter-level before any model call')
        chapter_level = minimum
    starts = [h for h in headings if h['level'] == chapter_level]
    if not starts:
        raise ReadingPackError('no headings at the requested chapter level')
    # A single parent title is allowed. Other parent headings require an explicit
    # restructured source rather than silently dropping parts/back matter.
    parents = [h for h in headings if h['level'] < chapter_level]
    if any(h['start'] >= starts[0]['start'] for h in parents) or len(parents) > 1:
        raise ReadingPackError('outline contains parent containers; supply a chapter-level source without dropping content')
    result = []
    for i, h in enumerate(starts):
        end = starts[i + 1]['start'] if i + 1 < len(starts) else len(text)
        child = [s for s in headings if h['start'] < s['start'] < end and s['level'] > chapter_level]
        sections = [{**s, 'id': f'S{i+1:02d}-{j+1:02d}',
                     'end': child[j+1]['start'] if j+1 < len(child) else end}
                    for j, s in enumerate(child)]
        result.append({'id': f'CH-{i+1:02d}', 'title': h['title'],
                       'start': 0 if i == 0 else h['start'], 'end': end, 'sections': sections})
    return result
