"""Deterministic, source-grounded catalog candidate extraction.

People, subject terms, and web references have different recall and review
requirements from claims.  This module therefore produces a private inventory
first, then converts only source- and chapter-grounded items into the ordinary
candidate workflow.  It never accepts, applies, or approves canonical data.

The built-in recognizers are conservative deterministic seed heuristics.  A
matching string is a review candidate, not a determination that a person or
term belongs in the published index.  Language-aware model or NER additions can
be supplied separately and are checked against the same exact source spans.
Chapter attribution becomes actionable only when the caller supplies an exact,
hash-checked normalized-text chapter span.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlparse

from .candidates import (
    _exact_source_term,
    _source_text_snapshot,
    _value_hash,
    create_candidate_run,
    load_candidate_run,
    normalize_text,
)
from reading_pack.errors import EXIT_IO, ReadingPackError
from reading_pack.importers import MAX_SOURCE_BYTES
from reading_pack.project import load_config, load_language_data
from reading_pack.schema_validation import require_structure
from reading_pack.source_registry import fingerprint_source, registered_source
from reading_pack.source_registry import SOURCE_FORMATS, SOURCE_LANGUAGES, SOURCE_ROLES
from .work_ledger import (
    create_work_ledger,
    reconcile_work_results,
    write_work_ledger,
)


CATALOG_SCHEMA_VERSION = 1
CATALOG_CONTEXT_SCHEMA_VERSION = 1
EXTRACTOR_VERSION = "catalog-heuristics-v3"
PDF_VERTICAL_EXTRACTOR_VERSION = (
    "catalog-heuristics-v3-pdf-vertical-conservative"
)
SUPPORTED_EXTRACTORS = {
    "catalog-heuristics-v1",
    "catalog-heuristics-v2",
    EXTRACTOR_VERSION,
    PDF_VERTICAL_EXTRACTOR_VERSION,
}
CATALOG_COLLECTIONS = ("names", "glossary", "references")
MAX_CATALOG_BYTES = 16 * 1024 * 1024
MAX_CHAPTER_MAP_BYTES = 4 * 1024 * 1024
MAX_CATALOG_ITEMS = 2_000
MAX_OCCURRENCES_PER_ITEM = 200
MAX_LABEL_CHARACTERS = 200
MAX_CHAPTER_SPANS = 20_000
MIN_GENERATED_EVIDENCE_CHARACTERS = 8
MAX_GENERATED_EVIDENCE_CHARACTERS = 500
MAX_GENERATED_EVIDENCE_PER_ITEM = 20
MAX_GENERATED_EVIDENCE_OCCURRENCE = 10_000
MAX_CONTEXT_PLAN_BYTES = 8 * 1024 * 1024

_CONTEXT_FIELDS = {
    "names": ("name", "book_context"),
    "glossary": ("term", "book_meaning"),
}

_SHA256 = re.compile(r"[a-f0-9]{64}")
_SOURCE_ID = re.compile(r"SRC-[A-Z0-9][A-Z0-9.-]{0,99}")
_CHAPTER_ID = re.compile(r"CH-[A-Z0-9][A-Z0-9.-]*")
_CONTROL = re.compile(
    r"[\x00-\x1f\x7f-\x9f\ud800-\udfff\u202a-\u202e\u2066-\u2069]"
)
_URL = re.compile(r"https?://[^\s<>\"'\]\[{}]+", re.IGNORECASE)
_DOI = re.compile(r"(?<![\w/])10\.\d{4,9}/[-._;()/:a-z0-9]+", re.IGNORECASE)
_ENGLISH_NAME = re.compile(
    r"(?<![A-Za-z])"
    r"([A-Z][a-z]{1,30}(?:[-'][A-Z]?[a-z]{1,30})?"
    r"(?:\s+[A-Z][a-z]{1,30}(?:[-'][A-Z]?[a-z]{1,30})?){1,3})"
    r"(?![A-Za-z])"
)
_ACRONYM = re.compile(r"(?<![A-Za-z0-9])([A-Z][A-Z0-9+.-]{1,15})(?![A-Za-z0-9])")

_ENGLISH_NAME_STOP = {
    "Active Inference",
    "Artificial Intelligence",
    "Artificial General Intelligence",
    "Chapter One",
    "Chapter Two",
    "Chapter Three",
    "Deep Learning",
    "Foundation Model",
    "Free Energy Principle",
    "Machine Learning",
    "New York",
    "Open Access",
    "Scaling Laws",
    "The Singularity Is Near",
    "Thousand Brains Theory",
    "United Kingdom",
    "United Nations",
    "United States",
    "Whole Brain Architecture",
}
_ACRONYM_STOP = {
    "AND", "ISBN", "ISSN", "HTTP", "HTTPS", "LAWS", "PDF", "THE", "URL", "WWW",
}
_JA_NAME_STOP = {
    "人工知能", "本書", "問題", "研究", "社会", "科学", "知能", "理論", "人間",
}
_JA_VERTICAL_KANJI_NONPERSON = {
    "名誉", "特任", "専任", "主任", "准", "客員", "非常勤",
}
_JA_VERTICAL_KANJI_NONPERSON_SUFFIXES = (
    "大学", "大学院", "学部", "学科", "研究所", "研究院", "研究機関",
    "委員会", "協議会", "財団", "政府", "特任", "主任", "専任",
)
_JA_NONPERSON_SUFFIXES = (
    "アーキテクチャ", "アプローチ", "アライメント", "アルゴリズム", "エージェント",
    "グループ", "コスト", "コンピュータ",
    "システム", "シナリオ", "スケーリング", "データ", "ネットワーク",
    "タスク", "トークン", "トランスフォーマー", "ハードウェア", "フレームワーク",
    "プラットフォーム", "プロセス", "ベンチマーク", "モデル", "リスク",
    "オートポイエーシス", "エナクティビズム", "クオリア", "シミュレーション",
    "スタートアップ", "センサー", "ダイナミズム", "パラダイム", "プルラリティ",
    "マップ", "ループ",
    "問題", "理論", "社会", "知能", "研究", "科学", "構造", "制度", "設計",
    "命題", "前提", "原理", "能力", "技術", "分析", "結論", "価値", "根拠",
)
_ATTRIBUTION = re.compile(
    r"論じ(?:た|て|る)?|述べ(?:た|て|る)?|提唱(?:し|した|する)|"
    r"指摘(?:し|した|する)|考案(?:し|した|する)|創始(?:し|した|する)|"
    r"主張(?:し|した|する)|実証(?:し|した|する)|受賞(?:し|した|する)|"
    r"定義(?:し|した|する)|予測(?:し|した|する)|示(?:し|した|す)|"
    r"唱え(?:た|て|る)?"
)
_EN_ATTRIBUTION = re.compile(
    r"\b(?:argued|claims?|demonstrated|described|developed|discovered|found|"
    r"introduced|observed|predicted|proposed|showed|suggested|wrote)\b"
)
_JA_KATAKANA_NAME = re.compile(
    r"([ァ-ヶー]{3,24}(?:・[ァ-ヶー]{2,24})?)(?=は|が|によ(?:り|る)|の)"
)
_JA_KANJI_NAME = re.compile(r"([一-龠々髙高]{2,6})(?=は|が|によ(?:り|る)|の)")
_JA_VERTICAL_KATAKANA_NAME = re.compile(
    r"([ァ-ヶー]{3,24}(?:・[ァ-ヶー]{2,24}){0,3})"
    r"(?=氏|博士|教授|は|が|によ(?:り|る)|の)"
)
_JA_VERTICAL_KANJI_NAME = re.compile(
    r"([一-龠々髙高]{2,5})(?=氏|博士|教授)"
)
_JA_QUOTED_TERM = re.compile(
    r"「\s*([^」]{2,40}?)\s*」\s*(?=とは|と呼ば|を[^。]{0,20}と呼)"
)
_JA_DEFINED_TERM = re.compile(
    r"(?:^|[。！？、「」])([ぁ-んァ-ヶー一-龠々A-Za-z][ぁ-んァ-ヶー一-龠々A-Za-z0-9・+／/_-]{1,39})(?=とは|と呼ば)"
)


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _content_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _inventory_integrity(value: Mapping[str, Any]) -> str:
    body = {key: item for key, item in value.items() if key != "integrity_sha256"}
    return hashlib.sha256(_json_bytes(body)).hexdigest()


def _inventory_id(value: Mapping[str, Any]) -> str:
    body = {
        key: item
        for key, item in value.items()
        if key not in {"inventory_id", "integrity_sha256"}
    }
    return f"CAT-{hashlib.sha256(_json_bytes(body)).hexdigest()[:20].upper()}"


def _context_plan_integrity(value: Mapping[str, Any]) -> str:
    body = {key: item for key, item in value.items() if key != "integrity_sha256"}
    return hashlib.sha256(_json_bytes(body)).hexdigest()


def _context_plan_id(value: Mapping[str, Any]) -> str:
    body = {
        key: item
        for key, item in value.items()
        if key not in {"plan_id", "integrity_sha256"}
    }
    return f"CTX-{hashlib.sha256(_json_bytes(body)).hexdigest()[:20].upper()}"


def _safe_label(value: Any, maximum: int = MAX_LABEL_CHARACTERS) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and len(value) <= maximum
        and not _CONTROL.search(value)
    )


def _source_identity(
    project: Path, language: str, source_id: str, source_path: Path
) -> tuple[dict[str, Any], Mapping[str, Any]]:
    data = load_language_data(project, language)
    fingerprint = fingerprint_source(source_path)
    if source_id == "SRC-1":
        canonical = data.get("source", {})
        if not isinstance(canonical, Mapping) or any(
            canonical.get(key) != fingerprint.get(key) for key in ("name", "sha256")
        ):
            raise ReadingPackError(
                "catalog source does not match the canonical primary source"
            )
        source_format = canonical.get("format")
        if not isinstance(source_format, str) or not source_format:
            raise ReadingPackError("canonical primary source format is missing")
        source = {
            "id": "SRC-1",
            "role": "primary-book",
            "language": language,
            "format": source_format,
            **fingerprint,
        }
    else:
        source = registered_source(project, source_id)
        if any(source.get(key) != fingerprint.get(key) for key in ("name", "sha256", "size_bytes")):
            raise ReadingPackError(f"registered source is stale or mismatched: {source_id}")
    if source.get("language") not in {language, "und"}:
        raise ReadingPackError(
            "catalog source language must match the target pack or be undetermined"
        )
    return source, data


def _validated_chapter_spans(
    values: Sequence[Mapping[str, Any]],
    *,
    normalized_source: str,
    chapter_ids: set[str],
) -> list[dict[str, Any]]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise ReadingPackError("chapter spans must be an array")
    if len(values) > MAX_CHAPTER_SPANS:
        raise ReadingPackError(f"chapter spans exceed {MAX_CHAPTER_SPANS} items")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    previous_end = 0
    for index, raw in enumerate(values):
        if not isinstance(raw, Mapping) or set(raw) != {
            "chapter_id", "char_start", "char_end", "span_sha256"
        }:
            raise ReadingPackError(f"chapter span {index} has invalid fields")
        chapter_id = raw["chapter_id"]
        start = raw["char_start"]
        end = raw["char_end"]
        digest = raw["span_sha256"]
        if (
            not isinstance(chapter_id, str)
            or not _CHAPTER_ID.fullmatch(chapter_id)
            or chapter_id not in chapter_ids
            or chapter_id in seen
        ):
            raise ReadingPackError(f"chapter span {index} has an invalid chapter ID")
        if (
            not isinstance(start, int)
            or isinstance(start, bool)
            or not isinstance(end, int)
            or isinstance(end, bool)
            or start < previous_end
            or start < 0
            or end <= start
            or end > len(normalized_source)
        ):
            raise ReadingPackError(f"chapter span {index} is unordered or out of range")
        actual = _content_hash(normalized_source[start:end])
        if not isinstance(digest, str) or digest != actual:
            raise ReadingPackError(f"chapter span {index} hash does not match the source")
        result.append(
            {
                "chapter_id": chapter_id,
                "char_start": start,
                "char_end": end,
                "span_sha256": digest,
            }
        )
        seen.add(chapter_id)
        previous_end = end
    return result


def _infer_chapter_spans(
    normalized_source: str, chapters: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Infer one conservative title-sequence source map.

    A dense first sequence is treated as a likely table of contents and skipped
    only when a second complete sequence exists. Ambiguity is still reported in
    the inventory and must be checked in the human review.
    """

    if not chapters:
        return []
    compact, offsets = _compact_view(normalized_source)
    occurrences: list[list[int]] = []
    for chapter in chapters:
        title = chapter.get("title") if isinstance(chapter, Mapping) else None
        if not _safe_label(title, 500):
            return []
        short_title = re.split(r"[―—–]{2,}|\s[-―—–]\s", title, maxsplit=1)[0]
        needle, _ = _compact_view(normalize_text(short_title))
        if not needle:
            return []
        positions: list[int] = []
        identifier = str(chapter.get("id", ""))
        numbered = re.fullmatch(r"CH-(\d+)", identifier)
        if numbered:
            number = str(int(numbered.group(1)))
            heading = re.compile(
                rf"第\s*{re.escape(number)}\s*章\s*{re.escape(needle)}"
            )
            positions = [match.start() for match in heading.finditer(compact)][:500]
        if not positions:
            cursor = 0
            while len(positions) < 500:
                position = compact.find(needle, cursor)
                if position < 0:
                    break
                positions.append(position)
                cursor = position + max(1, len(needle))
        if not positions:
            return []
        occurrences.append(positions)

    starts: list[int] = []
    for index, (chapter, values) in enumerate(zip(chapters, occurrences)):
        kind = str(chapter.get("kind", ""))
        if kind == "frontmatter" and index == 0:
            selected = values[0]
        elif kind == "afterword" and starts:
            selected = next((value for value in values if value > starts[-1] + 32), -1)
        elif len(values) >= 2:
            gap_index = max(
                range(len(values) - 1), key=lambda item: values[item + 1] - values[item]
            )
            selected = values[gap_index + 1]
        else:
            selected = values[0]
        if selected < 0 or (starts and selected <= starts[-1]):
            return []
        starts.append(selected)
    if not starts or any(right - left < 32 for left, right in zip(starts, starts[1:])):
        return []
    source_starts = [
        _source_span(position, position + 1, offsets, len(normalized_source))[0]
        for position in starts
    ]
    final_end = len(normalized_source)
    # Avoid assigning notes/glossary/bibliography after an unmodelled final
    # chapter to that chapter merely because it is the last known title.
    for marker in ("用語集", "参考文献", "bibliography", "glossary", "index"):
        compact_marker, _ = _compact_view(normalize_text(marker))
        position = compact.find(compact_marker, starts[-1] + 32)
        if position >= 0:
            boundary = _source_span(position, position + 1, offsets, len(normalized_source))[0]
            final_end = min(final_end, boundary)
    ends = source_starts[1:] + [final_end]
    if ends[-1] <= source_starts[-1]:
        return []
    return [
        {
            "chapter_id": chapter["id"],
            "char_start": start,
            "char_end": end,
            "span_sha256": _content_hash(normalized_source[start:end]),
        }
        for chapter, start, end in zip(chapters, source_starts, ends)
    ]


def _joinable_character(value: str) -> bool:
    if len(value) != 1:
        return False
    code = ord(value)
    return (
        value.isalnum()
        or 0x3040 <= code <= 0x30FF
        or 0x3400 <= code <= 0x9FFF
        or value in {"々", "〆", "ー", "・", "／", "/", "+", "-", "_"}
    )


def _compact_view(value: str) -> tuple[str, list[int]]:
    """Undo per-glyph PDF spacing while retaining a source-offset map."""

    matches = list(re.finditer(r"\S+", value))
    output: list[str] = []
    offsets: list[int] = []
    previous = ""
    for token_match in matches:
        token = token_match.group()
        merge = (
            len(previous) == 1
            and len(token) == 1
            and _joinable_character(previous)
            and _joinable_character(token)
        )
        if output and not merge:
            output.append(" ")
            offsets.append(max(0, token_match.start() - 1))
        for delta, character in enumerate(token):
            output.append(character)
            offsets.append(token_match.start() + delta)
        previous = token
    return "".join(output), offsets


def _source_span(
    match_start: int, match_end: int, offsets: Sequence[int], source_length: int
) -> tuple[int, int]:
    if match_start < 0 or match_end <= match_start or match_end > len(offsets):
        raise ReadingPackError("internal catalog match is out of range")
    start = offsets[match_start]
    end = min(source_length, offsets[match_end - 1] + 1)
    return start, end


def _chapter_for(start: int, end: int, spans: Sequence[Mapping[str, Any]]) -> str:
    matches = [
        span["chapter_id"]
        for span in spans
        if span["char_start"] <= start and end <= span["char_end"]
    ]
    return matches[0] if len(matches) == 1 else ""


def _occurrence(
    normalized_source: str,
    start: int,
    end: int,
    chapter_id: str,
) -> dict[str, Any]:
    span = normalized_source[start:end]
    result: dict[str, Any] = {
        "char_start": start,
        "char_end": end,
        "span_sha256": _content_hash(span),
    }
    if chapter_id:
        result["chapter_id"] = chapter_id
    return result


def _find_exact_occurrences(
    label: str,
    normalized_source: str,
    chapter_spans: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    needle = normalize_text(label)
    if not needle:
        return []
    result: list[dict[str, Any]] = []
    cursor = 0
    while len(result) < MAX_OCCURRENCES_PER_ITEM:
        start = normalized_source.find(needle, cursor)
        if start < 0:
            break
        end = start + len(needle)
        result.append(
            _occurrence(
                normalized_source, start, end, _chapter_for(start, end, chapter_spans)
            )
        )
        cursor = end
    return result


def _find_compact_occurrences(
    label: str,
    compact_normalized: str,
    normalized_offsets: Sequence[int],
    normalized_source: str,
    chapter_spans: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    needle, _ = _compact_view(normalize_text(label))
    if not needle:
        return []
    result: list[dict[str, Any]] = []
    cursor = 0
    ascii_word = bool(re.fullmatch(r"[a-z0-9]+", needle))
    while len(result) < MAX_OCCURRENCES_PER_ITEM:
        position = compact_normalized.find(needle, cursor)
        if position < 0:
            break
        compact_end = position + len(needle)
        if ascii_word and (
            (
                position > 0
                and bool(re.fullmatch(r"[a-z0-9]", compact_normalized[position - 1]))
            )
            or (
                compact_end < len(compact_normalized)
                and bool(
                    re.fullmatch(r"[a-z0-9]", compact_normalized[compact_end])
                )
            )
        ):
            cursor = position + 1
            continue
        start, end = _source_span(
            position, compact_end, normalized_offsets, len(normalized_source)
        )
        result.append(
            _occurrence(
                normalized_source, start, end, _chapter_for(start, end, chapter_spans)
            )
        )
        cursor = compact_end
    return result


def _preferred_term_occurrence(
    occurrences: Sequence[Mapping[str, Any]],
    normalized_source: str,
) -> Mapping[str, Any] | None:
    """Prefer the first definition-like mapped occurrence over a mere mention.

    Front matter and chapter previews often mention a term long before the book
    explains it.  Routing a glossary entry to that mention leaves both readers
    and the later context-enrichment pass with weak evidence.  The signal here
    is intentionally conservative: a nearby explicit definition, naming, or
    reference phrase wins; otherwise source order remains the fallback.
    """

    mapped = [item for item in occurrences if item.get("chapter_id")]
    if not mapped:
        return None

    def score(item: Mapping[str, Any]) -> tuple[int, int]:
        end = int(item["char_end"])
        after = re.sub(r"\s+", "", normalized_source[end : end + 160])
        definition_like = bool(
            re.match(
                r"^[」』”’\"'）):：、,―—-]{0,16}"
                r"(?:とは|を指す|を意味する|という(?:用語|概念|考え方)|"
                r"と呼(?:ぶ|ばれる)|と定義(?:する|される)|"
                r"means|refersto|isdefinedas|denotes)",
                after,
            )
            or re.match(
                r"^.{0,80}(?:と呼(?:ぶ|ばれる)|と定義(?:する|される)|"
                r"isdefinedas)",
                after,
            )
        )
        return (0 if definition_like else 1, int(item["char_start"]))

    return min(mapped, key=score)


def _candidate_key(kind: str, label: str, chapter_id: str, url: str = "") -> str:
    payload = f"{kind}\0{normalize_text(label)}\0{chapter_id}\0{url}"
    return f"CI-{hashlib.sha256(payload.encode()).hexdigest()[:20].upper()}"


def _aggregate_item(
    destination: dict[tuple[str, str, str, str], dict[str, Any]],
    *,
    kind: str,
    label: str,
    occurrence: Mapping[str, Any],
    confidence: str,
    reason: str,
    url: str = "",
) -> None:
    label = unicodedata.normalize("NFKC", label).strip(" \t\n、。，．:：;；()（）[]【】『』")
    if not _safe_label(label):
        return
    chapter_id = str(occurrence.get("chapter_id", ""))
    key = (kind, normalize_text(label), chapter_id, url)
    item = destination.get(key)
    if item is None:
        item = {
            "item_id": _candidate_key(kind, label, chapter_id, url),
            "kind": kind,
            "label": label,
            "url": url,
            "chapter": {
                "state": "resolved" if chapter_id else "unresolved",
                "chapter_id": chapter_id,
            },
            "confidence": confidence,
            "reason_codes": [reason],
            "occurrences": [],
        }
        destination[key] = item
    if occurrence not in item["occurrences"] and len(item["occurrences"]) < MAX_OCCURRENCES_PER_ITEM:
        item["occurrences"].append(dict(occurrence))
    if reason not in item["reason_codes"]:
        item["reason_codes"].append(reason)


def _strong_japanese_person_context(
    label: str,
    after: str,
    *,
    kanji: bool,
    conservative_vertical: bool = False,
) -> bool:
    if label in _JA_NAME_STOP or any(label.endswith(value) for value in _JA_NONPERSON_SUFFIXES):
        return False
    minimum_kanji = 2 if conservative_vertical else 3
    if kanji and (
        not minimum_kanji <= len(label) <= 5
        or label[0] in "本各同前後上下大小新全第"
        or (
            conservative_vertical
            and (
                label in _JA_VERTICAL_KANJI_NONPERSON
                or any(
                    label.endswith(suffix)
                    for suffix in _JA_VERTICAL_KANJI_NONPERSON_SUFFIXES
                )
            )
        )
    ):
        return False
    if not kanji and "・" not in label and not 3 <= len(label) <= 12:
        return False
    attribution = _ATTRIBUTION.pattern
    explicit_person_marker = bool(re.match(r"(?:氏|博士|教授)", after))
    honorific = bool(
        explicit_person_marker or re.match(r"(?:によれば|によると)", after)
    )
    if conservative_vertical:
        # Reconstructed vertical pages deliberately remove per-glyph line
        # breaks.  A broad ``.{N}`` context can therefore borrow an attribution
        # verb from the next sentence and turn an ordinary noun into a person.
        # Keep Kanji and unmarked Katakana seeds only when the source supplies
        # an explicit person marker; a model/NER recall pass may propose the
        # remaining names through the ordinary source-bound verifier.
        if kanji or "・" not in label:
            return explicit_person_marker
        return bool(
            honorific
            or re.match(
                rf"(?:は|が|により|による)[^。！？]{{0,20}}(?:{attribution})",
                after,
            )
            or re.match(r"の(?:理論|法則|議論|分類|予測|立場|構想)", after)
        )
    if kanji:
        # Short Kanji noun phrases are much more common than unmarked full
        # names. Keep only explicit person markers here and let a language-
        # aware NER/model recall pass propose unmarked Japanese names.
        return honorific
    return bool(
        honorific
        or re.match(rf"(?:は|が|により|による).{{0,28}}(?:{attribution})", after)
        or re.match(r"の(?:理論|法則|議論|分類|予測|立場|構想)", after)
    )


def _strong_latin_person_context(
    after: str, *, conservative_vertical: bool = False
) -> bool:
    attribution = _ATTRIBUTION.pattern
    context = r"[^。！？]{0,36}" if conservative_vertical else r".{0,36}"
    return bool(
        re.match(
            rf"\s*(?:氏|博士|教授|によれば|によると|は|が|により|による){context}(?:{attribution})",
            after,
        )
        or re.match(rf"\s*(?:{_EN_ATTRIBUTION.pattern})", after)
    )


def _vertical_quoted_term_label(label: str) -> bool:
    """Admit short explicit labels, not sentence-sized quoted assertions."""

    label = unicodedata.normalize("NFKC", label).strip()
    lexical = r"[ァ-ヶー一-龠々髙高A-Za-z0-9・+／/_-]+"
    return bool(
        2 <= len(label) <= 24
        and not re.search(r"[。！？]", label)
        and re.fullmatch(rf"{lexical}(?:の{lexical})?", label)
    )


def _vertical_defined_term_label(label: str) -> bool:
    """Require a compact lexical shape for an unquoted vertical definition."""

    label = unicodedata.normalize("NFKC", label).strip()
    return bool(
        2 <= len(label) <= 24
        and not re.search(r"[ぁ-ゖ]", label)
        and re.fullmatch(
            r"[ァ-ヶー一-龠々髙高A-Za-z0-9・+／/_-]+",
            label,
        )
    )


def _extract_catalog_items(
    source_text: str,
    normalized_source: str,
    chapter_spans: Sequence[Mapping[str, Any]],
    *,
    source_format: str = "",
) -> list[dict[str, Any]]:
    # The case-preserving view supplies display labels. Matches are re-found in
    # the normalized/compact source before a locator is retained because
    # Unicode case folding can change string length.
    display = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", source_text)).strip()
    compact_display, _ = _compact_view(display)
    compact_normalized, normalized_offsets = _compact_view(normalized_source)
    items: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    conservative_vertical = source_format == "pdf-vertical"

    # URLs and DOIs are deterministic reference-routing candidates.
    url_seen: Counter[str] = Counter()
    for match in _URL.finditer(display):
        raw_url = match.group().rstrip(".,;:!?)]}）】」』")
        parsed = urlparse(raw_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or "." not in parsed.netloc
            or raw_url.endswith(("-", ".", "/"))
        ):
            continue
        if parsed.netloc.casefold() in {"doi.org", "dx.doi.org"} and not _DOI.fullmatch(
            parsed.path.lstrip("/")
        ):
            continue
        url_occurrences = _find_exact_occurrences(
            raw_url, normalized_source, chapter_spans
        )
        if not url_occurrences:
            continue
        occurrence_index = url_seen[normalize_text(raw_url)]
        if occurrence_index >= len(url_occurrences):
            continue
        occurrence = url_occurrences[occurrence_index]
        url_seen[normalize_text(raw_url)] += 1
        _aggregate_item(
            items,
            kind="reference",
            label=parsed.netloc + (parsed.path if parsed.path not in {"", "/"} else ""),
            url=raw_url,
            occurrence=occurrence,
            confidence="high",
            reason="explicit_http_url",
        )
    doi_seen: Counter[str] = Counter()
    for match in _DOI.finditer(display):
        doi = match.group().rstrip(".,;:!?)]}）】」』")
        url = f"https://doi.org/{doi.lower()}"
        doi_occurrences = _find_exact_occurrences(
            doi, normalized_source, chapter_spans
        )
        if not doi_occurrences:
            continue
        occurrence_index = doi_seen[normalize_text(doi)]
        if occurrence_index >= len(doi_occurrences):
            continue
        occurrence = doi_occurrences[occurrence_index]
        doi_seen[normalize_text(doi)] += 1
        _aggregate_item(
            items,
            kind="reference",
            label=f"DOI {doi}",
            url=url,
            occurrence=occurrence,
            confidence="high",
            reason="explicit_doi",
        )

    # English names use case-preserving text. Exact normalized occurrences are
    # re-found so NFKC/case folding and repeated names remain evidence-safe.
    for match in _ENGLISH_NAME.finditer(display):
        label = match.group(1)
        for heading in sorted(_ENGLISH_NAME_STOP, key=len, reverse=True):
            if label.startswith(heading + " "):
                label = label[len(heading) + 1 :]
                break
        if label in _ENGLISH_NAME_STOP:
            continue
        for occurrence in _find_exact_occurrences(label, normalized_source, chapter_spans):
            context, _ = _compact_view(
                normalized_source[
                    occurrence["char_end"] : occurrence["char_end"] + 100
                ]
            )
            if not _strong_latin_person_context(
                context, conservative_vertical=conservative_vertical
            ):
                continue
            _aggregate_item(
                items,
                kind="person",
                label=label,
                occurrence=occurrence,
                confidence="medium",
                reason="latin_multi_token_name_shape",
            )

    # PDF often inserts spaces between every glyph. Compact views restore those
    # runs and map every match back to the standard evidence representation.
    compact_pairs = (
        (
            _JA_VERTICAL_KATAKANA_NAME
            if conservative_vertical
            else _JA_KATAKANA_NAME,
            (
                "vertical_katakana_person_context"
                if conservative_vertical
                else "katakana_attribution_context"
            ),
            False,
        ),
        (
            _JA_VERTICAL_KANJI_NAME if conservative_vertical else _JA_KANJI_NAME,
            (
                "vertical_kanji_person_marker"
                if conservative_vertical
                else "kanji_attribution_context"
            ),
            True,
        ),
    )
    for pattern, reason, kanji in compact_pairs:
        for match in pattern.finditer(compact_normalized):
            label = match.group(1)
            after = compact_normalized[match.end(1) : match.end(1) + 80]
            if not _strong_japanese_person_context(
                label,
                after,
                kanji=kanji,
                conservative_vertical=conservative_vertical,
            ):
                continue
            start, end = _source_span(
                match.start(1), match.end(1), normalized_offsets, len(normalized_source)
            )
            occurrence = _occurrence(
                normalized_source, start, end, _chapter_for(start, end, chapter_spans)
            )
            _aggregate_item(
                items,
                kind="person",
                label=label,
                occurrence=occurrence,
                confidence="low",
                reason=reason,
            )

    # Definition-shaped phrases and acronyms are subject-index candidates; no
    # definitions are generated or retained.
    for pattern, reason in (
        (_JA_QUOTED_TERM, "quoted_definition_shape"),
        (_JA_DEFINED_TERM, "definition_shape"),
    ):
        for match in pattern.finditer(compact_display):
            label = compact_display[match.start(1) : match.end(1)]
            item_reason = reason
            if conservative_vertical:
                if pattern is _JA_QUOTED_TERM:
                    if not _vertical_quoted_term_label(label):
                        continue
                    item_reason = "vertical_quoted_definition_shape"
                else:
                    if not _vertical_defined_term_label(label):
                        continue
                    item_reason = "vertical_definition_token_shape"
            occurrences = _find_compact_occurrences(
                label,
                compact_normalized,
                normalized_offsets,
                normalized_source,
                chapter_spans,
            )
            if occurrences:
                _aggregate_item(
                    items,
                    kind="term",
                    label=label,
                    occurrence=occurrences[0],
                    confidence="medium",
                    reason=item_reason,
                )
    for match in _ACRONYM.finditer(compact_display):
        label = match.group(1)
        if (
            label in _ACRONYM_STOP
            or "." in label
            or re.fullmatch(r"[IVXLCDM]+", label)
            or (label.isalpha() and len(label) > 8)
        ):
            continue
        for occurrence in _find_compact_occurrences(
            label,
            compact_normalized,
            normalized_offsets,
            normalized_source,
            chapter_spans,
        ):
            _aggregate_item(
                items,
                kind="term",
                label=label,
                occurrence=occurrence,
                confidence="low",
                reason="uppercase_acronym_shape",
            )

    # The canonical glossary points to the first substantive explanation when
    # a conservative definition/naming signal exists, otherwise to first
    # occurrence. Collapse the same term across chapters while leaving people
    # occurrence-shaped by chapter. Book-scope references likewise collapse by
    # URL across chapter occurrences.
    first_terms: dict[str, dict[str, Any]] = {}
    references: dict[str, dict[str, Any]] = {}
    people: list[dict[str, Any]] = []
    for item in items.values():
        if item["kind"] == "person":
            people.append(item)
            continue
        if item["kind"] == "term":
            all_occurrences = _find_compact_occurrences(
                item["label"],
                compact_normalized,
                normalized_offsets,
                normalized_source,
                chapter_spans,
            )
            preferred = _preferred_term_occurrence(
                all_occurrences, normalized_source
            )
            if preferred is not None:
                item = dict(item)
                item["chapter"] = {
                    "state": "resolved",
                    "chapter_id": preferred["chapter_id"],
                }
                item["occurrences"] = [preferred]
                item["item_id"] = _candidate_key(
                    "term", item["label"], preferred["chapter_id"]
                )
            key = normalize_text(item["label"])
            current = first_terms.get(key)
            first_position = item["occurrences"][0]["char_start"]
            if current is None or first_position < current["occurrences"][0]["char_start"]:
                first_terms[key] = item
            continue
        key = normalize_text(item["url"])
        current = references.get(key)
        if current is None:
            current = dict(item)
            current["chapter"] = {"state": "unresolved", "chapter_id": ""}
            current["item_id"] = _candidate_key(
                "reference", current["label"], "", current["url"]
            )
            current["occurrences"] = []
            references[key] = current
        for occurrence in item["occurrences"]:
            book_occurrence = {
                key: value
                for key, value in occurrence.items()
                if key != "chapter_id"
            }
            if book_occurrence not in current["occurrences"]:
                current["occurrences"].append(book_occurrence)
        for reason in item["reason_codes"]:
            if reason not in current["reason_codes"]:
                current["reason_codes"].append(reason)
    ordered = sorted(
        people + list(first_terms.values()) + list(references.values()),
        key=lambda item: (
            {"person": 0, "term": 1, "reference": 2}[item["kind"]],
            item["chapter"]["chapter_id"],
            normalize_text(item["label"]),
            item["url"],
        ),
    )
    if len(ordered) > 10_000:
        raise ReadingPackError(
            "catalog extraction exceeds 10000 detected candidates; narrow the source or use module chunks"
        )
    for item in ordered:
        item["reason_codes"].sort()
        item["occurrences"].sort(
            key=lambda occurrence: (occurrence["char_start"], occurrence["char_end"])
        )
    return ordered


def extract_catalog(
    project: Path,
    source_id: str,
    source_path: Path,
    *,
    language: str,
    chapter_spans: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Extract a deterministic private inventory without changing canonical data."""

    project = Path(project).resolve()
    source_path = Path(source_path).resolve()
    source, canonical_data = _source_identity(project, language, source_id, source_path)
    _, source_text = _source_text_snapshot(source_path, source_format=source["format"])
    normalized_source = normalize_text(source_text)
    text_sha256 = _content_hash(normalized_source)
    chapters = canonical_data.get("chapters", [])
    chapter_ids = {
        item.get("id")
        for item in chapters
        if isinstance(item, Mapping) and isinstance(item.get("id"), str)
    }
    inferred = chapter_spans is None
    raw_spans = _infer_chapter_spans(normalized_source, chapters) if inferred else chapter_spans
    spans = _validated_chapter_spans(
        raw_spans,
        normalized_source=normalized_source,
        chapter_ids=chapter_ids,
    )
    detected_items = _extract_catalog_items(
        source_text,
        normalized_source,
        spans,
        source_format=source["format"],
    )
    unresolved = Counter(
        item["kind"]
        for item in detected_items
        if item["kind"] in {"person", "term"}
        and item["chapter"]["state"] == "unresolved"
    )
    # An unresolved person/term cannot become a canonical occurrence record and
    # can be very numerous in notes or bibliography sections. Keep its count as
    # an omission signal, but do not retain the label as an actionable item.
    items = [
        item
        for item in detected_items
        if item["kind"] == "reference" or item["chapter"]["state"] == "resolved"
    ]
    if len(items) > MAX_CATALOG_ITEMS:
        raise ReadingPackError(
            f"catalog inventory exceeds {MAX_CATALOG_ITEMS} actionable candidates; narrow the source or use module chunks"
        )
    counts = Counter(item["kind"] for item in items)
    inventory: dict[str, Any] = {
        "schema_version": CATALOG_SCHEMA_VERSION,
        "inventory_id": "",
        "extractor": (
            PDF_VERTICAL_EXTRACTOR_VERSION
            if source["format"] == "pdf-vertical"
            else EXTRACTOR_VERSION
        ),
        "language": language,
        "source": {
            key: source[key]
            for key in (
                "id", "role", "language", "name", "format", "sha256", "size_bytes"
            )
        },
        "text_sha256": text_sha256,
        "canonical_data_sha256": _value_hash(canonical_data),
        "chapter_map": {
            "method": "title_sequence" if inferred and spans else ("explicit" if spans else "none"),
            "review_required": bool(inferred and spans),
        },
        "chapter_spans": spans,
        "items": items,
        "summary": {
            "total": len(items),
            "people": counts["person"],
            "terms": counts["term"],
            "references": counts["reference"],
            "unresolved_people": unresolved["person"],
            "unresolved_terms": unresolved["term"],
            "resolved_chapters": len(spans),
        },
        "integrity_sha256": "",
    }
    inventory["inventory_id"] = _inventory_id(inventory)
    inventory["integrity_sha256"] = _inventory_integrity(inventory)
    validate_catalog_inventory(inventory)
    return inventory


def validate_catalog_inventory(value: Any) -> dict[str, Any]:
    """Strictly validate a catalog inventory and its internal hashes."""

    require_structure("catalog-inventory.schema.json", value, label="catalog inventory")
    if not isinstance(value, Mapping):
        raise ReadingPackError("invalid catalog inventory: root must be an object")
    required = {
        "schema_version", "inventory_id", "extractor", "language", "source",
        "text_sha256", "canonical_data_sha256", "chapter_map", "chapter_spans", "items",
        "summary", "integrity_sha256",
    }
    if set(value) != required:
        raise ReadingPackError("invalid catalog inventory: root fields are invalid")
    if (
        value["schema_version"] != CATALOG_SCHEMA_VERSION
        or value["extractor"] not in SUPPORTED_EXTRACTORS
    ):
        raise ReadingPackError("invalid catalog inventory: unsupported version")
    if value["language"] not in {"ja", "en"}:
        raise ReadingPackError("invalid catalog inventory: language is invalid")
    if not isinstance(value["inventory_id"], str) or not re.fullmatch(r"CAT-[A-F0-9]{20}", value["inventory_id"]):
        raise ReadingPackError("invalid catalog inventory: inventory ID is invalid")
    if value["inventory_id"] != _inventory_id(value):
        raise ReadingPackError("invalid catalog inventory: inventory ID is stale")
    if not isinstance(value["integrity_sha256"], str) or value["integrity_sha256"] != _inventory_integrity(value):
        raise ReadingPackError("invalid catalog inventory: integrity hash does not match")
    for key in ("text_sha256", "canonical_data_sha256"):
        if not isinstance(value[key], str) or not _SHA256.fullmatch(value[key]):
            raise ReadingPackError(f"invalid catalog inventory: {key} is invalid")
    source = value["source"]
    if not isinstance(source, Mapping) or set(source) != {
        "id", "role", "language", "name", "format", "sha256", "size_bytes"
    }:
        raise ReadingPackError("invalid catalog inventory: source is invalid")
    if (
        not isinstance(source["id"], str)
        or not _SOURCE_ID.fullmatch(source["id"])
        or source["role"] not in SOURCE_ROLES
        or source["language"] not in SOURCE_LANGUAGES
        or source["format"] not in SOURCE_FORMATS
        or not _safe_label(source["name"], 500)
        or (source["id"] == "SRC-1" and source["role"] != "primary-book")
        or source["language"] not in {value["language"], "und"}
        or (source["id"] == "SRC-1" and source["language"] != value["language"])
    ):
        raise ReadingPackError("invalid catalog inventory: source identity is invalid")
    if Path(source["name"]).name != source["name"] or not _SHA256.fullmatch(str(source["sha256"])):
        raise ReadingPackError("invalid catalog inventory: source fingerprint is invalid")
    if (
        not isinstance(source["size_bytes"], int)
        or isinstance(source["size_bytes"], bool)
        or not 0 <= source["size_bytes"] <= MAX_SOURCE_BYTES
    ):
        raise ReadingPackError("invalid catalog inventory: source size is invalid")
    chapter_map = value["chapter_map"]
    if (
        not isinstance(chapter_map, Mapping)
        or set(chapter_map) != {"method", "review_required"}
        or chapter_map["method"] not in {"none", "explicit", "title_sequence"}
        or not isinstance(chapter_map["review_required"], bool)
        or chapter_map["review_required"] != (chapter_map["method"] == "title_sequence")
    ):
        raise ReadingPackError("invalid catalog inventory: chapter map is invalid")
    spans = value["chapter_spans"]
    if not isinstance(spans, list) or len(spans) > MAX_CHAPTER_SPANS:
        raise ReadingPackError("invalid catalog inventory: chapter spans are invalid")
    previous_end = 0
    chapter_ids: set[str] = set()
    for index, span in enumerate(spans):
        if not isinstance(span, Mapping) or set(span) != {
            "chapter_id", "char_start", "char_end", "span_sha256"
        }:
            raise ReadingPackError(f"invalid catalog inventory: chapter span {index}")
        if (
            not isinstance(span["chapter_id"], str)
            or not _CHAPTER_ID.fullmatch(span["chapter_id"])
            or span["chapter_id"] in chapter_ids
            or not isinstance(span["char_start"], int)
            or isinstance(span["char_start"], bool)
            or not isinstance(span["char_end"], int)
            or isinstance(span["char_end"], bool)
            or span["char_start"] < previous_end
            or span["char_end"] <= span["char_start"]
            or not isinstance(span["span_sha256"], str)
            or not _SHA256.fullmatch(span["span_sha256"])
        ):
            raise ReadingPackError(f"invalid catalog inventory: chapter span {index}")
        chapter_ids.add(span["chapter_id"])
        previous_end = span["char_end"]
    items = value["items"]
    if not isinstance(items, list) or len(items) > MAX_CATALOG_ITEMS:
        raise ReadingPackError("invalid catalog inventory: items are invalid")
    seen_items: set[str] = set()
    for index, item in enumerate(items):
        if not isinstance(item, Mapping) or set(item) != {
            "item_id", "kind", "label", "url", "chapter", "confidence",
            "reason_codes", "occurrences",
        }:
            raise ReadingPackError(f"invalid catalog inventory: item {index} fields")
        if (
            not isinstance(item["item_id"], str)
            or not re.fullmatch(r"CI-[A-F0-9]{20}", item["item_id"])
            or item["item_id"] in seen_items
            or item["kind"] not in {"person", "term", "reference"}
            or not _safe_label(item["label"])
            or item["confidence"] not in {"low", "medium", "high"}
        ):
            raise ReadingPackError(f"invalid catalog inventory: item {index}")
        seen_items.add(item["item_id"])
        url = item["url"]
        if item["kind"] == "reference":
            parsed = urlparse(url) if isinstance(url, str) else None
            if (
                parsed is None
                or not _safe_label(url, 2_048)
                or parsed.scheme not in {"http", "https"}
                or not parsed.netloc
            ):
                raise ReadingPackError(f"invalid catalog inventory: item {index} URL")
        elif url != "":
            raise ReadingPackError(f"invalid catalog inventory: item {index} unexpected URL")
        chapter = item["chapter"]
        if not isinstance(chapter, Mapping) or set(chapter) != {"state", "chapter_id"}:
            raise ReadingPackError(f"invalid catalog inventory: item {index} chapter")
        chapter_id = chapter["chapter_id"]
        if chapter["state"] == "resolved":
            if chapter_id not in chapter_ids:
                raise ReadingPackError(f"invalid catalog inventory: item {index} chapter binding")
        elif chapter["state"] != "unresolved" or chapter_id != "":
            raise ReadingPackError(f"invalid catalog inventory: item {index} chapter state")
        reasons = item["reason_codes"]
        if (
            not isinstance(reasons, list)
            or not reasons
            or len(set(reasons)) != len(reasons)
            or not all(
                isinstance(reason, str)
                and re.fullmatch(r"[a-z][a-z0-9_]{0,99}", reason)
                for reason in reasons
            )
        ):
            raise ReadingPackError(f"invalid catalog inventory: item {index} reasons")
        occurrences = item["occurrences"]
        if not isinstance(occurrences, list) or not occurrences or len(occurrences) > MAX_OCCURRENCES_PER_ITEM:
            raise ReadingPackError(f"invalid catalog inventory: item {index} occurrences")
        for occurrence in occurrences:
            allowed = {"char_start", "char_end", "span_sha256", "chapter_id"}
            if not isinstance(occurrence, Mapping) or not set(occurrence) <= allowed or not {
                "char_start", "char_end", "span_sha256"
            } <= set(occurrence):
                raise ReadingPackError(f"invalid catalog inventory: item {index} occurrence")
            if (
                not isinstance(occurrence["char_start"], int)
                or isinstance(occurrence["char_start"], bool)
                or not isinstance(occurrence["char_end"], int)
                or isinstance(occurrence["char_end"], bool)
                or occurrence["char_start"] < 0
                or occurrence["char_end"] <= occurrence["char_start"]
                or not isinstance(occurrence["span_sha256"], str)
                or not _SHA256.fullmatch(occurrence["span_sha256"])
                or (
                    occurrence.get("chapter_id", "") != chapter_id
                    if chapter_id
                    else "chapter_id" in occurrence
                )
            ):
                raise ReadingPackError(f"invalid catalog inventory: item {index} occurrence")
        expected_item_id = _candidate_key(item["kind"], item["label"], chapter_id, url)
        if item["item_id"] != expected_item_id:
            raise ReadingPackError(f"invalid catalog inventory: item {index} ID is stale")
    summary = value["summary"]
    summary_keys = {
        "total", "people", "terms", "references", "unresolved_people",
        "unresolved_terms", "resolved_chapters",
    }
    if not isinstance(summary, Mapping) or set(summary) != summary_keys or not all(
        isinstance(summary[key], int) and not isinstance(summary[key], bool) and summary[key] >= 0
        for key in summary_keys
    ):
        raise ReadingPackError("invalid catalog inventory: summary is invalid")
    actual = Counter(item["kind"] for item in items)
    if any(
        summary[key] != expected
        for key, expected in {
            "total": len(items),
            "people": actual["person"],
            "terms": actual["term"],
            "references": actual["reference"],
            "resolved_chapters": len(spans),
        }.items()
    ):
        raise ReadingPackError("invalid catalog inventory: summary is stale")
    return json.loads(json.dumps(value, ensure_ascii=False))


def write_catalog_inventory(path: Path, inventory: Mapping[str, Any]) -> None:
    checked = validate_catalog_inventory(inventory)
    path = Path(path).resolve()
    if path.exists() or path.is_symlink():
        raise ReadingPackError(f"refusing to overwrite catalog inventory: {path}", EXIT_IO)
    encoded = json.dumps(checked, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    if len(encoded) > MAX_CATALOG_BYTES:
        raise ReadingPackError(f"catalog inventory exceeds {MAX_CATALOG_BYTES} bytes", EXIT_IO)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor: int | None = None
    temporary: Path | None = None
    try:
        descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temporary = Path(name)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = None
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path, follow_symlinks=False)
        temporary.unlink()
        temporary = None
        path.chmod(0o600)
    except OSError as exc:
        raise ReadingPackError(f"cannot write catalog inventory {path}: {exc}", EXIT_IO) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _strict_json(text: str) -> Any:
    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in values:
            if key in result:
                raise ValueError(f"duplicate object key: {key}")
            result[key] = value
        return result

    return json.loads(
        text,
        object_pairs_hook=pairs,
        parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
    )


def load_catalog_inventory(path: Path) -> dict[str, Any]:
    path = Path(path).resolve()
    try:
        if path.stat().st_size > MAX_CATALOG_BYTES:
            raise ReadingPackError(f"catalog inventory exceeds {MAX_CATALOG_BYTES} bytes", EXIT_IO)
        value = _strict_json(path.read_text(encoding="utf-8"))
    except ReadingPackError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ReadingPackError(f"cannot read catalog inventory {path}: {exc}", EXIT_IO) from exc
    return validate_catalog_inventory(value)


def create_catalog_context_plan(
    project: Path,
    *,
    language: str,
    inventory: Mapping[str, Any],
    collections: Iterable[str] = ("names", "glossary"),
    refresh_existing: bool = False,
) -> dict[str, Any]:
    """Create a body-free, complete work plan for book-specific index context.

    Catalog extraction answers *what* occurs and *where*.  This second plan is
    intentionally created only after the accepted catalog records exist, so a
    model or editor explains the entries that will actually be published rather
    than spending effort on unreviewed detector output.
    """

    project = Path(project).resolve()
    checked_inventory = validate_catalog_inventory(inventory)
    requested = list(dict.fromkeys(collections))
    if not requested or any(collection not in _CONTEXT_FIELDS for collection in requested):
        raise ReadingPackError("catalog context collections must be names or glossary")
    if checked_inventory["language"] != language:
        raise ReadingPackError("catalog context inventory language is mismatched")
    if (
        checked_inventory["source"]["id"] != "SRC-1"
        or checked_inventory["source"]["role"] != "primary-book"
    ):
        raise ReadingPackError("catalog context must be grounded in the primary book source")
    if checked_inventory["chapter_map"]["method"] != "explicit":
        raise ReadingPackError(
            "catalog context requires an explicit, reviewed chapter map"
        )
    config = load_config(project)
    if language not in config.get("languages", []):
        raise ReadingPackError("catalog context language is not configured")
    canonical = load_language_data(project, language)
    source = canonical.get("source", {})
    inventory_source = checked_inventory["source"]
    if any(
        source.get(key) != inventory_source.get(key)
        for key in ("name", "format", "sha256")
    ):
        raise ReadingPackError("catalog context inventory is bound to another primary source")
    chapter_ids = {
        chapter.get("id")
        for chapter in canonical.get("chapters", [])
        if isinstance(chapter, Mapping) and isinstance(chapter.get("id"), str)
    }
    mapped_ids = {
        span["chapter_id"] for span in checked_inventory["chapter_spans"]
    }
    targets: list[dict[str, Any]] = []
    for collection in requested:
        value_key, context_key = _CONTEXT_FIELDS[collection]
        for record in canonical.get(collection, []):
            if not isinstance(record, Mapping):
                raise ReadingPackError(f"canonical {collection} record is invalid")
            chapter_id = record.get("chapter_id")
            if chapter_id not in chapter_ids or chapter_id not in mapped_ids:
                raise ReadingPackError(
                    f"catalog context target {record.get('id', '')} has no reviewed chapter span"
                )
            if (
                not refresh_existing
                and isinstance(record.get(context_key), str)
                and record[context_key].strip()
            ):
                continue
            targets.append(
                {
                    "collection": collection,
                    "record_id": record["id"],
                    "value": record[value_key],
                    "chapter_id": chapter_id,
                    "context_field": context_key,
                    "base_record_sha256": _value_hash(record),
                }
            )
    targets.sort(key=lambda item: (requested.index(item["collection"]), item["chapter_id"], item["record_id"]))
    counts = Counter(target["collection"] for target in targets)
    plan: dict[str, Any] = {
        "schema_version": CATALOG_CONTEXT_SCHEMA_VERSION,
        "plan_id": "",
        "language": language,
        "source": {
            key: inventory_source[key]
            for key in ("id", "role", "language", "name", "format", "sha256", "size_bytes")
        },
        "text_sha256": checked_inventory["text_sha256"],
        "canonical_data_sha256": _value_hash(canonical),
        "catalog_inventory_id": checked_inventory["inventory_id"],
        "catalog_integrity_sha256": checked_inventory["integrity_sha256"],
        "chapter_spans": json.loads(
            json.dumps(checked_inventory["chapter_spans"], ensure_ascii=False)
        ),
        "targets": targets,
        "summary": {
            "total": len(targets),
            "names": counts["names"],
            "glossary": counts["glossary"],
        },
        "integrity_sha256": "",
    }
    plan["plan_id"] = _context_plan_id(plan)
    plan["integrity_sha256"] = _context_plan_integrity(plan)
    return validate_catalog_context_plan(plan)


def validate_catalog_context_plan(value: Any) -> dict[str, Any]:
    """Strictly validate the excerpt-free catalog-context work plan."""

    require_structure(
        "catalog-context-plan.schema.json", value, label="catalog context plan"
    )
    if not isinstance(value, Mapping):
        raise ReadingPackError("invalid catalog context plan: root must be an object")
    required = {
        "schema_version", "plan_id", "language", "source", "text_sha256",
        "canonical_data_sha256", "catalog_inventory_id",
        "catalog_integrity_sha256", "chapter_spans", "targets", "summary",
        "integrity_sha256",
    }
    if set(value) != required:
        raise ReadingPackError("invalid catalog context plan: root fields are invalid")
    if value["schema_version"] != CATALOG_CONTEXT_SCHEMA_VERSION:
        raise ReadingPackError("invalid catalog context plan: unsupported version")
    if (
        not isinstance(value["plan_id"], str)
        or not re.fullmatch(r"CTX-[A-F0-9]{20}", value["plan_id"])
        or value["plan_id"] != _context_plan_id(value)
    ):
        raise ReadingPackError("invalid catalog context plan: plan ID is stale")
    if (
        not isinstance(value["integrity_sha256"], str)
        or value["integrity_sha256"] != _context_plan_integrity(value)
    ):
        raise ReadingPackError("invalid catalog context plan: integrity hash does not match")
    if value["language"] not in {"ja", "en"}:
        raise ReadingPackError("invalid catalog context plan: language is invalid")
    for key in (
        "text_sha256", "canonical_data_sha256", "catalog_integrity_sha256"
    ):
        if not isinstance(value[key], str) or not _SHA256.fullmatch(value[key]):
            raise ReadingPackError(f"invalid catalog context plan: {key} is invalid")
    if (
        not isinstance(value["catalog_inventory_id"], str)
        or not re.fullmatch(r"CAT-[A-F0-9]{20}", value["catalog_inventory_id"])
    ):
        raise ReadingPackError("invalid catalog context plan: inventory ID is invalid")
    source = value["source"]
    if not isinstance(source, Mapping) or set(source) != {
        "id", "role", "language", "name", "format", "sha256", "size_bytes"
    }:
        raise ReadingPackError("invalid catalog context plan: source is invalid")
    if (
        source["id"] != "SRC-1"
        or source["role"] != "primary-book"
        or source["language"] != value["language"]
        or source["format"] not in SOURCE_FORMATS
        or not _safe_label(source["name"], 500)
        or Path(source["name"]).name != source["name"]
        or not isinstance(source["sha256"], str)
        or not _SHA256.fullmatch(source["sha256"])
        or not isinstance(source["size_bytes"], int)
        or isinstance(source["size_bytes"], bool)
        or not 0 <= source["size_bytes"] <= MAX_SOURCE_BYTES
    ):
        raise ReadingPackError("invalid catalog context plan: source identity is invalid")
    spans = value["chapter_spans"]
    if not isinstance(spans, list) or not spans or len(spans) > MAX_CHAPTER_SPANS:
        raise ReadingPackError("invalid catalog context plan: chapter spans are invalid")
    previous_end = 0
    span_ids: set[str] = set()
    for index, span in enumerate(spans):
        if not isinstance(span, Mapping) or set(span) != {
            "chapter_id", "char_start", "char_end", "span_sha256"
        }:
            raise ReadingPackError(f"invalid catalog context plan: chapter span {index}")
        if (
            not isinstance(span["chapter_id"], str)
            or not _CHAPTER_ID.fullmatch(span["chapter_id"])
            or span["chapter_id"] in span_ids
            or not isinstance(span["char_start"], int)
            or isinstance(span["char_start"], bool)
            or not isinstance(span["char_end"], int)
            or isinstance(span["char_end"], bool)
            or span["char_start"] < previous_end
            or span["char_end"] <= span["char_start"]
            or not isinstance(span["span_sha256"], str)
            or not _SHA256.fullmatch(span["span_sha256"])
        ):
            raise ReadingPackError(f"invalid catalog context plan: chapter span {index}")
        span_ids.add(span["chapter_id"])
        previous_end = span["char_end"]
    targets = value["targets"]
    if not isinstance(targets, list) or len(targets) > MAX_CATALOG_ITEMS:
        raise ReadingPackError("invalid catalog context plan: targets are invalid")
    seen: set[tuple[str, str]] = set()
    for index, target in enumerate(targets):
        if not isinstance(target, Mapping) or set(target) != {
            "collection", "record_id", "value", "chapter_id", "context_field",
            "base_record_sha256",
        }:
            raise ReadingPackError(f"invalid catalog context plan: target {index} fields")
        collection = target["collection"]
        if collection not in _CONTEXT_FIELDS:
            raise ReadingPackError(f"invalid catalog context plan: target {index} collection")
        _, context_field = _CONTEXT_FIELDS[collection]
        record_id = target["record_id"]
        if (
            not isinstance(record_id, str)
            or not _GENERATED_RECORD_IDS[collection].fullmatch(record_id)
            or (collection, record_id) in seen
            or target["chapter_id"] not in span_ids
            or target["context_field"] != context_field
            or not _safe_label(target["value"])
            or not isinstance(target["base_record_sha256"], str)
            or not _SHA256.fullmatch(target["base_record_sha256"])
        ):
            raise ReadingPackError(f"invalid catalog context plan: target {index}")
        seen.add((collection, record_id))
    summary = value["summary"]
    counts = Counter(target["collection"] for target in targets)
    if not isinstance(summary, Mapping) or set(summary) != {"total", "names", "glossary"}:
        raise ReadingPackError("invalid catalog context plan: summary is invalid")
    if summary != {
        "total": len(targets),
        "names": counts["names"],
        "glossary": counts["glossary"],
    }:
        raise ReadingPackError("invalid catalog context plan: summary is stale")
    return json.loads(json.dumps(value, ensure_ascii=False))


def write_catalog_context_plan(path: Path, plan: Mapping[str, Any]) -> None:
    checked = validate_catalog_context_plan(plan)
    path = Path(path).resolve()
    if path.exists() or path.is_symlink():
        raise ReadingPackError(f"refusing to overwrite catalog context plan: {path}", EXIT_IO)
    encoded = json.dumps(checked, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    if len(encoded) > MAX_CONTEXT_PLAN_BYTES:
        raise ReadingPackError(
            f"catalog context plan exceeds {MAX_CONTEXT_PLAN_BYTES} bytes", EXIT_IO
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor: int | None = None
    temporary: Path | None = None
    try:
        descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temporary = Path(name)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = None
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path, follow_symlinks=False)
        temporary.unlink()
        temporary = None
        path.chmod(0o600)
    except OSError as exc:
        raise ReadingPackError(f"cannot write catalog context plan {path}: {exc}", EXIT_IO) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load_catalog_context_plan(path: Path) -> dict[str, Any]:
    path = Path(path).resolve()
    try:
        if path.stat().st_size > MAX_CONTEXT_PLAN_BYTES:
            raise ReadingPackError(
                f"catalog context plan exceeds {MAX_CONTEXT_PLAN_BYTES} bytes", EXIT_IO
            )
        value = _strict_json(path.read_text(encoding="utf-8"))
    except ReadingPackError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ReadingPackError(f"cannot read catalog context plan {path}: {exc}", EXIT_IO) from exc
    return validate_catalog_context_plan(value)


def load_chapter_spans(path: Path) -> list[Mapping[str, Any]]:
    """Load a bounded explicit chapter map; source-bound checks occur on extraction."""

    path = Path(path).resolve()
    try:
        if path.stat().st_size > MAX_CHAPTER_MAP_BYTES:
            raise ReadingPackError(
                f"chapter map exceeds {MAX_CHAPTER_MAP_BYTES} bytes", EXIT_IO
            )
        value = _strict_json(path.read_text(encoding="utf-8"))
    except ReadingPackError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ReadingPackError(f"cannot read chapter map {path}: {exc}", EXIT_IO) from exc
    if isinstance(value, Mapping) and set(value) == {"chapter_spans"}:
        value = value["chapter_spans"]
    if (
        isinstance(value, (str, bytes))
        or not isinstance(value, list)
        or len(value) > MAX_CHAPTER_SPANS
        or not all(isinstance(item, Mapping) for item in value)
    ):
        raise ReadingPackError("chapter map must be an array of chapter span objects")
    return value


_GENERATED_RECORD_FIELDS = {
    "names": {"id", "name", "chapter_id", "status"},
    "glossary": {"id", "term", "chapter_id", "status"},
    "references": {"id", "url", "label", "status"},
}
_GENERATED_RECORD_IDS = {
    "names": re.compile(r"NAME-[A-Z0-9][A-Z0-9.-]*"),
    "glossary": re.compile(r"TERM-[A-Z0-9][A-Z0-9.-]*"),
    "references": re.compile(r"REF-[A-Z0-9][A-Z0-9.-]*"),
}
_GENERATED_SUPPORT_FIELDS = {
    "names": "name",
    "glossary": "term",
    "references": "url",
}


def _generated_values(responses: Any) -> list[Mapping[str, Any]]:
    value = responses
    if isinstance(value, str):
        try:
            value = _strict_json(value)
        except (json.JSONDecodeError, ValueError) as exc:
            raise ReadingPackError(
                f"generated catalog responses are invalid JSON: {exc}"
            ) from exc
    if isinstance(value, Mapping) and set(value) == {"candidates"}:
        value = value["candidates"]
    if (
        isinstance(value, (str, bytes))
        or not isinstance(value, Sequence)
        or len(value) > MAX_CATALOG_ITEMS
        or not all(isinstance(item, Mapping) for item in value)
    ):
        raise ReadingPackError(
            f"generated catalog responses must be an array of at most {MAX_CATALOG_ITEMS} candidates"
        )
    return list(value)


def _locate_generated_evidence(
    normalized_source: str, snippet: str, occurrence: int
) -> tuple[int, int, str]:
    support = normalize_text(snippet)
    if not MIN_GENERATED_EVIDENCE_CHARACTERS <= len(support) <= MAX_GENERATED_EVIDENCE_CHARACTERS:
        raise ReadingPackError("generated catalog evidence length is invalid")
    cursor = 0
    start = -1
    for _ in range(occurrence + 1):
        start = normalized_source.find(support, cursor)
        if start < 0:
            break
        cursor = start + len(support)
    if start < 0:
        raise ReadingPackError("generated catalog evidence is not in the source")
    return start, start + len(support), support


def _reference_supported_by(url: str, raw_snippet: str) -> bool:
    # URL path and query components can be case-sensitive. Require one complete
    # case-preserving URL token instead of a normalized substring.
    for match in _URL.finditer(unicodedata.normalize("NFKC", raw_snippet)):
        token = match.group().rstrip(".,;:!?)]}）】」』")
        if token == unicodedata.normalize("NFKC", url):
            return True
    parsed = urlparse(url)
    if parsed.netloc.casefold() in {"doi.org", "dx.doi.org"}:
        doi = normalize_text(parsed.path.lstrip("/"))
        support = normalize_text(raw_snippet)
        return bool(
            doi
            and _DOI.fullmatch(doi)
            and any(match.group().rstrip(".,;:!?)]}）】」』") == doi for match in _DOI.finditer(support))
        )
    return False


def _response_key(response: Mapping[str, Any]) -> tuple[str, str, str]:
    collection = str(response["collection"])
    record = response["record"]
    if collection == "names":
        return collection, normalize_text(record["name"]), record["chapter_id"]
    if collection == "glossary":
        return collection, normalize_text(record["term"]), ""
    return collection, normalize_text(record["url"]), ""


def validate_generated_catalog_responses(
    inventory: Mapping[str, Any],
    responses: Any,
    source_path: Path,
    *,
    collections: Iterable[str] = CATALOG_COLLECTIONS,
) -> list[dict[str, Any]]:
    """Validate model/NER additions against exact source and chapter spans.

    Generated records may improve multilingual recall, but they cannot invent a
    chapter assignment or use evidence from a different chapter.  The return
    value is still transient candidate input; it grants no review decision.
    """

    checked = validate_catalog_inventory(inventory)
    requested = list(dict.fromkeys(collections))
    if not requested or any(collection not in CATALOG_COLLECTIONS for collection in requested):
        raise ReadingPackError("catalog collections must be names, glossary, or references")
    if (
        {"names", "glossary"} & set(requested)
        and checked["chapter_map"]["method"] != "explicit"
    ):
        raise ReadingPackError(
            "inferred chapter spans must be reviewed and supplied as an explicit chapter map before indexing"
        )
    source = checked["source"]
    source_path = Path(source_path).resolve()
    fingerprint = fingerprint_source(source_path)
    if any(fingerprint[key] != source[key] for key in ("name", "sha256", "size_bytes")):
        raise ReadingPackError("catalog source is stale or mismatched")
    _, source_text = _source_text_snapshot(source_path, source_format=source["format"])
    normalized_source = normalize_text(source_text)
    if _content_hash(normalized_source) != checked["text_sha256"]:
        raise ReadingPackError("catalog normalized source hash is stale")
    spans = _recheck_inventory_spans(checked, normalized_source)
    compact_source, compact_offsets = _compact_view(normalized_source)
    seen_ids: set[str] = set()
    seen_records: set[tuple[str, str, str]] = set()
    result: list[dict[str, Any]] = []
    for index, raw in enumerate(_generated_values(responses)):
        if set(raw) != {"collection", "record", "evidence"}:
            raise ReadingPackError(
                f"generated catalog response {index} has missing or unexpected fields"
            )
        collection = raw["collection"]
        if collection not in requested:
            raise ReadingPackError(
                f"generated catalog response {index} uses an unrequested collection"
            )
        record = raw["record"]
        expected_fields = _GENERATED_RECORD_FIELDS[collection]
        if not isinstance(record, Mapping) or set(record) != expected_fields:
            raise ReadingPackError(
                f"generated catalog response {index} record fields are invalid"
            )
        record_id = record["id"]
        if (
            not isinstance(record_id, str)
            or not _GENERATED_RECORD_IDS[collection].fullmatch(record_id)
            or record_id in seen_ids
            or record.get("status") != "draft"
        ):
            raise ReadingPackError(
                f"generated catalog response {index} record ID or status is invalid"
            )
        seen_ids.add(record_id)
        support_field = _GENERATED_SUPPORT_FIELDS[collection]
        value = record[support_field]
        if collection == "references":
            if not _safe_label(value, 2_048) or not _safe_label(record["label"], 500):
                raise ReadingPackError(
                    f"generated catalog response {index} content is invalid"
                )
        elif not _safe_label(value):
            raise ReadingPackError(
                f"generated catalog response {index} content is invalid"
            )
        chapter_id = ""
        if collection in {"names", "glossary"}:
            chapter_id = record["chapter_id"]
            if chapter_id not in spans:
                raise ReadingPackError(
                    f"generated catalog response {index} chapter is not source-mapped"
                )
        else:
            parsed = urlparse(value)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ReadingPackError(
                    f"generated catalog response {index} URL is invalid"
                )
        evidence = raw["evidence"]
        if (
            not isinstance(evidence, list)
            or not evidence
            or len(evidence) > MAX_GENERATED_EVIDENCE_PER_ITEM
        ):
            raise ReadingPackError(
                f"generated catalog response {index} evidence is invalid"
            )
        for evidence_item in evidence:
            if (
                not isinstance(evidence_item, Mapping)
                or set(evidence_item) - {"snippet", "occurrence", "supports_field"}
                or not isinstance(evidence_item.get("snippet"), str)
                or evidence_item.get("supports_field") != support_field
            ):
                raise ReadingPackError(
                    f"generated catalog response {index} has invalid field-bound evidence"
                )
            occurrence = evidence_item.get("occurrence", 0)
            if (
                not isinstance(occurrence, int)
                or isinstance(occurrence, bool)
                or not 0 <= occurrence <= MAX_GENERATED_EVIDENCE_OCCURRENCE
            ):
                raise ReadingPackError(
                    f"generated catalog response {index} evidence occurrence is invalid"
                )
            start, end, support = _locate_generated_evidence(
                normalized_source, evidence_item["snippet"], occurrence
            )
            if chapter_id:
                span = spans[chapter_id]
                if not span["char_start"] <= start < end <= span["char_end"]:
                    raise ReadingPackError(
                        f"generated catalog response {index} evidence is outside its chapter"
                    )
                direct = _exact_source_term(value, support)
            else:
                direct = _reference_supported_by(value, evidence_item["snippet"])
            if not direct:
                raise ReadingPackError(
                    f"generated catalog response {index} evidence does not contain its indexed value"
                )
        if collection == "glossary":
            preferred = _preferred_term_occurrence(
                _find_compact_occurrences(
                    value,
                    compact_source,
                    compact_offsets,
                    normalized_source,
                    list(spans.values()),
                ),
                normalized_source,
            )
            if preferred is None or preferred["chapter_id"] != chapter_id:
                raise ReadingPackError(
                    f"generated catalog response {index} does not use the preferred substantive term chapter"
                )
        copied = json.loads(json.dumps(raw, ensure_ascii=False))
        key = _response_key(copied)
        if key in seen_records:
            raise ReadingPackError(
                f"generated catalog response {index} duplicates an indexed value"
            )
        seen_records.add(key)
        result.append(copied)
    return result


def validate_catalog_context_responses(
    plan: Mapping[str, Any],
    responses: Any,
    source_path: Path,
    canonical_data: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Bind one concise description for every target to exact chapter evidence."""

    checked = validate_catalog_context_plan(plan)
    if not isinstance(canonical_data, Mapping):
        raise ReadingPackError("catalog context canonical data must be an object")
    if _value_hash(canonical_data) != checked["canonical_data_sha256"]:
        raise ReadingPackError("canonical data changed after the catalog context plan")
    source_path = Path(source_path).resolve()
    fingerprint = fingerprint_source(source_path)
    if any(
        fingerprint[key] != checked["source"][key]
        for key in ("name", "sha256", "size_bytes")
    ):
        raise ReadingPackError("catalog context source is stale or mismatched")
    _, source_text = _source_text_snapshot(
        source_path, source_format=checked["source"]["format"]
    )
    normalized_source = normalize_text(source_text)
    if _content_hash(normalized_source) != checked["text_sha256"]:
        raise ReadingPackError("catalog context normalized source hash is stale")
    spans: dict[str, Mapping[str, Any]] = {}
    for span in checked["chapter_spans"]:
        start, end = span["char_start"], span["char_end"]
        if (
            end > len(normalized_source)
            or _content_hash(normalized_source[start:end]) != span["span_sha256"]
        ):
            raise ReadingPackError("catalog context chapter span no longer matches the source")
        spans[span["chapter_id"]] = span

    value = responses
    if isinstance(value, str):
        try:
            value = _strict_json(value)
        except (json.JSONDecodeError, ValueError) as exc:
            raise ReadingPackError(f"catalog context responses are invalid JSON: {exc}") from exc
    require_structure(
        "catalog-context-responses.schema.json",
        value,
        label="catalog context responses",
    )
    if not isinstance(value, Mapping) or set(value) != {"plan_id", "candidates"}:
        raise ReadingPackError("catalog context responses have invalid root fields")
    if value["plan_id"] != checked["plan_id"]:
        raise ReadingPackError("catalog context responses are bound to another plan")
    items = value["candidates"]
    if (
        not isinstance(items, list)
        or len(items) != len(checked["targets"])
        or len(items) > MAX_CATALOG_ITEMS
    ):
        raise ReadingPackError(
            "catalog context responses must cover every plan target exactly once"
        )
    target_by_id = {target["record_id"]: target for target in checked["targets"]}
    canonical_by_id: dict[tuple[str, str], Mapping[str, Any]] = {}
    for collection in _CONTEXT_FIELDS:
        for record in canonical_data.get(collection, []):
            if isinstance(record, Mapping) and isinstance(record.get("id"), str):
                canonical_by_id[(collection, record["id"])] = record
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        if not isinstance(item, Mapping) or set(item) != {
            "record_id", "description", "evidence"
        }:
            raise ReadingPackError(
                f"catalog context response {index} has invalid fields"
            )
        record_id = item["record_id"]
        target = target_by_id.get(record_id) if isinstance(record_id, str) else None
        if target is None or record_id in seen:
            raise ReadingPackError(
                f"catalog context response {index} target is unknown or duplicated"
            )
        seen.add(record_id)
        description = item["description"]
        if not _safe_label(description, 500):
            raise ReadingPackError(
                f"catalog context response {index} description is invalid"
            )
        evidence = item["evidence"]
        if (
            not isinstance(evidence, list)
            or not evidence
            or len(evidence) > MAX_GENERATED_EVIDENCE_PER_ITEM
        ):
            raise ReadingPackError(
                f"catalog context response {index} evidence is invalid"
            )
        value_supported = False
        checked_evidence: list[dict[str, Any]] = []
        for evidence_item in evidence:
            if (
                not isinstance(evidence_item, Mapping)
                or set(evidence_item) - {"snippet", "occurrence", "supports_field"}
                or not isinstance(evidence_item.get("snippet"), str)
                or evidence_item.get("supports_field") != target["context_field"]
            ):
                raise ReadingPackError(
                    f"catalog context response {index} has invalid field-bound evidence"
                )
            occurrence = evidence_item.get("occurrence", 0)
            if (
                not isinstance(occurrence, int)
                or isinstance(occurrence, bool)
                or not 0 <= occurrence <= MAX_GENERATED_EVIDENCE_OCCURRENCE
            ):
                raise ReadingPackError(
                    f"catalog context response {index} evidence occurrence is invalid"
                )
            start, end, support = _locate_generated_evidence(
                normalized_source, evidence_item["snippet"], occurrence
            )
            span = spans[target["chapter_id"]]
            if not span["char_start"] <= start < end <= span["char_end"]:
                raise ReadingPackError(
                    f"catalog context response {index} evidence is outside its chapter"
                )
            value_supported = value_supported or _exact_source_term(
                target["value"], support
            )
            checked_evidence.append(
                {
                    "snippet": evidence_item["snippet"],
                    "occurrence": occurrence,
                    "supports_field": target["context_field"],
                }
            )
        if not value_supported:
            raise ReadingPackError(
                f"catalog context response {index} evidence does not identify its target"
            )
        base = canonical_by_id.get((target["collection"], record_id))
        if base is None or _value_hash(base) != target["base_record_sha256"]:
            raise ReadingPackError(
                f"catalog context response {index} target is stale"
            )
        record = json.loads(json.dumps(base, ensure_ascii=False))
        record[target["context_field"]] = description
        record["status"] = "draft"
        result.append(
            {
                "collection": target["collection"],
                "record": record,
                "evidence": checked_evidence,
            }
        )
    if seen != set(target_by_id):
        raise ReadingPackError("catalog context responses omit one or more plan targets")
    return result


def create_catalog_context_candidate_run(
    project: Path,
    *,
    language: str,
    plan: Mapping[str, Any],
    source_path: Path,
    responses: Any,
    run_directory: Path,
    run_id: str | None = None,
) -> Path:
    """Create ordinary update candidates from a complete context response set."""

    project = Path(project).resolve()
    checked = validate_catalog_context_plan(plan)
    config = load_config(project)
    if language not in config.get("languages", []) or checked["language"] != language:
        raise ReadingPackError("catalog context plan language is not configured")
    data_by_lang = {
        lang: load_language_data(project, lang) for lang in config.get("languages", [])
    }
    canonical = data_by_lang[language]
    transient = validate_catalog_context_responses(
        checked, responses, source_path, canonical
    )
    if not transient:
        raise ReadingPackError("catalog context plan has no missing descriptions")
    return create_candidate_run(
        run_directory,
        source_path=Path(source_path),
        responses=transient,
        language=language,
        canonical_data=canonical,
        project_data_by_lang=data_by_lang,
        known_chapter_ids={chapter["id"] for chapter in canonical["chapters"]},
        run_id=run_id,
        generator={
            "adapter": "catalog-context-json",
            "model": "",
            "revision": f"1:{checked['plan_id']}",
            "settings_hash": checked["integrity_sha256"],
        },
    )


def _evidence_snippet(
    normalized_source: str,
    occurrence: Mapping[str, Any],
    *,
    chapter_span: Mapping[str, Any] | None = None,
) -> tuple[str, int]:
    start = occurrence["char_start"]
    end = occurrence["char_end"]
    lower = chapter_span["char_start"] if chapter_span is not None else 0
    upper = chapter_span["char_end"] if chapter_span is not None else len(normalized_source)
    left = max(lower, start - 48)
    right = min(upper, end + 48)
    window = normalized_source[left:right]
    leading = len(window) - len(window.lstrip())
    snippet_start = left + leading
    snippet = window.strip()
    if len(snippet) < 8:
        left = max(lower, start - 8)
        window = normalized_source[left : min(upper, end + 8)]
        leading = len(window) - len(window.lstrip())
        snippet_start = left + leading
        snippet = window.strip()
    snippet = snippet[:500]
    cursor = 0
    occurrence_index = 0
    while True:
        located = normalized_source.find(snippet, cursor)
        if located < 0:
            raise ReadingPackError("internal catalog evidence snippet cannot be located")
        if located == snippet_start:
            break
        if located > snippet_start:
            raise ReadingPackError("internal catalog evidence occurrence is inconsistent")
        occurrence_index += 1
        if occurrence_index > MAX_GENERATED_EVIDENCE_OCCURRENCE:
            raise ReadingPackError("catalog evidence occurrence exceeds the safe limit")
        cursor = located + len(snippet)
    return snippet, occurrence_index


def _recheck_inventory_spans(
    checked: Mapping[str, Any], normalized_source: str
) -> dict[str, Mapping[str, Any]]:
    spans_by_id: dict[str, Mapping[str, Any]] = {}
    for span in checked["chapter_spans"]:
        start, end = span["char_start"], span["char_end"]
        if (
            end > len(normalized_source)
            or _content_hash(normalized_source[start:end]) != span["span_sha256"]
        ):
            raise ReadingPackError("catalog chapter span no longer matches the source")
        spans_by_id[span["chapter_id"]] = span
    return spans_by_id


def catalog_candidate_responses(
    inventory: Mapping[str, Any],
    source_path: Path,
    *,
    collections: Iterable[str] = CATALOG_COLLECTIONS,
) -> list[dict[str, Any]]:
    """Rehydrate a private inventory into standard transient candidate input."""

    checked = validate_catalog_inventory(inventory)
    requested = list(dict.fromkeys(collections))
    if not requested or any(collection not in CATALOG_COLLECTIONS for collection in requested):
        raise ReadingPackError("catalog collections must be names, glossary, or references")
    source = checked["source"]
    source_path = Path(source_path).resolve()
    fingerprint = fingerprint_source(source_path)
    if any(fingerprint[key] != source[key] for key in ("name", "sha256", "size_bytes")):
        raise ReadingPackError("catalog source is stale or mismatched")
    _, source_text = _source_text_snapshot(source_path, source_format=source["format"])
    normalized_source = normalize_text(source_text)
    if _content_hash(normalized_source) != checked["text_sha256"]:
        raise ReadingPackError("catalog normalized source hash is stale")
    spans_by_id = _recheck_inventory_spans(checked, normalized_source)
    responses: list[dict[str, Any]] = []
    for item in checked["items"]:
        collection = {"person": "names", "term": "glossary", "reference": "references"}[item["kind"]]
        if collection not in requested:
            continue
        chapter_id = item["chapter"]["chapter_id"]
        if collection in {"names", "glossary"} and not chapter_id:
            continue
        occurrence = item["occurrences"][0]
        start, end = occurrence["char_start"], occurrence["char_end"]
        if end > len(normalized_source) or _content_hash(normalized_source[start:end]) != occurrence["span_sha256"]:
            raise ReadingPackError("catalog occurrence no longer matches the source")
        if chapter_id:
            span = spans_by_id[chapter_id]
            if not span["char_start"] <= start < end <= span["char_end"]:
                raise ReadingPackError("catalog occurrence lies outside its bound chapter")
        digest = hashlib.sha256(
            f"{collection}\0{normalize_text(item['label'])}\0{chapter_id}\0{item['url']}".encode()
        ).hexdigest()[:16].upper()
        if collection == "names":
            record = {
                "id": f"NAME-AUTO-{digest}",
                "name": item["label"],
                "chapter_id": chapter_id,
                "status": "draft",
            }
            supports = "name"
        elif collection == "glossary":
            record = {
                "id": f"TERM-AUTO-{digest}",
                "term": item["label"],
                "chapter_id": chapter_id,
                "status": "draft",
            }
            supports = "term"
        else:
            record = {
                "id": f"REF-AUTO-{digest}",
                "url": item["url"],
                "label": item["label"],
                "status": "draft",
            }
            supports = "url"
        snippet, evidence_occurrence = _evidence_snippet(
            normalized_source,
            occurrence,
            chapter_span=spans_by_id.get(chapter_id),
        )
        responses.append(
            {
                "collection": collection,
                "record": record,
                "evidence": [
                    {
                        "snippet": snippet,
                        "occurrence": evidence_occurrence,
                        "supports_field": supports,
                    }
                ],
            }
        )
    return responses


def create_catalog_candidate_run(
    project: Path,
    *,
    language: str,
    inventory: Mapping[str, Any],
    source_path: Path,
    run_directory: Path,
    run_id: str | None = None,
    collections: Iterable[str] = CATALOG_COLLECTIONS,
    ledger_output: Path | None = None,
    generated_responses: Any | None = None,
) -> tuple[Path, Path | None]:
    """Create one combined catalog run and optional reconciled coverage ledger."""

    project = Path(project).resolve()
    checked = validate_catalog_inventory(inventory)
    config = load_config(project)
    if language not in config.get("languages", []) or checked["language"] != language:
        raise ReadingPackError("catalog inventory language is not configured")
    data_by_lang = {
        lang: load_language_data(project, lang) for lang in config.get("languages", [])
    }
    canonical = data_by_lang[language]
    if _value_hash(canonical) != checked["canonical_data_sha256"]:
        raise ReadingPackError("canonical data changed after catalog extraction")
    requested = list(dict.fromkeys(collections))
    if (
        {"names", "glossary"} & set(requested)
        and checked["chapter_map"]["method"] != "explicit"
    ):
        raise ReadingPackError(
            "inferred chapter spans must be reviewed and supplied as an explicit chapter map before indexing"
        )
    responses = catalog_candidate_responses(checked, source_path, collections=requested)
    if generated_responses is not None:
        generated = validate_generated_catalog_responses(
            checked,
            generated_responses,
            source_path,
            collections=requested,
        )
        seen = {_response_key(response) for response in responses}
        for response in generated:
            key = _response_key(response)
            if key not in seen:
                responses.append(response)
                seen.add(key)
    support_source = None
    if checked["source"]["id"] != "SRC-1":
        support_source = registered_source(project, checked["source"]["id"])
        if any(
            support_source.get(key) != checked["source"].get(key)
            for key in (
                "id", "role", "language", "format", "name", "sha256", "size_bytes"
            )
        ):
            raise ReadingPackError(
                "registered catalog source identity changed after extraction"
            )
    manifest_path = create_candidate_run(
        run_directory,
        source_path=Path(source_path),
        responses=responses,
        language=language,
        canonical_data=canonical,
        project_data_by_lang=data_by_lang,
        known_chapter_ids={chapter["id"] for chapter in canonical["chapters"]},
        run_id=run_id,
        generator={
            "adapter": (
                checked["extractor"]
                if generated_responses is None
                else f"{checked['extractor']}+catalog-generated-json"
            ),
            "model": "",
            "revision": f"1:{checked['inventory_id']}",
            "settings_hash": checked["integrity_sha256"],
        },
        support_source=support_source,
    )
    if ledger_output is None:
        return manifest_path, None
    manifest = load_candidate_run(manifest_path)
    modules = [collection for collection in CATALOG_COLLECTIONS if collection in requested]
    ledger = create_work_ledger(
        language=language,
        canonical_data=canonical,
        modules=modules,
    )
    candidates = manifest["candidates"]
    results: list[dict[str, Any]] = []
    for work in ledger["items"]:
        module = work["module"]
        chapter_id = work["scope"].get("chapter_id", "")
        matching = [
            candidate
            for candidate in candidates
            if candidate["collection"] == module
            and (
                module == "references"
                or candidate.get("record", {}).get("chapter_id") == chapter_id
            )
        ]
        ready = [candidate["candidate_id"] for candidate in matching if candidate["candidate_state"] == "ready_for_review"]
        if ready:
            status, reason = "complete", ""
        elif matching:
            status, reason = "failed", "catalog_candidates_quarantined"
        else:
            # A detector returning no match is not evidence that the book has
            # no person, term, or reference for this scope. Keep the omission
            # visible until an explicit worker/human outcome says otherwise.
            status, reason = "failed", "catalog_no_match_requires_omission_review"
        results.append(
            {
                "work_id": work["work_id"],
                "status": status,
                "reason_code": reason,
                "candidate_ids": ready,
            }
        )
    reconciled = reconcile_work_results(
        ledger,
        {
            "schema_version": 1,
            "plan_id": ledger["plan_id"],
            "run_id": manifest["run_id"],
            "results": results,
        },
        manifest,
    )
    write_work_ledger(Path(ledger_output), reconciled)
    return manifest_path, Path(ledger_output).resolve()
