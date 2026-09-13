"""Bind grader quotations to exact answer text, allowing paired bold markup."""
from __future__ import annotations

import re
from reading_pack.errors import ReadingPackError


def _plain_bold(text: str) -> tuple[str, list[int]]:
    removed = set()
    code = [(m.start(), m.end()) for m in re.finditer(r'(`+)[\s\S]*?\1', text)]
    # Only balanced inline strong emphasis. Do not strip punctuation, numbers,
    # whitespace, code, escapes, unmatched markers, or arbitrary Markdown.
    for match in re.finditer(r'(?<![\\*])\*\*(?=\S)([^\n`*]+?\S|[^\n`*])\*\*(?!\*)', text):
        if any(left < match.end() and match.start() < right for left, right in code):
            continue
        removed.update((match.start(), match.start()+1, match.end()-2, match.end()-1))
    positions = [i for i in range(len(text)) if i not in removed]
    return ''.join(text[i] for i in positions), positions


def answer_quote_bindings(answer: str, quotes: list[str]) -> list[dict]:
    """Return receipts only for formatting restoration; invented prose fails."""
    plain, positions = _plain_bold(answer)
    bindings = []
    for quote in quotes:
        if quote and quote in answer:
            continue
        projected, _ = _plain_bold(quote)
        start = plain.find(projected) if projected else -1
        if start < 0:
            raise ReadingPackError('grader fabricated an answer quotation')
        left, right = positions[start], positions[start + len(projected) - 1] + 1
        exact = answer[left:right]
        bindings.append({'reported_quote': quote, 'verbatim_quote': exact,
                         'start': left, 'end': right, 'method': 'paired-bold-markup-only'})
    return bindings
