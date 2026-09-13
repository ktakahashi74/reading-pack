"""Bounded, source-bound recovery of Japanese vertical PDF navigation.

Geometry suggests headings; it does not approve them. The pipeline independently
selects and reviews body candidates before using their section inventory. TOC
wording is retained for reconciliation and is never substituted for body titles.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import math
import re
import unicodedata
from xml.etree import ElementTree as ET

from .errors import ReadingPackError

MAX_XML_BYTES = 100 * 1024 * 1024
MAX_SPANS = 1_000_000
MAX_CANDIDATES = 20_000


def _number(value: str | None, *, positive: bool = False) -> float:
    try:
        number = float(value or '')
    except ValueError as exc:
        raise ReadingPackError('invalid PDF layout coordinate') from exc
    if not math.isfinite(number) or abs(number) > 1_000_000 or (positive and number <= 0):
        raise ReadingPackError('invalid PDF layout coordinate')
    return number


def _identity(text: str) -> int | None:
    if text == '序章':
        return 0
    found = re.fullmatch(r'第(\d{1,3})章(.{1,200})', text)
    return int(found[1]) if found and int(found[1]) > 0 else None


def recover_vertical_layout(xml: bytes, raw_text: str) -> dict:
    from .importers import (
        PDF_VERTICAL_RADICAL_REPAIRS, PDF_VERTICAL_LIGATURE_REPAIRS,
        _pdf_vertical_repair_signature, _repair_pdf_vertical_token,
        _pdf_horizontal_token, reconstruct_pdf_vertical_text,
    )
    if len(xml) > MAX_XML_BYTES or b'<!ENTITY' in xml.upper():
        raise ReadingPackError('PDF layout exceeds limit or declares XML entities')
    # Poppler emits this fixed external DTD name; no DTD is loaded or resolved.
    xml = re.sub(br'<!DOCTYPE pdf2xml SYSTEM [\"\x27]pdf2xml\.dtd[\"\x27]\s*>', b'', xml)
    if b'<!DOCTYPE' in xml.upper():
        raise ReadingPackError('unsupported PDF layout DTD')
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise ReadingPackError('invalid PDF layout XML') from exc
    if root.tag != 'pdf2xml':
        raise ReadingPackError('invalid PDF layout root')
    fonts = {}
    for font in root.iter('fontspec'):
        identity = font.get('id')
        size = _number(font.get('size'), positive=True)
        if identity in fonts and fonts[identity] != size:
            raise ReadingPackError('PDF layout redefines a font size')
        fonts[identity] = size
    pages = root.findall('page')
    if not pages or len(pages) > 20_000:
        raise ReadingPackError('PDF layout page count is outside limits')
    all_spans = list(root.iter('text'))
    if len(all_spans) > MAX_SPANS:
        raise ReadingPackError('PDF layout span count exceeds limit')
    tokens = [''.join(t.itertext()).strip() for t in all_spans]
    nonempty = [t for t in tokens if t]
    repair = _pdf_vertical_repair_signature(nonempty)
    normalized = []
    for index, value in enumerate(nonempty):
        value = _repair_pdf_vertical_token(nonempty, index) if repair else value
        normalized.append(unicodedata.normalize('NFKC', value).translate(PDF_VERTICAL_RADICAL_REPAIRS))
    iterator = iter(normalized)
    spans_by_page = []
    size_counts = Counter()
    for number, page in enumerate(pages, 1):
        if page.get('number') != str(number):
            raise ReadingPackError('PDF layout physical page order is ambiguous')
        width, height = _number(page.get('width'), positive=True), _number(page.get('height'), positive=True)
        spans = []
        for element in page.findall('text'):
            if not ''.join(element.itertext()).strip():
                continue
            try:
                size = fonts[element.get('font')]
            except KeyError as exc:
                raise ReadingPackError('PDF layout references an unknown font') from exc
            text = next(iterator)
            left, top = _number(element.get('left')), _number(element.get('top'))
            if not (-size <= left <= width + size and -size <= top <= height + size):
                raise ReadingPackError('PDF text lies outside page bounds')
            spans.append({'text': text, 'size': size, 'left': left, 'top': top, 'pdf_page': number})
            size_counts[size] += len(text)
        spans_by_page.append(spans)
    if not size_counts:
        raise ReadingPackError('PDF layout contains no text')
    body_size = size_counts.most_common(1)[0][0]

    def join(parts: list[str]) -> str:
        out = []
        for part in parts:
            if out and _pdf_horizontal_token(out[-1]) and _pdf_horizontal_token(part):
                out.append(' ')
            out.append(part)
        text = ''.join(out)
        if repair:
            text = re.sub(r'\u0336\s*\u0336', '――', text).replace('\u0336', '―')
            for corrupted, replacement in PDF_VERTICAL_LIGATURE_REPAIRS:
                text = text.replace(corrupted, replacement)
        return text.strip()

    large_groups = []
    for spans in spans_by_page:
        current = None
        for span in spans:
            if span['size'] >= body_size * 1.3:
                if current is not None and current['size'] == span['size']:
                    current['parts'].append(span['text'])
                else:
                    current = {**span, 'parts': [span['text']]}
                    large_groups.append(current)
            else:
                current = None
    headings = []
    for group in large_groups:
        title = join(group['parts'])
        number = _identity(title)
        if number is not None:
            headings.append({k: v for k, v in group.items() if k not in {'parts', 'text'}} | {'number': number, 'title': title})
    if not headings:
        raise ReadingPackError('PDF layout has no bounded chapter headings')
    opener_size = max(h['size'] for h in headings)
    chapters = [h for h in headings if h['size'] == opener_size]
    numbers = [h['number'] for h in chapters]
    numbered = [n for n in numbers if n]
    if not numbered or len(numbers) != len(set(numbers)) or numbers != sorted(numbers) or numbered != list(range(1, max(numbered) + 1)):
        raise ReadingPackError('PDF chapter openers are duplicated, unordered, or incomplete')
    first_page = chapters[0]['pdf_page']
    toc_groups = []
    current = None
    for spans in spans_by_page[:first_page - 1]:
        for span in spans:
            if span['size'] >= body_size * 1.3:
                if current is not None and current['part'] == 'title':
                    current['title'].append(span['text'])
                else:
                    current = {'title': [span['text']], 'sections': [], 'part': 'title'}
                    toc_groups.append(current)
            elif span['size'] == body_size and current is not None:
                current['part'] = 'sections'
                current['sections'].append(span['text'])
    toc = []
    for group in toc_groups:
        title = join(group['title'])
        number = _identity(title)
        if number is not None:
            sections = [s.strip() for s in join(group['sections']).split('/') if s.strip()]
            if any(len(s) > 500 for s in sections):
                raise ReadingPackError('PDF TOC section boundary is ambiguous')
            toc.append({'number': number, 'title': title, 'sections': sections})
    if toc and [item['number'] for item in toc] != numbers:
        raise ReadingPackError('PDF TOC and body chapter inventories disagree')

    # Retain actual raw-page indexing, including blank pages. Text offsets refer
    # to the exact evidence representation used by the vertical source adapter.
    raw_pages = raw_text.split('\f')
    if len(raw_pages) == len(pages) + 1 and not raw_pages[-1].strip():
        raw_pages.pop()
    if len(raw_pages) != len(pages):
        raise ReadingPackError('PDF layout and raw text page inventories disagree')
    rendered = reconstruct_pdf_vertical_text(raw_text).split('\n')
    page_texts, cursor, offset = [], 0, 0
    for raw_page in raw_pages:
        text = rendered[cursor] if raw_page.strip() else ''
        if raw_page.strip():
            cursor += 1
        page_texts.append({'text': text, 'start': offset})
        if text:
            offset += len(text) + 1
    if cursor != len(rendered):
        raise ReadingPackError('PDF evidence page reconstruction changed')

    columns_by_page = []
    gaps = Counter()
    tops = Counter()
    for spans in spans_by_page:
        columns = []
        for span in spans:
            if span['size'] != body_size:
                continue
            if columns and abs(span['left'] - columns[-1]['left']) < body_size * .75 and span['top'] >= columns[-1]['end'] - body_size * .5:
                columns[-1]['parts'].append(span['text'])
                columns[-1]['end'] = span['top']
            else:
                columns.append({**span, 'parts': [span['text']], 'end': span['top']})
        for index, col in enumerate(columns):
            col['text'] = join(col.pop('parts'))
            col['before'] = columns[index - 1]['left'] - col['left'] if index else None
            col['after'] = col['left'] - columns[index + 1]['left'] if index + 1 < len(columns) else None
            if col['after'] and body_size <= col['after'] <= body_size * 2:
                gaps[round(col['after'])] += 1
            tops[round(col['top'])] += 1
        columns_by_page.append(columns)
    pitch = gaps.most_common(1)[0][0] if gaps else body_size * 1.6
    baseline = tops.most_common(1)[0][0] if tops else 0
    pool = []
    for page_index, columns in enumerate(columns_by_page, 1):
        if page_index < first_page:
            continue
        for index, col in enumerate(columns):
            boundary = col['before'] is None or col['before'] < 0 or col['before'] >= pitch * 1.6
            if not boundary or not baseline - body_size * .5 <= col['top'] <= baseline + body_size * .6:
                continue
            parts = []
            for end in range(index, min(index + 4, len(columns))):
                if end > index and not (0 < columns[end]['before'] <= pitch * 1.6):
                    break
                parts.append(columns[end]['text'])
                title = ''.join(parts)
                if len(title) > 200 or '。' in title:
                    break
                after = columns[end]['after']
                if after is None or after <= 0 or after >= pitch * 1.6:
                    if len(title) >= 2:
                        context = ''.join(c['text'] for c in columns[max(0, index - 1):min(len(columns), end + 4)])
                        pool.append({'title': title, 'pdf_page': page_index, 'left': col['left'], 'top': col['top'], 'context': context[:1000]})
                    break
    if len(pool) > MAX_CANDIDATES:
        raise ReadingPackError('PDF heading candidate count exceeds limit')
    for index, chapter in enumerate(chapters):
        chapter['id'] = 'CH-INTRO' if chapter['number'] == 0 else f'CH-{chapter["number"]:02d}'
        next_page = chapters[index + 1]['pdf_page'] if index + 1 < len(chapters) else len(pages) + 1
        chapter['toc_title'] = toc[index]['title'] if toc else None
        chapter['toc_sections'] = toc[index]['sections'] if toc else []
        chapter['source_start'] = page_texts[chapter['pdf_page'] - 1]['start']
        chapter['source_text'] = '\n'.join(p['text'] for p in page_texts[chapter['pdf_page'] - 1:next_page - 1] if p['text'])
        chapter['body_pages'] = [dict(p, pdf_page=i + 1)
            for i, p in enumerate(page_texts) if chapter['pdf_page'] <= i + 1 < next_page]
        chapter['candidates'] = [c for c in pool if chapter['pdf_page'] <= c['pdf_page'] < next_page]
        for order, candidate in enumerate(chapter['candidates']):
            candidate['order'] = order
            candidate['id'] = 'LH-' + hashlib.sha256(f'{chapter["id"]}/{order}/{candidate["title"]}'.encode()).hexdigest()[:20]
            page = page_texts[candidate['pdf_page'] - 1]
            compact = []
            positions = []
            for position, character in enumerate(page['text']):
                if not character.isspace():
                    folded = character.casefold()
                    compact.extend(folded)
                    positions.extend([position] * len(folded))
            needle = re.sub(r'\s+', '', candidate['title']).casefold()
            found = ''.join(compact).find(needle)
            candidate['exact_body_text_match'] = found >= 0
            candidate['source_offset'] = page['start'] + (positions[found] if found >= 0 else 0)
    return {'schema_version': 1, 'method': 'pdf-vertical-layout-v1', 'body_font_size': body_size,
            'opener_font_size': opener_size, 'physical_pages': len(pages), 'chapters': chapters,
            'requires_independent_review': True}


def supplement_heading_candidates(chapter: dict, proposals: list[dict], source_sha256: str) -> list[dict]:
    """Bind proposed omissions to unique text positions, without adopting them."""
    pages = {p['pdf_page']: p for p in chapter.get('body_pages', [])}
    additions = []
    occupied = {c['source_offset'] for c in chapter['candidates']}

    def compact(text: str) -> tuple[str, list[int]]:
        positions = [i for i, character in enumerate(text) if not character.isspace()]
        return ''.join(text[i] for i in positions), positions

    for proposal in proposals:
        page = pages.get(proposal['pdf_page'])
        title, _ = compact(proposal['title'])
        quote, _ = compact(proposal['evidence_quote'])
        if page is None or not 2 <= len(title) <= 200 or title not in quote or len(quote) > 500:
            raise ReadingPackError('missing heading has invalid page or evidence')
        text, positions = compact(page['text'])
        if not quote or text.count(quote) != 1 or quote.count(title) != 1:
            raise ReadingPackError('missing heading evidence is absent or ambiguous in its body page')
        offset = text.index(quote) + quote.index(title)
        start, end = positions[offset], positions[offset + len(title) - 1] + 1
        source_offset = page['start'] + start
        if source_offset in occupied:
            continue
        canonical_title = ' '.join(page['text'][start:end].split())
        if len(canonical_title) > 200 or any(ord(c) < 32 for c in canonical_title):
            raise ReadingPackError('missing heading title exceeds candidate bounds')
        identifier = hashlib.sha256(f'{source_sha256}/{chapter["id"]}/{source_offset}/{canonical_title}'.encode()).hexdigest()[:20]
        additions.append({'id': 'LH-' + identifier, 'title': canonical_title,
            'pdf_page': page['pdf_page'], 'source_offset': source_offset,
            'context': page['text'][max(0, start - 200):end + 300],
            'exact_body_text_match': True, 'method': 'source-grounded-supplement-v1'})
        occupied.add(source_offset)
    return additions


def extracted_layout_book(layout: dict, title: str):
    from .importers import ExtractedBook
    records = [{'id': c['id'], 'title': c['title'], 'pages': '', 'sections': [],
                'summary': '', 'terms': [], 'status': 'draft'} for c in layout['chapters']]
    return ExtractedBook(title, records, 'pdf-vertical', layout=layout)
