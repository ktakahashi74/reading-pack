"""Offline, structure-only importers for manuscripts, EPUB3, and PDF files."""

from __future__ import annotations

import html
import os
import posixpath
import re
import selectors
import shutil
import stat
import subprocess
import time
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable
from urllib.parse import unquote, urlparse
from xml.etree import ElementTree as ET

from .errors import EXIT_IO, ReadingPackError
from .hashing import file_hash, semantic_hash
from .project import load_config, load_language_data, project_lock, write_json

MAX_ENTRY_BYTES = 10 * 1024 * 1024
MAX_ARCHIVE_BYTES = 50 * 1024 * 1024
MAX_SOURCE_BYTES = 100 * 1024 * 1024
MAX_EPUB_MEMBERS = 10_000
MAX_EPUB_SPINE_ITEMS = 10_000
MAX_EPUB_TEXT_CHARACTERS = 50 * 1024 * 1024
MAX_COMPRESSION_RATIO = 1000
MAX_PDF_BYTES = 100 * 1024 * 1024
MAX_PDF_TEXT_BYTES = 50 * 1024 * 1024
MAX_PDF_INFO_BYTES = 1024 * 1024
MAX_PDF_ERROR_BYTES = 64 * 1024
MAX_PDF_PAGES = 20_000
MAX_PDF_HEADING_CHARACTERS = 500
PDF_TOOL_TIMEOUT_SECONDS = 120

# Some Japanese publishing PDFs expose vertical text as one glyph per Poppler
# ``-raw`` line. A subset-font encoding seen in production files also maps a
# small, consistent set of kana and punctuation glyphs to Latin characters.
# Repairs are applied only after a corpus-level Japanese/vertical signature
# check; ordinary horizontal PDFs never enter this path. A handful of
# multi-glyph aliases below are likewise exact signatures observed in that
# subset font, including page-number interleaving inside ligatures.
PDF_VERTICAL_GLYPH_REPAIRS = {
    "ò": "ー",
    "D": "っ",
    "n": "ァ",
    "û": "ッ",
    "º": "ィ",
    "}": "ェ",
    "q": "ュ",
    "k": "ォ",
    "K": "ャ",
    "â": "ゥ",
    "ë": "ゃ",
    "g": "ょ",
    "Ç": "：",
    "´": "≒",
    "Ÿ": "ョ",
    "‡": "か",
    "\u0336": "―",
    "―\u0336": "――",
    "\u0336\u0336": "――",
}
PDF_VERTICAL_RADICAL_REPAIRS = str.maketrans(
    {
        "⺠": "民",
        "⻄": "西",
        "⻑": "長",
        "⻤": "鬼",
        "⻩": "黄",
        "⻭": "歯",
        "⻲": "亀",
    }
)
PDF_VERTICAL_LIGATURE_REPAIRS = (
    ("フ204ェーズ", "フェーズ"),
    ("フ204}ーズ", "フェーズ"),
    ("図D)", "図)"),
    ("20330年", "20〜30年"),
    ("200532009年", "2005〜2009年"),
)


@dataclass(frozen=True)
class ExtractedBook:
    title: str
    chapters: list[dict]
    source_format: str


def read_regular_source_bytes(path: Path, *, maximum: int = MAX_SOURCE_BYTES) -> bytes:
    """Read a regular source through a no-follow descriptor with a hard cap."""

    path = path.resolve()
    descriptor: int | None = None
    try:
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ReadingPackError(f"source is not a regular file: {path}", EXIT_IO)
        if before.st_size > maximum:
            raise ReadingPackError(f"source exceeds {maximum} bytes: {path}", EXIT_IO)
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, maximum + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > maximum:
                raise ReadingPackError(f"source exceeds {maximum} bytes: {path}", EXIT_IO)
        after = os.fstat(descriptor)
        path_after = path.stat()
    except OSError as exc:
        raise ReadingPackError(f"cannot read source {path}: {exc}", EXIT_IO) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    path_identity = (
        path_after.st_dev,
        path_after.st_ino,
        path_after.st_size,
        path_after.st_mtime_ns,
    )
    if identity_before != identity_after or identity_after != path_identity:
        raise ReadingPackError("source changed while it was being read", EXIT_IO)
    return b"".join(chunks)


def _clean_heading(value: str) -> str:
    value = re.sub(r"\s+#+\s*$", "", value)
    value = re.sub(r"\s+:[\w@#%:.-]+:\s*$", "", value)
    value = re.sub(r"\{#[-\w]+\}\s*$", "", value)
    return html.unescape(re.sub(r"\s+", " ", value)).strip()


def _chapters_from_headings(
    headings: Iterable[tuple[int, str]], *, skip_first_heading: bool = False
) -> list[dict]:
    raw = [(level, _clean_heading(text)) for level, text in headings if _clean_heading(text)]
    if skip_first_heading and raw:
        raw = raw[1:]
    if not raw:
        return []
    chapter_level = min(level for level, _ in raw)
    chapters: list[dict] = []
    current: dict | None = None
    for level, heading in raw:
        if level == chapter_level:
            current = {
                "id": f"CH-{len(chapters) + 1:02d}",
                "title": heading,
                "pages": "",
                "sections": [],
                "summary": "",
                "terms": [],
                "status": "draft",
            }
            chapters.append(current)
        elif level > chapter_level and current is not None:
            current["sections"].append(heading)
    return chapters


def _implicit_heading_title(headings: list[tuple[int, str]]) -> tuple[str, bool]:
    """Infer a document title only when one root heading wraps the document."""

    cleaned = [
        (level, _clean_heading(text))
        for level, text in headings
        if _clean_heading(text)
    ]
    if not cleaned:
        return "Untitled book", False
    root_level = min(level for level, _ in cleaned)
    roots = [(level, text) for level, text in cleaned if level == root_level]
    has_children = any(level > root_level for level, _ in cleaned)
    if len(roots) == 1 and cleaned[0][0] == root_level:
        return roots[0][1], has_children
    return "Untitled book", False


def extract_markdown(text: str) -> ExtractedBook:
    headings: list[tuple[int, str]] = []
    in_fence = False
    for line in text.splitlines():
        if re.match(r"^\s*(```|~~~)", line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if match:
            headings.append((len(match.group(1)), match.group(2)))
    title, skip_first_heading = _implicit_heading_title(headings)
    return ExtractedBook(
        title,
        _chapters_from_headings(headings, skip_first_heading=skip_first_heading),
        "markdown",
    )


def extract_org(text: str) -> ExtractedBook:
    if re.search(r"^[ \t]*#\+INCLUDE\s*:", text, re.IGNORECASE | re.MULTILINE):
        raise ReadingPackError(
            "Org source contains #+INCLUDE; resolve includes first and import "
            "one self-contained file"
        )
    title_match = re.search(r"^#\+TITLE:\s*(.+)$", text, re.IGNORECASE | re.MULTILINE)
    headings = []
    for line in text.splitlines():
        match = re.match(r"^(\*{1,8})\s+(?:TODO\s+|DONE\s+)?(.+?)\s*$", line)
        if match:
            headings.append((len(match.group(1)), match.group(2)))
    if title_match:
        title = _clean_heading(title_match.group(1))
        skip_first_heading = False
    else:
        title, skip_first_heading = _implicit_heading_title(headings)
    return ExtractedBook(
        title,
        _chapters_from_headings(headings, skip_first_heading=skip_first_heading),
        "org",
    )


def extract_text(text: str) -> ExtractedBook:
    nonempty = [line.strip() for line in text.splitlines() if line.strip()]
    title = nonempty[0] if nonempty else "Untitled book"
    pattern = re.compile(r"^(?:chapter\s+\d+|第[^\s]{1,8}章|part\s+\d+)\b[:：\s-]*(.*)$", re.IGNORECASE)
    headings = []
    for line in nonempty[1:]:
        match = pattern.match(line)
        if match:
            headings.append((1, line))
    if not headings and len(nonempty) > 1:
        headings = [(1, nonempty[1])]
    return ExtractedBook(title, _chapters_from_headings(headings), "text")


def _pdf_tool(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise ReadingPackError(
            f"PDF import requires the local Poppler tool {name!r}; install Poppler and try again",
            EXIT_IO,
        )
    return path


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        process.kill()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _run_pdf_tool(arguments: list[str], label: str, *, max_stdout: int) -> bytes:
    """Run a fixed local PDF tool while hard-limiting time and captured output."""

    try:
        process = subprocess.Popen(
            arguments,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, "LC_ALL": "C", "LANG": "C"},
        )
    except OSError as exc:
        raise ReadingPackError(f"{label} failed: {exc}", EXIT_IO) from exc

    assert process.stdout is not None
    assert process.stderr is not None
    captured = {"stdout": bytearray(), "stderr": bytearray()}
    limits = {"stdout": max_stdout, "stderr": MAX_PDF_ERROR_BYTES}
    streams = selectors.DefaultSelector()
    streams.register(process.stdout, selectors.EVENT_READ, "stdout")
    streams.register(process.stderr, selectors.EVENT_READ, "stderr")
    deadline = time.monotonic() + PDF_TOOL_TIMEOUT_SECONDS
    try:
        while streams.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _stop_process(process)
                raise ReadingPackError(
                    f"{label} timed out after {PDF_TOOL_TIMEOUT_SECONDS} seconds", EXIT_IO
                )
            for key, _ in streams.select(timeout=min(remaining, 0.25)):
                chunk = os.read(key.fileobj.fileno(), 64 * 1024)
                if not chunk:
                    streams.unregister(key.fileobj)
                    key.fileobj.close()
                    continue
                target = captured[key.data]
                if len(target) + len(chunk) > limits[key.data]:
                    _stop_process(process)
                    raise ReadingPackError(
                        f"{label} {key.data} exceeds {limits[key.data]} bytes", EXIT_IO
                    )
                target.extend(chunk)
        remaining = max(0.01, deadline - time.monotonic())
        try:
            returncode = process.wait(timeout=remaining)
        except subprocess.TimeoutExpired as exc:
            _stop_process(process)
            raise ReadingPackError(
                f"{label} timed out after {PDF_TOOL_TIMEOUT_SECONDS} seconds", EXIT_IO
            ) from exc
    finally:
        streams.close()
        if process.poll() is None:
            _stop_process(process)
        process.stdout.close()
        process.stderr.close()

    if returncode != 0:
        detail = captured["stderr"].decode("utf-8", errors="replace").strip()
        detail = re.sub(r"[\x00-\x1f\x7f]+", " ", detail)[:500]
        detail = detail or f"exit status {returncode}"
        raise ReadingPackError(f"{label} failed: {detail}", EXIT_IO)
    return bytes(captured["stdout"])


def _pdf_line(value: str) -> str:
    """Normalize Poppler raw text while preserving ordinary word spacing."""

    value = unicodedata.normalize("NFKC", value).strip()
    tokens = value.split()
    if len(tokens) >= 3 and sum(len(token) == 1 for token in tokens) / len(tokens) >= 0.7:
        return "".join(tokens)
    return re.sub(r"\s+", " ", value)


def _pdf_heading(value: str) -> str:
    value = _pdf_line(value)
    value = re.sub(r"[\x00-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]", "", value)
    if len(value) > MAX_PDF_HEADING_CHARACTERS:
        return ""
    value = re.sub(r"[─━—-]{3,}.*$", "", value)
    value = re.sub(r"(?:\.{3,}|…{2,}).*$", "", value)
    value = re.sub(r"\s+\d{1,4}$", "", value)
    value = value.strip(" \t│|─━—-:：")
    value = re.sub(r"[─━—-]{1,2}", "――", value)
    value = _clean_heading(value)
    return value if re.search(r"\w", value, re.UNICODE) else ""


def _standalone_page(value: str) -> int | None:
    match = re.fullmatch(r"(\d{1,4})", value)
    if not match:
        return None
    page = int(match.group(1))
    return page if page > 0 else None


def _nearby_page(lines: list[str], start: int, *, distance: int = 9) -> int | None:
    for value in lines[start : start + distance]:
        page = _standalone_page(value)
        if page is not None:
            return page
    return None


def _chapter_at(lines: list[str], index: int) -> tuple[int, str, int] | None:
    """Return chapter number, title, and the next unread line index."""

    line = lines[index]
    match = re.match(r"^第\s*(\d{1,3})\s*章\s*(.+)$", line)
    if not match:
        match = re.match(r"^Chapter\s*(\d{1,3})\s*[:.\-]?\s*(.+)$", line, re.IGNORECASE)
    if match:
        title = _pdf_heading(match.group(2))
        if title:
            return int(match.group(1)), title, index + 1

    # Multi-column PDFs often emit "第", "10", and "章 Title" as separate lines.
    if line in {"第", "Chapter"} and index + 2 < len(lines):
        number = re.fullmatch(r"\d{1,3}", lines[index + 1])
        marker = lines[index + 2]
        if number and (marker.startswith("章") or line == "Chapter"):
            title_parts: list[str] = []
            remainder = marker[1:] if marker.startswith("章") else marker
            if _pdf_heading(remainder):
                title_parts.append(_pdf_heading(remainder))
            cursor = index + 3
            while not title_parts and cursor < min(len(lines), index + 7):
                candidate = _pdf_heading(lines[cursor])
                if candidate and _standalone_page(candidate) is None:
                    title_parts.append(candidate)
                cursor += 1
            # A split subtitle can immediately follow the first title line.
            if cursor < min(len(lines), index + 8):
                raw_candidate = lines[cursor]
                candidate = _pdf_heading(raw_candidate)
                if candidate and raw_candidate.startswith(("─", "━", "—", "-", "│")):
                    title_parts.append(f"――{candidate}")
                    cursor += 1
            title = _pdf_heading("".join(title_parts))
            if title:
                return int(number.group()), title, cursor

    # Some layout-preserving extractions invert the number and marker.
    match = re.match(r"^第章\s*(\d{1,3})\s*(.+)$", line)
    if match and _pdf_heading(match.group(2)):
        return int(match.group(1)), _pdf_heading(match.group(2)), index + 1
    return None


def _section_at(lines: list[str], index: int) -> tuple[int, int, str] | None:
    line = lines[index]
    patterns = (
        r"^(\d{1,3})[・.]\s*(\d{1,3})\s*(.+)$",
        r"^(\d{1,3})\s+(\d{1,3})\s+(.+)$",
    )
    for pattern in patterns:
        match = re.match(pattern, line)
        if match and _pdf_heading(match.group(3)):
            return int(match.group(1)), int(match.group(2)), _pdf_heading(match.group(3))

    chapter = re.fullmatch(r"\d{1,3}", line)
    if not chapter or index + 1 >= len(lines):
        return None
    marker = lines[index + 1]
    same_line = re.match(r"^・\s*(\d{1,3})\s*(.+)$", marker)
    if same_line and _pdf_heading(same_line.group(2)):
        return int(chapter.group()), int(same_line.group(1)), _pdf_heading(same_line.group(2))
    if marker != "・" or index + 3 >= len(lines):
        return None
    section = re.fullmatch(r"\d{1,3}", lines[index + 2])
    title = _pdf_heading(lines[index + 3])
    if section and title and _standalone_page(title) is None:
        return int(chapter.group()), int(section.group()), title
    return None


def _special_page(lines: list[str], label: str) -> int | None:
    candidates: list[int] = []
    for index, line in enumerate(lines):
        if line == label:
            page = _nearby_page(lines, index + 1, distance=1)
            if page is not None:
                candidates.append(page)
    return min(candidates, default=None)


def extract_pdf_text(text: str, *, metadata_title: str = "") -> ExtractedBook:
    """Conservatively recover a PDF's numbered TOC structure from Poppler raw text."""

    lines = [_pdf_line(line) for line in text.splitlines() if _pdf_line(line)]
    titles: dict[int, str] = {}
    starts: dict[int, int] = {}
    sections: dict[int, dict[int, str]] = {}
    for index in range(len(lines)):
        chapter = _chapter_at(lines, index)
        if chapter:
            number, title, following = chapter
            if number not in titles:
                titles[number] = title
                page = _nearby_page(lines, following)
                if page is not None:
                    starts[number] = page
        section = _section_at(lines, index)
        if section:
            chapter_number, section_number, title = section
            sections.setdefault(chapter_number, {}).setdefault(section_number, title)

    chapters: list[dict] = []
    preface_start = _special_page(lines, "まえがき")
    if preface_start is not None:
        chapters.append(
            {
                "id": "CH-PREFACE",
                "title": "まえがき",
                "pages": str(preface_start),
                "sections": [],
                "summary": "",
                "terms": [],
                "status": "draft",
            }
        )

    ordered = sorted(titles)
    afterword_start = _special_page(lines, "あとがき")
    for position, number in enumerate(ordered):
        page = starts.get(number)
        next_page = starts.get(ordered[position + 1]) if position + 1 < len(ordered) else afterword_start
        pages = ""
        if page is not None:
            pages = str(page) if next_page is None or next_page <= page else f"{page}-{next_page - 1}"
        chapters.append(
            {
                "id": f"CH-{number:02d}",
                "title": titles[number],
                "pages": pages,
                "sections": [title for _, title in sorted(sections.get(number, {}).items())],
                "summary": "",
                "terms": [],
                "status": "draft",
            }
        )

    if afterword_start is not None:
        notes_start = _special_page(lines, "注")
        pages = str(afterword_start)
        if notes_start is not None and notes_start > afterword_start:
            pages = f"{afterword_start}-{notes_start - 1}"
        chapters.append(
            {
                "id": "CH-AFTERWORD",
                "title": "あとがき",
                "pages": pages,
                "sections": [],
                "summary": "",
                "terms": [],
                "status": "draft",
            }
        )
    return ExtractedBook(metadata_title, chapters, "pdf")


def _is_japanese_character(character: str) -> bool:
    character = unicodedata.normalize("NFKC", character)
    if len(character) != 1:
        return False
    codepoint = ord(character)
    return (
        0x3040 <= codepoint <= 0x30FF
        or 0x3400 <= codepoint <= 0x4DBF
        or 0x4E00 <= codepoint <= 0x9FFF
        or 0xF900 <= codepoint <= 0xFAFF
    )


def _pdf_vertical_repair_signature(lines: list[str]) -> bool:
    """Recognize a subset-font failure before applying ambiguous repairs."""

    if not lines:
        return False
    single = sum(len(value) == 1 for value in lines)
    japanese = sum(
        1 for value in lines if len(value) == 1 and _is_japanese_character(value)
    )
    sentinel_values = {"ò", "û", "º", "}", "q", "Ÿ", "‡"}
    sentinels = {value for value in lines if value in sentinel_values}
    sentinel_count = sum(value in sentinel_values for value in lines)
    return (
        single / len(lines) >= 0.70
        and japanese / max(single, 1) >= 0.40
        and len(sentinels) >= 3
        and sentinel_count >= 20
    )


def _vertical_kana_neighbor(value: str, *, hiragana_only: bool = False) -> bool:
    if len(value) != 1:
        return False
    value = PDF_VERTICAL_GLYPH_REPAIRS.get(value, value)
    codepoint = ord(value)
    if 0x3040 <= codepoint <= 0x309F:
        return True
    return not hiragana_only and (0x30A0 <= codepoint <= 0x30FF or value == "ー")


def _vertical_neighbor(tokens: list[str], index: int, direction: int) -> str:
    """Return the adjacent glyph, ignoring an interleaved printed page number."""

    cursor = index + direction
    while 0 <= cursor < len(tokens):
        value = tokens[cursor]
        if value.isascii() and value.isdigit():
            cursor += direction
            continue
        return value
    return ""


def _vertical_neighbor_character(tokens: list[str], index: int, direction: int) -> str:
    value = _vertical_neighbor(tokens, index, direction)
    value = PDF_VERTICAL_GLYPH_REPAIRS.get(value, value)
    value = unicodedata.normalize("NFKC", value).strip()
    return (value[-1:] if direction < 0 else value[:1])


def _repair_pdf_vertical_token(tokens: list[str], index: int) -> str:
    token = tokens[index]
    replacement = PDF_VERTICAL_GLYPH_REPAIRS.get(token)
    if replacement is not None:
        # ASCII subset-font aliases are ambiguous in formulas and foreign text.
        # Repair them only inside a kana run; accented sentinels are unambiguous
        # after the document-level signature has passed.
        if token == "D":
            before = _vertical_neighbor_character(tokens, index, -1)
            after = _vertical_neighbor_character(tokens, index, 1)
            if not (
                len(before) == 1
                and _is_japanese_character(before)
                and len(after) == 1
                and _is_japanese_character(after)
            ):
                return token
        elif token == "g":
            before = tokens[index - 1] if index else ""
            after = tokens[index + 1] if index + 1 < len(tokens) else ""
            if not (
                _vertical_kana_neighbor(before, hiragana_only=True)
                and _vertical_kana_neighbor(after, hiragana_only=True)
            ):
                return token
        elif token == "n":
            before = _vertical_neighbor_character(tokens, index, -1)
            after = _vertical_neighbor_character(tokens, index, 1)
            if not (
                _vertical_kana_neighbor(before)
                and _vertical_kana_neighbor(after)
            ):
                return token
        elif token == "K":
            before = tokens[index - 1] if index else ""
            after = tokens[index + 1] if index + 1 < len(tokens) else ""
            if not (
                _vertical_kana_neighbor(before)
                and (
                    _vertical_kana_neighbor(after)
                    or (len(after) == 1 and _is_japanese_character(after))
                    or (after and unicodedata.category(after[0]).startswith("P"))
                )
            ):
                return token
        elif token == "}":
            before = _vertical_neighbor_character(tokens, index, -1)
            after = _vertical_neighbor_character(tokens, index, 1)
            if not (
                _vertical_kana_neighbor(before)
                and (
                    _vertical_kana_neighbor(after)
                    or (after and unicodedata.category(after[0]).startswith("P"))
                )
            ):
                return token
        elif token in {"q", "k"}:
            before = tokens[index - 1] if index else ""
            after = tokens[index + 1] if index + 1 < len(tokens) else ""
            if not (
                _vertical_kana_neighbor(before)
                and _vertical_kana_neighbor(after)
            ):
                return token
        return replacement

    # Poppler occasionally groups the colon alias with vertical brackets. Only
    # replace it when every other non-space character is Unicode punctuation.
    if "Ç" in token and all(
        character == "Ç"
        or character.isspace()
        or unicodedata.category(character).startswith(("P", "M"))
        for character in token
    ):
        return token.replace("Ç", "：")
    return token


def reconstruct_pdf_vertical_text(text: str) -> str:
    """Reconstruct stable Japanese vertical text from Poppler ``-raw`` output.

    Poppler normally orders a two-page Japanese spread from right to left but
    emits one glyph per line. Tokens are joined inside each physical PDF page;
    form-feed boundaries become newlines. This is a text-layer adapter, not
    OCR, so import plans produced from it remain subject to human review.
    """

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    repair_subset_font = _pdf_vertical_repair_signature(lines)
    pages: list[str] = []
    for raw_page in text.split("\f"):
        raw_tokens = [line.strip() for line in raw_page.splitlines() if line.strip()]
        tokens: list[str] = []
        for index, token in enumerate(raw_tokens):
            if repair_subset_font:
                token = _repair_pdf_vertical_token(raw_tokens, index)
            normalized_token = unicodedata.normalize("NFKC", token)
            if tokens and _pdf_horizontal_token(tokens[-1]) and _pdf_horizontal_token(
                normalized_token
            ):
                tokens.append(" ")
            tokens.append(normalized_token)
        page = "".join(tokens).translate(PDF_VERTICAL_RADICAL_REPAIRS)
        if repair_subset_font:
            page = re.sub(r"\u0336\s*\u0336", "――", page).replace("\u0336", "―")
            for corrupted, replacement in PDF_VERTICAL_LIGATURE_REPAIRS:
                page = page.replace(corrupted, replacement)
        if page:
            pages.append(page)
    return "\n".join(pages)


def _pdf_horizontal_token(value: str) -> bool:
    """Identify multi-character horizontal ASCII lines inside a vertical page."""

    return (
        len(value) > 1
        and any(character.isascii() and character.isalpha() for character in value)
        and all(character.isascii() for character in value)
    )


def _read_pdf_text(path: Path) -> tuple[str, str]:
    """Return trusted local PDF metadata title and extracted UTF-8 body text."""

    path = path.resolve()
    try:
        source_stat = path.stat()
    except OSError as exc:
        raise ReadingPackError(f"cannot read PDF {path}: {exc}", EXIT_IO) from exc
    if not stat.S_ISREG(source_stat.st_mode):
        raise ReadingPackError(f"PDF source is not a regular file: {path}", EXIT_IO)
    if source_stat.st_size > MAX_PDF_BYTES:
        raise ReadingPackError(f"PDF exceeds {MAX_PDF_BYTES} bytes", EXIT_IO)

    pdfinfo = _pdf_tool("pdfinfo")
    pdftotext = _pdf_tool("pdftotext")
    info = _run_pdf_tool(
        [pdfinfo, str(path)], "pdfinfo", max_stdout=MAX_PDF_INFO_BYTES
    )
    info_text = info.decode("utf-8", errors="replace")
    encrypted = re.search(r"^Encrypted:\s*(\S+)", info_text, re.MULTILINE | re.IGNORECASE)
    if encrypted is None:
        raise ReadingPackError("pdfinfo did not report PDF encryption state", EXIT_IO)
    if encrypted.group(1).lower() not in {"no", "false", "0"}:
        raise ReadingPackError("encrypted or password-protected PDF files are not supported")
    pages_match = re.search(r"^Pages:\s*(\d+)\s*$", info_text, re.MULTILINE | re.IGNORECASE)
    if pages_match is None:
        raise ReadingPackError("pdfinfo did not report a valid page count", EXIT_IO)
    pages = int(pages_match.group(1))
    if pages < 1 or pages > MAX_PDF_PAGES:
        raise ReadingPackError(f"PDF page count is outside the supported range: {pages}", EXIT_IO)
    title_match = re.search(r"^Title:\s*(.*?)\s*$", info_text, re.MULTILINE)
    title = _clean_heading(title_match.group(1)) if title_match else ""
    if (
        len(title) > MAX_PDF_HEADING_CHARACTERS
        or re.search(r"[\x00-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]", title)
        or re.search(r"\.(?:pdf|indd)$", title, re.IGNORECASE)
        or title.lower() in {"untitled", "none"}
    ):
        title = ""

    raw = _run_pdf_tool(
        [pdftotext, "-raw", "-enc", "UTF-8", str(path), "-"],
        "pdftotext",
        max_stdout=MAX_PDF_TEXT_BYTES,
    )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReadingPackError("pdftotext did not return valid UTF-8", EXIT_IO) from exc
    return title, text


def extract_pdf_authorized_text(path: Path, *, vertical: bool = False) -> str:
    """Extract PDF text with the same bounded, encryption-aware path as import."""

    text = _read_pdf_text(path)[1]
    return reconstruct_pdf_vertical_text(text) if vertical else text


def extract_pdf(path: Path) -> ExtractedBook:
    title, text = _read_pdf_text(path)
    return extract_pdf_text(text, metadata_title=title)


def extract_pdf_vertical(path: Path) -> ExtractedBook:
    """Extract conservative structure while selecting vertical evidence text."""

    title, text = _read_pdf_text(path)
    extracted = extract_pdf_text(
        reconstruct_pdf_vertical_text(text), metadata_title=title
    )
    return ExtractedBook(extracted.title, extracted.chapters, "pdf-vertical")


def _safe_member_name(name: str) -> None:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name:
        raise ReadingPackError(f"unsafe EPUB member path: {name}")


def _read_zip_member(archive: zipfile.ZipFile, name: str) -> bytes:
    _safe_member_name(name)
    try:
        info = archive.getinfo(name)
    except KeyError as exc:
        raise ReadingPackError(f"EPUB member is missing: {name}") from exc
    if info.file_size > MAX_ENTRY_BYTES:
        raise ReadingPackError(f"EPUB member exceeds {MAX_ENTRY_BYTES} bytes: {name}")
    if info.compress_size and info.file_size / info.compress_size > MAX_COMPRESSION_RATIO:
        raise ReadingPackError(f"unsafe EPUB compression ratio: {name}")
    return archive.read(info)


def _xml(data: bytes, label: str) -> ET.Element:
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise ReadingPackError(f"unsafe XML declaration in EPUB: {label}")
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise ReadingPackError(f"invalid XML in EPUB ({label}): {exc}") from exc


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _element_text(element: ET.Element) -> str:
    return _clean_heading(" ".join("".join(element.itertext()).split()))


def _resolve_epub_path(base: str, href: str) -> str:
    parsed = urlparse(unquote(href))
    if parsed.scheme or parsed.netloc:
        raise ReadingPackError(f"external EPUB spine reference is not allowed: {href}")
    joined = posixpath.normpath(posixpath.join(posixpath.dirname(base), parsed.path))
    _safe_member_name(joined)
    return joined


def _read_epub(path: Path) -> tuple[ExtractedBook, str]:
    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise ReadingPackError(f"invalid EPUB ZIP: {exc}", EXIT_IO) from exc
    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_EPUB_MEMBERS:
            raise ReadingPackError(f"EPUB contains more than {MAX_EPUB_MEMBERS} members")
        names = [info.filename for info in infos]
        if len(names) != len(set(names)):
            raise ReadingPackError("EPUB contains duplicate member names")
        for info in infos:
            _safe_member_name(info.filename)
        if sum(info.file_size for info in infos) > MAX_ARCHIVE_BYTES:
            raise ReadingPackError(f"expanded EPUB exceeds {MAX_ARCHIVE_BYTES} bytes")
        if "META-INF/encryption.xml" in archive.namelist():
            raise ReadingPackError("encrypted or DRM-protected EPUB files are not supported")
        if _read_zip_member(archive, "mimetype") != b"application/epub+zip":
            raise ReadingPackError("EPUB mimetype is missing or invalid")
        container = _xml(_read_zip_member(archive, "META-INF/container.xml"), "container.xml")
        rootfiles = [element for element in container.iter() if _local(element.tag) == "rootfile"]
        if not rootfiles:
            raise ReadingPackError("EPUB container has no rootfile")
        opf_path = rootfiles[0].attrib.get("full-path", "")
        _safe_member_name(opf_path)
        opf = _xml(_read_zip_member(archive, opf_path), opf_path)
        if not opf.attrib.get("version", "").startswith("3"):
            raise ReadingPackError("only EPUB3 package documents are supported")

        title = "Untitled book"
        for element in opf.iter():
            if _local(element.tag) == "title" and _element_text(element):
                title = _element_text(element)
                break
        manifest = {
            element.attrib.get("id", ""): element.attrib.get("href", "")
            for element in opf.iter()
            if _local(element.tag) == "item"
        }
        spine = [
            element.attrib.get("idref", "")
            for element in opf.iter()
            if _local(element.tag) == "itemref"
        ]
        if len(spine) > MAX_EPUB_SPINE_ITEMS:
            raise ReadingPackError(
                f"EPUB spine contains more than {MAX_EPUB_SPINE_ITEMS} items"
            )
        if len(spine) != len(set(spine)):
            raise ReadingPackError("EPUB spine contains duplicate item references")
        chapters: list[dict] = []
        body_text: list[str] = []
        body_characters = 0
        seen_spine_members: set[str] = set()
        parsed_spine_bytes = 0
        for idref in spine:
            if idref not in manifest:
                raise ReadingPackError(f"EPUB spine references unknown manifest id: {idref}")
            member = _resolve_epub_path(opf_path, manifest[idref])
            if member in seen_spine_members:
                raise ReadingPackError("EPUB spine resolves multiple items to the same member")
            seen_spine_members.add(member)
            document_bytes = _read_zip_member(archive, member)
            parsed_spine_bytes += len(document_bytes)
            if parsed_spine_bytes > MAX_ARCHIVE_BYTES:
                raise ReadingPackError(
                    f"EPUB parsed spine exceeds {MAX_ARCHIVE_BYTES} bytes"
                )
            document = _xml(document_bytes, member)
            readable = " ".join(" ".join(document.itertext()).split())
            if readable:
                body_characters += len(readable)
                if body_characters > MAX_EPUB_TEXT_CHARACTERS:
                    raise ReadingPackError(
                        f"EPUB readable text exceeds {MAX_EPUB_TEXT_CHARACTERS} characters"
                    )
                body_text.append(readable)
            headings = [
                (int(_local(element.tag)[1]), _element_text(element))
                for element in document.iter()
                if re.fullmatch(r"h[1-6]", _local(element.tag)) and _element_text(element)
            ]
            if not headings:
                continue
            chapter_title = headings[0][1]
            sections = [heading for level, heading in headings[1:] if level > headings[0][0]]
            chapters.append(
                {
                    "id": f"CH-{len(chapters) + 1:02d}",
                    "title": chapter_title,
                    "pages": "",
                    "sections": sections,
                    "summary": "",
                    "terms": [],
                    "status": "draft",
                }
            )
        return ExtractedBook(title, chapters, "epub3"), "\n".join(body_text)


def extract_epub_authorized_text(path: Path) -> str:
    """Extract bounded EPUB spine text through the validated EPUB reader."""

    return _read_epub(path)[1]


def extract_epub(path: Path) -> ExtractedBook:
    return _read_epub(path)[0]


def detect_format(path: Path, explicit: str | None = None) -> str:
    if explicit:
        return explicit
    suffix = path.suffix.lower()
    return {
        ".md": "markdown",
        ".markdown": "markdown",
        ".org": "org",
        ".epub": "epub3",
        ".pdf": "pdf",
        ".txt": "text",
    }.get(suffix, "")


def extract(path: Path, explicit_format: str | None = None) -> ExtractedBook:
    fmt = detect_format(path, explicit_format)
    if fmt not in {"markdown", "org", "epub3", "pdf", "pdf-vertical", "text"}:
        raise ReadingPackError(
            "unsupported manuscript format; use UTF-8 Markdown, Org mode, EPUB3, PDF, vertical PDF, or plain text"
        )
    if fmt == "epub3":
        return extract_epub(path)
    if fmt == "pdf":
        return extract_pdf(path)
    if fmt == "pdf-vertical":
        return extract_pdf_vertical(path)
    try:
        text = read_regular_source_bytes(path).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReadingPackError(f"manuscript must be UTF-8: {path}") from exc
    return {"markdown": extract_markdown, "org": extract_org, "text": extract_text}[fmt](text)


def _import_manuscript_unlocked(
    project: Path,
    manuscript: Path,
    *,
    lang: str,
    explicit_format: str | None = None,
    force: bool = False,
) -> ExtractedBook:
    try:
        before = manuscript.stat()
    except OSError as exc:
        raise ReadingPackError(f"cannot read manuscript {manuscript}: {exc}", EXIT_IO) from exc
    config = load_config(project)
    if lang not in config.get("languages", []):
        raise ReadingPackError(f"language is not configured: {lang}")
    data = load_language_data(project, lang)
    if data.get("chapters") and not force:
        raise ReadingPackError(
            f"refusing to overwrite canonical data for {lang}; pass --force to replace extracted structure"
        )
    extracted = extract(manuscript, explicit_format)
    if not extracted.chapters:
        raise ReadingPackError("no chapter headings were found; add structured headings and try again")
    chapters = extracted.chapters
    primary = config["primary_language"]
    if lang != primary:
        primary_data = load_language_data(project, primary)
        primary_by_id = {item["id"]: item for item in primary_data.get("chapters", [])}
        for chapter in chapters:
            source = primary_by_id.get(chapter["id"])
            if source:
                chapter["source_id"] = source["id"]
                chapter["source_hash"] = semantic_hash(source)
                chapter["translation_status"] = "draft"
    raw = read_regular_source_bytes(manuscript)
    try:
        after = manuscript.stat()
    except OSError as exc:
        raise ReadingPackError(f"cannot read manuscript {manuscript}: {exc}", EXIT_IO) from exc
    identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    if identity_before != identity_after:
        raise ReadingPackError("manuscript changed during import; no canonical data was written", EXIT_IO)
    data["source"] = {
        "format": extracted.source_format,
        "name": manuscript.name,
        "sha256": file_hash(raw),
    }
    # Extracted titles are candidates only. Canonical project metadata remains
    # the value explicitly configured by the project owner.
    data["chapters"] = chapters
    write_json(project / "data" / f"pack.{lang}.json", data)
    return extracted


def import_manuscript(
    project: Path,
    manuscript: Path,
    *,
    lang: str,
    explicit_format: str | None = None,
    force: bool = False,
) -> ExtractedBook:
    """Compatibility import serialized with every other canonical mutator."""

    with project_lock(project):
        return _import_manuscript_unlocked(
            project,
            manuscript,
            lang=lang,
            explicit_format=explicit_format,
            force=force,
        )
