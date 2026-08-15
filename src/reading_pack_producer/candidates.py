"""Private, source-grounded candidate runs.

This module deliberately keeps model/manual candidates outside canonical pack data.
Evidence snippets are accepted only as transient input: finalized run manifests retain
rehydratable offsets and hashes, never the snippets themselves.  Applying a run is a
separate operation and can only write ``draft`` records.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import selectors
import stat
import subprocess
import tempfile
import time
import unicodedata
import uuid
from collections import Counter
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlparse

from reading_pack.errors import EXIT_IO, ReadingPackError
from reading_pack.hashing import file_hash, semantic_hash
from reading_pack.importers import extract_epub_authorized_text, extract_pdf_authorized_text
from reading_pack.project import load_config, load_language_data, project_lock, write_json
from reading_pack.schema_validation import require_structure
from reading_pack.validation import errors, validate_data_set


RUN_SCHEMA_VERSION = 1
NORMALIZATION = "nfkc-casefold-whitespace-v1"
JSON_STRING_NORMALIZATION = "json-decoded-strings-nfkc-casefold-whitespace-v1"
SUPPORTED_NORMALIZATIONS = {NORMALIZATION, JSON_STRING_NORMALIZATION}
MANIFEST_NAME = "manifest.json"
MAX_EVIDENCE_CHARACTERS = 500
MAX_SOURCE_BYTES = 100 * 1024 * 1024
MAX_AUTHORIZED_TEXT_CHARACTERS = 50 * 1024 * 1024
MAX_JSON_POINTER_CHARACTERS = 4_096
MAX_JSON_DEPTH = 128
MAX_CANDIDATES = 2_000
MAX_EVIDENCE_PER_CANDIDATE = 20
MAX_EVIDENCE_OCCURRENCE = 10_000
MAX_RUN_EVIDENCE_REFS = 10_000
MAX_RUN_EVIDENCE_MATCH_WORK = 100_000
MIN_EVIDENCE_CHARACTERS = 8
MAX_EXTRACTIVE_CHARACTERS_PER_RECORD = 1_000
MAX_EXTRACTIVE_STRINGS_PER_RECORD = 200
MAX_RECORD_BYTES = 64 * 1024
MAX_MANIFEST_BYTES = 32 * 1024 * 1024
MAX_ADAPTER_INPUT_BYTES = 1024 * 1024
MAX_ADAPTER_OUTPUT_BYTES = 16 * 1024 * 1024
MAX_ADAPTER_ERROR_BYTES = 64 * 1024
MAX_ADAPTER_TIMEOUT_SECONDS = 600.0
MAX_AI_REVIEW_BYTES = 4 * 1024 * 1024

REVIEWER_TYPES = {"human", "ai"}
AI_REVIEW_CHECKS = {
    "source_support",
    "semantic_fidelity",
    "scope_and_qualification",
}

CANDIDATE_STATES = {
    "proposed",
    "quarantined",
    "ready_for_review",
    "accepted",
    "applying",
    "rejected",
    "applied",
}

COLLECTIONS = (
    "chapters",
    "certainty",
    "claims",
    "misreadings",
    "policies",
    "names",
    "glossary",
    "references",
)

ID_PATTERNS = {
    "chapters": r"CH-[A-Z0-9][A-Z0-9.-]*",
    "certainty": r"CERT-[A-Z0-9][A-Z0-9.-]*",
    "claims": r"(?:CL|PROP)-[A-Z0-9][A-Z0-9.-]*",
    "misreadings": r"MIS-[A-Z0-9][A-Z0-9.-]*",
    "policies": r"POLICY-[A-Z0-9][A-Z0-9.-]*",
    "names": r"NAME-[A-Z0-9][A-Z0-9.-]*",
    "glossary": r"TERM-[A-Z0-9][A-Z0-9.-]*",
    "references": r"REF-[A-Z0-9][A-Z0-9.-]*",
}

REQUIRED_FIELDS = {
    "chapters": {"id", "title", "sections", "summary", "terms", "status"},
    "certainty": {"id", "label", "definition", "status"},
    "claims": {"id", "layer", "kind", "statement", "chapter_ids", "status"},
    "misreadings": {"id", "response", "chapter_ids", "status"},
    "policies": {"id", "kind", "statement", "status"},
    "names": {"id", "name", "chapter_id", "status"},
    "glossary": {"id", "term", "chapter_id", "status"},
    "references": {"id", "url", "label", "status"},
}

ALLOWED_FIELDS = {
    "chapters": {
        "id", "kind", "title", "pages", "sections", "summary", "terms", "status",
        "contributors", "aliases", "learning_objectives", "prerequisites",
        "spoiler_scope", "source_locations",
    },
    "certainty": {
        "id", "label", "definition", "source_locations", "status",
    },
    "claims": {
        "id", "layer", "kind", "statement", "chapter_ids", "certainty_id",
        "falsifiability", "revision_conditions", "source_locations",
        "reader_note", "status",
    },
    "misreadings": {
        "id", "kind", "issue", "misreading", "response", "impact",
        "remaining_uncertainty", "chapter_ids", "claim_ids", "anchor",
        "source_locations", "status",
    },
    "policies": {
        "id", "kind", "statement", "source_locations", "status",
    },
    "names": {
        "id", "name", "aliases", "chapter_id", "book_context", "source_locations",
        "status",
    },
    "glossary": {
        "id", "term", "aliases", "chapter_id", "book_meaning", "source_locations",
        "status",
    },
    "references": {
        "id", "url", "label", "source_locations", "status",
    },
}

CONTENT_FIELDS = {
    "chapters": ("title",),
    "certainty": ("label", "definition"),
    "claims": ("kind", "statement"),
    "misreadings": ("response",),
    "policies": ("kind", "statement"),
    "names": ("name",),
    "glossary": ("term",),
    "references": ("label", "url"),
}

LIST_FIELDS = {
    "chapters": (
        "sections",
        "terms",
    ),
    "claims": ("chapter_ids",),
    "misreadings": ("chapter_ids",),
    "policies": (),
}

OPTIONAL_LIST_FIELDS = {
    "chapters": (
        "contributors",
        "aliases",
        "learning_objectives",
        "prerequisites",
        "source_locations",
    ),
    "certainty": ("source_locations",),
    "claims": ("source_locations",),
    "misreadings": ("claim_ids", "source_locations"),
    "policies": ("source_locations",),
    "names": ("aliases", "source_locations"),
    "glossary": ("aliases", "source_locations"),
    "references": ("source_locations",),
}

CHAPTER_STRUCTURAL_FIELDS = ("id", "kind", "title", "pages", "sections")
CHAPTER_EDITABLE_FIELDS = (
    "summary",
    "terms",
    "contributors",
    "aliases",
    "learning_objectives",
    "prerequisites",
    "spoiler_scope",
    "source_locations",
)

STRING_LIMITS = {
    "chapters": {
        "title": 500,
        "pages": 100,
        "summary": 500,
        "spoiler_scope": 32,
    },
    "certainty": {"label": 200, "definition": 1_000},
    "claims": {
        "kind": 100,
        "statement": 1_000,
        "certainty_id": 100,
        "falsifiability": 1_000,
        "revision_conditions": 1_000,
    },
    "misreadings": {
        "kind": 32,
        "misreading": 1_000,
        "response": 1_000,
        "impact": 1_000,
        "remaining_uncertainty": 1_000,
        "issue": 1_000,
    },
    "policies": {"kind": 100, "statement": 2_000},
    "names": {"name": 200, "chapter_id": 100, "book_context": 500},
    "glossary": {"term": 200, "chapter_id": 100, "book_meaning": 500},
    "references": {"url": 2_048, "label": 500},
}

LIST_LIMITS = {
    "chapters": {
        "sections": (500, 500),
        "terms": (500, 200),
        "contributors": (200, 200),
        "aliases": (500, 200),
        "learning_objectives": (500, 500),
        "prerequisites": (500, 500),
        "source_locations": (500, 500),
    },
    "misreadings": {
        "chapter_ids": (500, 100),
        "claim_ids": (500, 100),
        "source_locations": (500, 500),
    },
    "certainty": {"source_locations": (500, 500)},
    "claims": {"chapter_ids": (500, 100), "source_locations": (500, 500)},
    "policies": {"source_locations": (500, 500)},
    "names": {"aliases": (500, 200), "source_locations": (500, 500)},
    "glossary": {"aliases": (500, 200), "source_locations": (500, 500)},
    "references": {"source_locations": (500, 500)},
}

UNSAFE_TEXT = re.compile(
    r"[\x00-\x1f\x7f-\x9f\ud800-\udfff\u202a-\u202e\u2066-\u2069]"
)


@dataclass(frozen=True)
class LeakPolicy:
    """Deterministic verbatim-copy limit for prose-bearing candidate fields."""

    max_contiguous_characters: int = 160

    def __post_init__(self) -> None:
        if self.max_contiguous_characters < 64:
            raise ValueError("max_contiguous_characters must be at least 64")


class _SourceCopyIndex:
    """Bounded rolling-hash index that cannot miss a policy-length copy.

    Source windows are sampled every half-policy window. Any copied interval at
    least as long as the policy necessarily contains one complete sampled
    source window. Candidate windows are scanned at every position. Hash
    collisions can only quarantine extra material; they cannot let a copy pass.
    """

    _BASE = 1_000_003
    _MASK = (1 << 64) - 1

    def __init__(self, normalized_source: str, policy: LeakPolicy) -> None:
        self.window = max(1, policy.max_contiguous_characters // 2)
        self._source_hashes = {
            value
            for position, value in self._rolling_hashes(normalized_source)
            if position % self.window == 0
        }

    def _rolling_hashes(self, value: str) -> Iterable[tuple[int, int]]:
        width = self.window
        if len(value) < width:
            return
        modulus = 1 << 64
        high = pow(self._BASE, width - 1, modulus)
        current = 0
        for character in value[:width]:
            current = ((current * self._BASE) + ord(character)) & self._MASK
        yield 0, current
        for end in range(width, len(value)):
            outgoing = ord(value[end - width])
            current = (current - outgoing * high) & self._MASK
            current = ((current * self._BASE) + ord(value[end])) & self._MASK
            yield end - width + 1, current

    def contains_policy_copy(self, value: str) -> bool:
        return any(
            digest in self._source_hashes
            for _, digest in self._rolling_hashes(value)
        )


def _extractive_budget_exceeded(
    collection: str,
    record: Mapping[str, Any],
    normalized_source: str,
) -> bool:
    total = 0
    values = list(_record_strings(record, collection=collection))
    if len(values) > MAX_EXTRACTIVE_STRINGS_PER_RECORD:
        return True
    for value in values:
        candidate = normalize_text(value)
        if len(candidate) >= MIN_EVIDENCE_CHARACTERS and candidate in normalized_source:
            total += len(candidate)
            if total > MAX_EXTRACTIVE_CHARACTERS_PER_RECORD:
                return True
    return False


def normalize_text(value: str) -> str:
    """Return the stable representation used for evidence offsets and matching."""

    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value).casefold()).strip()


def _stop_adapter(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        if os.name == "posix":
            try:
                os.killpg(process.pid, 9)
            except (OSError, ProcessLookupError):
                process.kill()
        else:
            process.kill()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def run_local_adapter(
    command: Sequence[str],
    request: Any,
    timeout: float = 60.0,
    max_output: int = 2 * 1024 * 1024,
) -> Any:
    """Exchange one bounded JSON request with a local subprocess.

    The adapter reads one JSON value from stdin and writes one JSON value to
    stdout.  A shell is never invoked, inherited environment variables are
    reduced to a non-credential allowlist, output is consumed incrementally,
    and stderr content is never echoed because it may contain source prose.
    """

    if (
        isinstance(command, (str, bytes))
        or not isinstance(command, Sequence)
        or not command
        or not all(isinstance(part, str) and part and "\x00" not in part for part in command)
    ):
        raise ReadingPackError("local adapter command must be a non-empty argument array")
    if not isinstance(timeout, (int, float)) or timeout <= 0 or timeout > MAX_ADAPTER_TIMEOUT_SECONDS:
        raise ReadingPackError(
            f"local adapter timeout must be between 0 and {MAX_ADAPTER_TIMEOUT_SECONDS} seconds"
        )
    if not isinstance(max_output, int) or max_output < 1 or max_output > MAX_ADAPTER_OUTPUT_BYTES:
        raise ReadingPackError(
            f"local adapter max_output must be between 1 and {MAX_ADAPTER_OUTPUT_BYTES} bytes"
        )
    try:
        payload = _json_bytes(request) + b"\n"
    except (TypeError, ValueError) as exc:
        raise ReadingPackError(f"local adapter request is not JSON-serializable: {exc}") from exc
    if len(payload) > MAX_ADAPTER_INPUT_BYTES:
        raise ReadingPackError(
            f"local adapter request exceeds {MAX_ADAPTER_INPUT_BYTES} bytes"
        )

    environment = {
        key: value
        for key, value in os.environ.items()
        if key in {"PATH", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT", "WINDIR"}
    }
    environment.update(
        {
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PYTHONIOENCODING": "utf-8",
            "READING_PACK_ADAPTER": "1",
        }
    )
    try:
        process = subprocess.Popen(
            list(command),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            env=environment,
            start_new_session=os.name == "posix",
        )
    except OSError as exc:
        raise ReadingPackError(f"cannot start local adapter: {exc}", EXIT_IO) from exc

    assert process.stdin is not None
    assert process.stdout is not None
    assert process.stderr is not None
    streams = selectors.DefaultSelector()
    streams.register(process.stdin, selectors.EVENT_WRITE, "stdin")
    streams.register(process.stdout, selectors.EVENT_READ, "stdout")
    streams.register(process.stderr, selectors.EVENT_READ, "stderr")
    captured = {"stdout": bytearray(), "stderr": bytearray()}
    limits = {"stdout": max_output, "stderr": MAX_ADAPTER_ERROR_BYTES}
    input_offset = 0
    deadline = time.monotonic() + float(timeout)
    try:
        while streams.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _stop_adapter(process)
                raise ReadingPackError(f"local adapter timed out after {timeout} seconds", EXIT_IO)
            events = streams.select(timeout=min(remaining, 0.25))
            if not events and process.poll() is not None:
                # A dead child may leave stdin registered without another event.
                try:
                    streams.unregister(process.stdin)
                    process.stdin.close()
                except (KeyError, OSError):
                    pass
                continue
            for key, _ in events:
                label = key.data
                if label == "stdin":
                    try:
                        written = os.write(key.fileobj.fileno(), payload[input_offset : input_offset + 64 * 1024])
                    except BrokenPipeError:
                        written = 0
                    input_offset += written
                    if written == 0 or input_offset >= len(payload):
                        streams.unregister(key.fileobj)
                        key.fileobj.close()
                    continue
                chunk = os.read(key.fileobj.fileno(), 64 * 1024)
                if not chunk:
                    streams.unregister(key.fileobj)
                    key.fileobj.close()
                    continue
                target = captured[label]
                if len(target) + len(chunk) > limits[label]:
                    _stop_adapter(process)
                    raise ReadingPackError(
                        f"local adapter {label} exceeds {limits[label]} bytes",
                        EXIT_IO,
                    )
                target.extend(chunk)
        remaining = max(0.01, deadline - time.monotonic())
        try:
            returncode = process.wait(timeout=remaining)
        except subprocess.TimeoutExpired as exc:
            _stop_adapter(process)
            raise ReadingPackError(f"local adapter timed out after {timeout} seconds", EXIT_IO) from exc
    finally:
        streams.close()
        if process.poll() is None:
            _stop_adapter(process)
        for stream in (process.stdin, process.stdout, process.stderr):
            if not stream.closed:
                stream.close()

    if returncode != 0:
        stderr_hash = hashlib.sha256(captured["stderr"]).hexdigest()
        raise ReadingPackError(
            f"local adapter exited with status {returncode} (stderr sha256 {stderr_hash})",
            EXIT_IO,
        )
    try:
        return json.loads(bytes(captured["stdout"]).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReadingPackError("local adapter did not return one valid UTF-8 JSON value", EXIT_IO) from exc


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _strict_json_loads(value: str) -> Any:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = item
        return result

    return json.loads(
        value,
        object_pairs_hook=reject_duplicates,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("non-finite JSON number")),
    )


def _value_hash(value: Any) -> str:
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _manifest_integrity(manifest: Mapping[str, Any]) -> str:
    content = {key: value for key, value in manifest.items() if key != "integrity_sha256"}
    return _value_hash(content)


def _read_source(source_path: Path) -> tuple[bytes, os.stat_result]:
    descriptor: int | None = None
    try:
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(source_path, flags)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ReadingPackError(
                "candidate source is not a regular file", EXIT_IO
            )
        if before.st_size > MAX_SOURCE_BYTES:
            raise ReadingPackError(
                f"candidate source exceeds {MAX_SOURCE_BYTES} bytes", EXIT_IO
            )
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, MAX_SOURCE_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_SOURCE_BYTES:
                raise ReadingPackError(
                    f"candidate source exceeds {MAX_SOURCE_BYTES} bytes", EXIT_IO
                )
        raw = b"".join(chunks)
        after = source_path.stat()
    except OSError as exc:
        raise ReadingPackError(f"cannot read candidate source {source_path}: {exc}", EXIT_IO) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    if identity_before != identity_after:
        raise ReadingPackError("candidate source changed while it was being read", EXIT_IO)
    return raw, after


def json_string_values_view(value: str) -> tuple[str, dict[str, tuple[int, int]]]:
    """Return a deterministic decoded-string view and JSON-value spans.

    Candidate evidence and copy checks must inspect the same representation.
    Using decoded string values prevents ``\\uXXXX`` spelling from hiding a
    verbatim copy from the leak policy. Object keys are included too because a
    generic support source may carry content in keys. JSON Pointer markers make
    equal values at different paths independently addressable.
    """

    try:
        parsed = _strict_json_loads(value)
    except (json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise ReadingPackError(f"candidate JSON source is invalid: {exc}", EXIT_IO) from exc
    chunks: list[str] = []
    spans: dict[str, tuple[int, int]] = {}
    length = 0

    def pointer_segment(item: str) -> str:
        return item.replace("~", "~0").replace("/", "~1")

    def add(kind: str, pointer: str, content: str, *, bind: bool = False) -> None:
        nonlocal length
        if (
            len(pointer) > MAX_JSON_POINTER_CHARACTERS
            or len(content) > MAX_AUTHORIZED_TEXT_CHARACTERS
            or UNSAFE_TEXT.search(content)
        ):
            raise ReadingPackError("candidate JSON source contains unsafe text", EXIT_IO)
        normalized = normalize_text(content)
        marker = normalize_text(
            f"json-{kind} {json.dumps(pointer, ensure_ascii=True)}"
        )
        separator_before_content = 1 if normalized else 0
        entry = marker + ((" " + normalized) if normalized else "")
        separator = 1 if chunks else 0
        if length + separator + len(entry) > MAX_AUTHORIZED_TEXT_CHARACTERS:
            raise ReadingPackError(
                f"authorized text exceeds {MAX_AUTHORIZED_TEXT_CHARACTERS} characters",
                EXIT_IO,
            )
        length += separator
        start = length + len(marker) + separator_before_content
        chunks.append(entry)
        length += len(entry)
        if bind:
            spans[pointer] = (start, start + len(normalized))

    def walk(current: Any, pointer: str, depth: int) -> None:
        if depth > MAX_JSON_DEPTH:
            raise ReadingPackError(
                f"candidate JSON source exceeds maximum depth {MAX_JSON_DEPTH}",
                EXIT_IO,
            )
        if isinstance(current, Mapping):
            for key, item in current.items():
                if not isinstance(key, str):  # JSON object keys are strings after parsing.
                    raise ReadingPackError("candidate JSON source has an invalid key", EXIT_IO)
                child = f"{pointer}/{pointer_segment(key)}"
                add("key", child, key)
                walk(item, child, depth + 1)
        elif isinstance(current, list):
            for index, item in enumerate(current):
                walk(item, f"{pointer}/{index}", depth + 1)
        elif isinstance(current, str):
            add("value", pointer, current, bind=True)

    try:
        walk(parsed, "", 0)
    except RecursionError as exc:
        raise ReadingPackError("candidate JSON source nesting is too deep", EXIT_IO) from exc
    representation = " ".join(chunks)
    if normalize_text(representation) != representation:
        raise ReadingPackError("candidate JSON representation is not stable", EXIT_IO)
    return representation, spans


def json_string_values_text(value: str) -> str:
    """Return the decoded JSON representation used by candidate manifests."""

    return json_string_values_view(value)[0]


def _authorized_text(
    source_path: Path, raw: bytes, *, source_format: str
) -> str:
    """Derive evidence text from the exact source through an internal reader."""

    if source_format == "pdf":
        decoded = extract_pdf_authorized_text(source_path)
    elif source_format == "pdf-vertical":
        decoded = extract_pdf_authorized_text(source_path, vertical=True)
    elif source_format in {"epub", "epub3"}:
        decoded = extract_epub_authorized_text(source_path)
    else:
        try:
            decoded = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ReadingPackError(
                "candidate evidence supports PDF, EPUB3, or UTF-8 source files",
                EXIT_IO,
            ) from exc
        if source_format == "json":
            decoded = json_string_values_text(decoded)
    if len(decoded) > MAX_AUTHORIZED_TEXT_CHARACTERS:
        raise ReadingPackError(
            f"authorized text exceeds {MAX_AUTHORIZED_TEXT_CHARACTERS} characters",
            EXIT_IO,
        )
    return decoded


def _source_text_snapshot(
    source_path: Path, *, source_format: str
) -> tuple[bytes, str]:
    """Derive evidence from an immutable 0600 snapshot of the exact source bytes."""

    raw, before = _read_source(source_path)
    binary_suffix = {
        "pdf": ".pdf",
        "pdf-vertical": ".pdf",
        "epub": ".epub",
        "epub3": ".epub",
    }.get(source_format)
    if binary_suffix:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix="reading-pack-source-", suffix=binary_suffix
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(raw)
            temporary_path.chmod(0o600)
            text = _authorized_text(
                temporary_path, raw, source_format=source_format
            )
        finally:
            temporary_path.unlink(missing_ok=True)
    else:
        text = _authorized_text(source_path, raw, source_format=source_format)
    after_raw, after = _read_source(source_path)
    before_identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    after_identity = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    if before_identity != after_identity or raw != after_raw:
        raise ReadingPackError("candidate source changed while evidence text was extracted", EXIT_IO)
    return raw, text


def _coerce_responses(responses: Any) -> list[Mapping[str, Any]]:
    value = responses
    if isinstance(value, str):
        try:
            value = _strict_json_loads(value)
        except (json.JSONDecodeError, ValueError) as exc:
            raise ReadingPackError(f"candidate response is not valid JSON: {exc}") from exc
    if isinstance(value, Mapping) and "candidates" in value:
        value = value["candidates"]
    elif isinstance(value, Mapping):
        value = [value]
    if not isinstance(value, Sequence) or isinstance(value, (bytes, bytearray, str)):
        raise ReadingPackError("candidate response must be an object or array")
    if len(value) > MAX_CANDIDATES:
        raise ReadingPackError(f"candidate response exceeds {MAX_CANDIDATES} items")
    result: list[Mapping[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise ReadingPackError(f"candidate response item {index} must be an object")
        result.append(item)
    return result


def _sanitize_record(collection: str, raw_record: Any) -> tuple[dict[str, Any], list[str]]:
    if not isinstance(raw_record, Mapping):
        return {}, ["invalid_record"]
    allowed = ALLOWED_FIELDS.get(collection, set())
    record = {
        key: copy.deepcopy(value)
        for key, value in raw_record.items()
        if key in allowed
    }
    reasons: list[str] = []
    if set(raw_record) - allowed:
        reasons.append("unknown_record_field")
    # No model/manual input can promote a record.
    record["status"] = "draft"
    try:
        if len(_json_bytes(record)) > MAX_RECORD_BYTES:
            reasons.append("record_too_large")
    except (TypeError, ValueError):
        reasons.append("record_not_json")
    return record, reasons


def _record_reasons(
    collection: str,
    record: Mapping[str, Any],
    chapter_ids: set[str] | None,
) -> list[str]:
    reasons: list[str] = []
    if collection not in COLLECTIONS:
        return ["unknown_collection"]
    missing = REQUIRED_FIELDS[collection] - record.keys()
    if missing:
        reasons.append("missing_required_field")
    identifier = record.get("id")
    if not isinstance(identifier, str) or not re.fullmatch(ID_PATTERNS[collection], identifier):
        reasons.append("invalid_record_id")
    if record.get("status") != "draft":
        reasons.append("non_draft_record")
    for field in CONTENT_FIELDS[collection]:
        value = record.get(field)
        if not isinstance(value, str) or not value.strip():
            reasons.append("invalid_content_field")
            break
    for field, limit in STRING_LIMITS[collection].items():
        if field not in record:
            continue
        value = record[field]
        if not isinstance(value, str) or len(value) > limit or UNSAFE_TEXT.search(value):
            reasons.append("invalid_string_field")
            break
    for field in LIST_FIELDS.get(collection, ()):
        value = record.get(field)
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            reasons.append("invalid_list_field")
            break
    for field in OPTIONAL_LIST_FIELDS.get(collection, ()):
        if field in record and (
            not isinstance(record[field], list)
            or not all(isinstance(item, str) for item in record[field])
        ):
            reasons.append("invalid_list_field")
            break
    for field, (max_items, max_characters) in LIST_LIMITS.get(collection, {}).items():
        if field not in record:
            continue
        values = record[field]
        if (
            not isinstance(values, list)
            or len(values) > max_items
            or any(
                not isinstance(item, str)
                or not item.strip()
                or len(item) > max_characters
                or UNSAFE_TEXT.search(item)
                for item in values
            )
        ):
            reasons.append("invalid_list_field")
            break
    if collection == "chapters":
        kind = record.get("kind")
        if kind is not None and (
            not isinstance(kind, str)
            or kind not in {
                "frontmatter",
                "part",
                "chapter",
                "afterword",
                "appendix",
                "notes",
                "bibliography",
                "glossary",
                "index",
                "colophon",
                "unknown",
            }
        ):
            reasons.append("invalid_chapter_kind")
        spoiler_scope = record.get("spoiler_scope")
        if spoiler_scope is not None and (
            not isinstance(spoiler_scope, str)
            or spoiler_scope not in {
                "none",
                "chapter_only",
                "labeled_spoilers",
                "full_book",
            }
        ):
            reasons.append("invalid_spoiler_scope")
        if not isinstance(record.get("summary"), str):
            reasons.append("invalid_summary")
    if collection == "claims":
        layer = record.get("layer")
        if not isinstance(layer, str) or layer not in {"descriptive", "normative"}:
            reasons.append("invalid_claim_layer")
        if record.get("layer") == "descriptive" and record.get("revision_conditions"):
            reasons.append("invalid_claim_condition")
        if record.get("layer") == "normative" and record.get("falsifiability"):
            reasons.append("invalid_claim_condition")
    if collection == "misreadings":
        if ("issue" in record) == ("misreading" in record):
            reasons.append("invalid_issue_field")
        else:
            issue = record.get("issue", record.get("misreading"))
            if not isinstance(issue, str) or not issue.strip():
                reasons.append("invalid_content_field")
        kind = record.get("kind", "misreading")
        if kind not in {
            "misreading",
            "clarification",
            "open_objection",
            "author_update",
        }:
            reasons.append("invalid_misreading_kind")
    if collection == "policies" and record.get("kind") not in {
        "authority_order",
        "language_precedence",
        "translation_rights",
        "retrieval",
        "publisher_relation",
        "usage_terms",
        "other",
    }:
        reasons.append("invalid_policy_kind")
    if collection == "references":
        parsed = urlparse(str(record.get("url", "")))
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            reasons.append("invalid_reference_url")
    if chapter_ids is not None and collection in {"claims", "misreadings"}:
        refs = record.get("chapter_ids", [])
        if isinstance(refs, list) and any(ref not in chapter_ids for ref in refs):
            reasons.append("broken_chapter_reference")
    if chapter_ids is not None and collection in {"names", "glossary"}:
        if record.get("chapter_id") not in chapter_ids:
            reasons.append("broken_chapter_reference")
    return reasons


def _record_strings(
    record: Mapping[str, Any], *, collection: str | None = None
) -> Iterable[str]:
    for key, value in record.items():
        if collection == "chapters" and key not in CHAPTER_EDITABLE_FIELDS:
            continue
        if key in {"id", "status", "layer", "chapter_id", "chapter_ids", "certainty_id", "url"}:
            continue
        if isinstance(value, str):
            yield value
        elif isinstance(value, list):
            yield from (item for item in value if isinstance(item, str))


def _copy_risk(
    collection: str,
    record: Mapping[str, Any],
    source_index: _SourceCopyIndex,
    policy: LeakPolicy,
) -> bool:
    limit = policy.max_contiguous_characters
    for value in _record_strings(record, collection=collection):
        candidate = normalize_text(value)
        if len(candidate) < limit:
            continue
        if source_index.contains_policy_copy(candidate):
            return True
    return False


def _exact_source_term(value: str, normalized_source: str) -> bool:
    term = normalize_text(value)
    if not term:
        return False
    if re.fullmatch(r"[a-z0-9][a-z0-9 ._'’-]*", term):
        pattern = rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])"
        if re.search(pattern, normalized_source) is not None:
            return True
    elif term in normalized_source:
        return True
    # Some print PDFs expose every glyph as a separate positioned token.  The
    # authorized representation may contain normalized whitespace between the
    # characters of a name or index term.  Permit only that representation; no
    # other character may intervene, and ordinary exact matching above remains
    # the primary path.
    compact_term = re.sub(r"\s+", "", term)
    if 2 <= len(compact_term) <= 200:
        if re.fullmatch(r"[a-z0-9]+", compact_term):
            glyph_pattern = r"\s+".join(
                re.escape(character) for character in compact_term
            )
            for match in re.finditer(glyph_pattern, normalized_source):
                previous = re.search(r"([a-z0-9]+)\s*$", normalized_source[: match.start()])
                following = re.match(r"\s*([a-z0-9]+)", normalized_source[match.end() :])
                # Reject a substring of another per-glyph token sequence such
                # as QXR inside ``a q x r z`` while allowing ordinary adjacent
                # words such as ``Q X R is``.
                if previous is not None and len(previous.group(1)) == 1:
                    continue
                if following is not None and len(following.group(1)) == 1:
                    continue
                return True
            return False
        glyph_pattern = r"\s*".join(
            re.escape(character) for character in compact_term
        )
        return re.search(glyph_pattern, normalized_source) is not None
    return False


def _sanitize_generator(generator: Mapping[str, Any] | None) -> dict[str, Any]:
    allowed = {
        "adapter",
        "model",
        "revision",
        "settings_hash",
        "prompt_hash",
        "seed",
        "temperature",
        "top_p",
    }
    if not generator:
        return {"adapter": "manual", "model": ""}
    result = {key: copy.deepcopy(value) for key, value in generator.items() if key in allowed}
    result.setdefault("adapter", "manual")
    result.setdefault("model", "")
    for key in ("adapter", "model", "revision", "settings_hash", "prompt_hash"):
        if key not in result:
            continue
        value = result[key]
        if (
            not isinstance(value, str)
            or len(value) > 500
            or UNSAFE_TEXT.search(value)
        ):
            raise ReadingPackError("candidate generator metadata is invalid")
    if not result["adapter"]:
        raise ReadingPackError("candidate generator adapter must not be empty")
    if "seed" in result and not isinstance(result["seed"], int):
        raise ReadingPackError("candidate generator seed must be an integer")
    for key in ("temperature", "top_p"):
        if key in result and (
            type(result[key]) not in {int, float}
            or not math.isfinite(result[key])
        ):
            raise ReadingPackError("candidate generator sampling metadata is invalid")
    return result


def _evidence_refs(
    raw_evidence: Any,
    *,
    normalized_source: str,
    source_sha256: str,
    record_sha256: str,
    record_fields: set[str],
    representation: str = NORMALIZATION,
) -> tuple[list[dict[str, Any]], list[str]]:
    if not isinstance(raw_evidence, list) or not raw_evidence:
        return [], ["missing_evidence"]
    if len(raw_evidence) > MAX_EVIDENCE_PER_CANDIDATE:
        return [], ["too_many_evidence_refs"]
    refs: list[dict[str, Any]] = []
    reasons: list[str] = []
    seen: set[tuple[int, int, str, str]] = set()
    for item in raw_evidence:
        if isinstance(item, str):
            snippet = item
            occurrence = 0
            supports_field = ""
        elif isinstance(item, Mapping):
            if set(item) - {"snippet", "occurrence", "supports_field"}:
                reasons.append("invalid_evidence")
                continue
            snippet = item.get("snippet", "")
            occurrence = item.get("occurrence", 0)
            supports_field = item.get("supports_field", "")
        else:
            reasons.append("invalid_evidence")
            continue
        if not isinstance(snippet, str) or not snippet.strip():
            reasons.append("invalid_evidence")
            continue
        if len(snippet) > MAX_EVIDENCE_CHARACTERS:
            reasons.append("evidence_too_long")
            continue
        if (
            not isinstance(occurrence, int)
            or occurrence < 0
            or occurrence > MAX_EVIDENCE_OCCURRENCE
        ):
            reasons.append("invalid_evidence")
            continue
        if supports_field and (
            not isinstance(supports_field, str)
            or supports_field not in record_fields
            or len(supports_field) > 100
        ):
            reasons.append("invalid_evidence")
            continue
        support = normalize_text(snippet)
        if len(support) < MIN_EVIDENCE_CHARACTERS:
            reasons.append("evidence_too_short")
            continue
        start = -1
        cursor = 0
        for _ in range(occurrence + 1):
            start = normalized_source.find(support, cursor)
            if start < 0:
                break
            cursor = start + len(support)
        if start < 0:
            reasons.append("unsupported_evidence")
            continue
        end = start + len(support)
        span_sha256 = hashlib.sha256(support.encode("utf-8")).hexdigest()
        signature = (start, end, span_sha256, supports_field)
        if signature in seen:
            continue
        seen.add(signature)
        evidence_signature = (
            f"{source_sha256}:{record_sha256}:{start}:{end}:{span_sha256}"
            + (f":{supports_field}" if supports_field else "")
        )
        ref = {
                "id": f"EV-{hashlib.sha256(evidence_signature.encode()).hexdigest()[:16].upper()}",
                "source_sha256": source_sha256,
                "candidate_record_sha256": record_sha256,
                "locator": {
                    "kind": "normalized_text",
                    "char_start": start,
                    "char_end": end,
                },
                "representation": representation,
                "span_sha256": span_sha256,
                "support": "direct",
                "excerpt_stored": False,
            }
        if supports_field:
            ref["supports_field"] = supports_field
        require_structure("evidence-ref.schema.json", ref, label="evidence reference")
        refs.append(ref)
    if not refs:
        reasons.append("missing_supported_evidence")
    return refs, sorted(set(reasons))


def _candidate_id(
    source_sha256: str,
    collection: str,
    record: Mapping[str, Any] | str,
    index: int,
) -> str:
    record_sha256 = _value_hash(record) if isinstance(record, Mapping) else record
    digest = hashlib.sha256(
        _json_bytes(
            {
                "source_sha256": source_sha256,
                "collection": collection,
                "record_sha256": record_sha256,
                "ordinal": index,
            }
        )
    ).hexdigest()[:20].upper()
    return f"CAND-{digest}"


def _candidate_artifact_hash(
    candidate: Mapping[str, Any],
    *,
    source_sha256: str,
    text_sha256: str,
    canonical_binding: Mapping[str, Any],
) -> str:
    """Hash every machine-verifiable input a reviewer acceptance relies on."""

    payload = {
        "candidate_id": candidate.get("candidate_id"),
        "collection": candidate.get("collection"),
        "record_id": candidate.get("record_id"),
        "record_sha256": candidate.get("record_sha256"),
        "base_record_sha256": candidate.get("base_record_sha256"),
        "evidence_refs": candidate.get("evidence_refs"),
        "source_sha256": source_sha256,
        "text_sha256": text_sha256,
        "canonical": canonical_binding,
    }
    return _value_hash(payload)


def _summary(candidates: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    counts = Counter(
        str(candidate.get("candidate_state", ""))
        for candidate in candidates
        if isinstance(candidate, Mapping)
    )
    return {
        "total": sum(counts.values()),
        "proposed": counts["proposed"],
        "quarantined": counts["quarantined"],
        "ready_for_review": counts["ready_for_review"],
        "accepted": counts["accepted"],
        "applying": counts["applying"],
        "rejected": counts["rejected"],
        "applied": counts["applied"],
    }


def _application_record(value: Mapping[str, Any]) -> dict[str, Any]:
    """Return the deterministic durable form of one prepared CAS transaction."""

    payload = {
        "before_sha256": value["before_sha256"],
        "after_sha256": value["after_sha256"],
        "before_project_sha256": value["before_project_sha256"],
        "after_project_sha256": value["after_project_sha256"],
        "candidate_ids": list(value["candidate_ids"]),
    }
    return {
        "application_id": f"APP-{_value_hash(payload)[:20].upper()}",
        **payload,
    }


def _write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    manifest["summary"] = _summary(manifest.get("candidates", []))
    manifest["integrity_sha256"] = _manifest_integrity(manifest)
    require_structure("candidate-run.schema.json", manifest, label="candidate run")
    if len(_json_bytes(manifest)) > MAX_MANIFEST_BYTES:
        raise ReadingPackError(
            f"candidate manifest exceeds {MAX_MANIFEST_BYTES} bytes", EXIT_IO
        )
    write_json(path, manifest)
    try:
        path.chmod(0o600)
    except OSError as exc:
        raise ReadingPackError(f"cannot restrict candidate manifest permissions: {exc}", EXIT_IO) from exc


def create_candidate_run(
    run_directory: Path,
    *,
    source_path: Path,
    responses: Any,
    language: str,
    canonical_data: Mapping[str, Any],
    project_data_by_lang: Mapping[str, Mapping[str, Any]],
    known_chapter_ids: Iterable[str] = (),
    leak_policy: LeakPolicy | None = None,
    run_id: str | None = None,
    created_at: str | None = None,
    generator: Mapping[str, Any] | None = None,
    support_source: Mapping[str, Any] | None = None,
) -> Path:
    """Verify transient candidates and write a private, excerpt-free manifest.

    Evidence text is always derived internally from the exact PDF, EPUB3, or
    UTF-8 source; callers cannot substitute an unrelated extraction sidecar.
    """

    source_path = source_path.resolve()
    if not isinstance(canonical_data, Mapping):
        raise ReadingPackError("canonical_data must be an object")
    if (
        not isinstance(project_data_by_lang, Mapping)
        or language not in project_data_by_lang
        or project_data_by_lang[language] != canonical_data
        or not all(
            isinstance(key, str) and isinstance(value, Mapping)
            for key, value in project_data_by_lang.items()
        )
    ):
        raise ReadingPackError("project_data_by_lang must contain the exact target snapshot")
    canonical_source = canonical_data.get("source", {})
    if not isinstance(canonical_source, Mapping) or not canonical_source.get("sha256"):
        raise ReadingPackError(
            "canonical source is not imported; apply a reviewed import plan first"
        )
    if support_source is None:
        source_id = "SRC-1"
        source_role = "primary-book"
        expected_source = canonical_source
    else:
        if not isinstance(support_source, Mapping):
            raise ReadingPackError("support_source must be an object")
        source_id = support_source.get("id")
        source_role = support_source.get("role")
        if (
            not isinstance(source_id, str)
            or not re.fullmatch(r"SRC-[A-Z0-9][A-Z0-9.-]{0,99}", source_id)
            or not isinstance(source_role, str)
            or source_role not in {
                "author-qa", "author-canon", "author-data", "errata", "bibliography",
                "publisher-metadata", "translation",
            }
            or not isinstance(support_source.get("sha256"), str)
            or not isinstance(support_source.get("name"), str)
        ):
            raise ReadingPackError("support source identity is invalid")
        expected_source = support_source
    source_format = expected_source.get("format")
    if not isinstance(source_format, str) or not source_format:
        raise ReadingPackError("candidate source format is missing")
    use_json_string_values = source_format == "json"
    normalization = (
        JSON_STRING_NORMALIZATION if use_json_string_values else NORMALIZATION
    )
    raw_source, source_text = _source_text_snapshot(
        source_path, source_format=source_format
    )
    source_sha256 = file_hash(raw_source)
    if (
        expected_source.get("sha256") != source_sha256
        or expected_source.get("name") != source_path.name
    ):
        raise ReadingPackError("candidate source does not match its registered source identity")
    normalized_source = normalize_text(source_text)
    text_sha256 = hashlib.sha256(normalized_source.encode("utf-8")).hexdigest()
    policy = leak_policy or LeakPolicy()
    source_copy_index = _SourceCopyIndex(normalized_source, policy)
    raw_candidates = _coerce_responses(responses)

    evidence_ref_count = 0
    evidence_match_work = 0
    for raw_candidate in raw_candidates:
        raw_evidence = raw_candidate.get("evidence")
        if not isinstance(raw_evidence, list):
            continue
        evidence_ref_count += len(raw_evidence)
        if evidence_ref_count > MAX_RUN_EVIDENCE_REFS:
            raise ReadingPackError(
                f"candidate run exceeds {MAX_RUN_EVIDENCE_REFS} evidence references"
            )
        for item in raw_evidence:
            occurrence = item.get("occurrence", 0) if isinstance(item, Mapping) else 0
            if isinstance(occurrence, int) and 0 <= occurrence <= MAX_EVIDENCE_OCCURRENCE:
                evidence_match_work += occurrence + 1
                if evidence_match_work > MAX_RUN_EVIDENCE_MATCH_WORK:
                    raise ReadingPackError(
                        "candidate run exceeds the bounded evidence match workload"
                    )

    canonical_records: dict[tuple[str, str], Mapping[str, Any]] = {}
    for collection_name in COLLECTIONS:
        records = canonical_data.get(collection_name, [])
        if not isinstance(records, list):
            raise ReadingPackError(f"canonical collection is not an array: {collection_name}")
        for item in records:
            if isinstance(item, Mapping) and isinstance(item.get("id"), str):
                canonical_records[(collection_name, item["id"])] = item

    preliminary: list[dict[str, Any]] = []
    chapter_ids = {value for value in known_chapter_ids if isinstance(value, str)}
    canonical_chapter_ids = {
        item.get("id")
        for item in canonical_data.get("chapters", [])
        if isinstance(item, Mapping) and isinstance(item.get("id"), str)
    }
    if chapter_ids != canonical_chapter_ids:
        raise ReadingPackError("known chapter IDs do not match canonical chapter IDs")

    for index, raw_candidate in enumerate(raw_candidates):
        collection = raw_candidate.get("collection", "unknown")
        if collection not in COLLECTIONS:
            collection = "unknown"
            record: dict[str, Any] = {}
            reasons = ["unknown_collection"]
            record_safe_to_store = False
        else:
            record, reasons = _sanitize_record(collection, raw_candidate.get("record"))
            structural_reasons = _record_reasons(collection, record, chapter_ids)
            reasons.extend(structural_reasons)
            record_safe_to_store = not structural_reasons and not any(
                reason in {"unknown_record_field", "record_too_large", "record_not_json"}
                for reason in reasons
            )

        if collection == "names" and isinstance(record.get("name"), str):
            if not _exact_source_term(record["name"], normalized_source):
                reasons.append("name_not_in_source")
        if collection == "glossary" and isinstance(record.get("term"), str):
            if not _exact_source_term(record["term"], normalized_source):
                reasons.append("term_not_in_source")

        copy_risk = collection in COLLECTIONS and _copy_risk(
            collection, record, source_copy_index, policy
        )
        if collection in COLLECTIONS and _extractive_budget_exceeded(
            collection, record, normalized_source
        ):
            copy_risk = True
        if copy_risk:
            reasons.append("source_copy_risk")

        record_hash = _value_hash(record)
        refs, evidence_reasons = _evidence_refs(
            raw_candidate.get("evidence"),
            normalized_source=normalized_source,
            source_sha256=source_sha256,
            record_sha256=record_hash,
            record_fields=set(record),
            representation=normalization,
        )
        reasons.extend(evidence_reasons)
        identifier = record.get("id") if isinstance(record.get("id"), str) else ""
        base = canonical_records.get((collection, identifier))
        if collection == "chapters":
            if base is None:
                reasons.append("chapter_not_in_canonical")
                record_safe_to_store = False
            elif any(record.get(field) != base.get(field) for field in CHAPTER_STRUCTURAL_FIELDS):
                reasons.append("chapter_structure_changed")
                record_safe_to_store = False
        record_id = record.get("id", "")
        if (
            collection not in ID_PATTERNS
            or not isinstance(record_id, str)
            or not re.fullmatch(ID_PATTERNS[collection], record_id)
        ):
            record_id = ""
        candidate: dict[str, Any] = {
            "candidate_id": _candidate_id(source_sha256, collection, record, index),
            "collection": collection,
            "record_id": record_id,
            "record_sha256": record_hash,
            "base_record_sha256": _value_hash(base) if base is not None else "",
            "evidence_refs": refs,
            "candidate_state": "proposed",
            "review": {
                "status": "pending",
                "reviewer": "",
                "reviewed_at": "",
                "candidate_record_sha256": record_hash,
                "candidate_artifact_sha256": "",
            },
            "qa": {
                "passed": False,
                "reason_codes": sorted(set(reasons)),
            },
        }
        # A risky verbatim field is not merely quarantined; it is omitted so the
        # candidate workspace does not become a second copy of manuscript prose.
        if record and not copy_risk and record_safe_to_store:
            candidate["record"] = record
        preliminary.append(candidate)

    record_counts = Counter(
        candidate["record_id"] for candidate in preliminary if candidate["record_id"]
    )
    for candidate in preliminary:
        reasons = set(candidate["qa"]["reason_codes"])
        if candidate["record_id"] and record_counts[candidate["record_id"]] > 1:
            reasons.add("duplicate_record_id")
        candidate["qa"]["reason_codes"] = sorted(reasons)
        candidate["qa"]["passed"] = not reasons
        candidate["candidate_state"] = (
            "ready_for_review" if not reasons else "quarantined"
        )

    canonical_binding = {
        "data_sha256": _value_hash(canonical_data),
        "project_data_sha256": _value_hash(project_data_by_lang),
    }
    for candidate in preliminary:
        candidate["review"]["candidate_artifact_sha256"] = _candidate_artifact_hash(
            candidate,
            source_sha256=source_sha256,
            text_sha256=text_sha256,
            canonical_binding=canonical_binding,
        )

    run_directory = run_directory.resolve()
    try:
        run_directory.mkdir(parents=True, exist_ok=False)
        run_directory.chmod(0o700)
    except FileExistsError as exc:
        raise ReadingPackError(f"candidate run directory already exists: {run_directory}", EXIT_IO) from exc
    except OSError as exc:
        raise ReadingPackError(f"cannot create candidate run directory {run_directory}: {exc}", EXIT_IO) from exc

    manifest = {
        "schema_version": RUN_SCHEMA_VERSION,
        "run_id": run_id or str(uuid.uuid4()),
        "created_at": created_at or datetime.now(timezone.utc).isoformat(),
        "status": "complete",
        "language": language,
        "source": {
            "id": source_id,
            "role": source_role,
            "name": source_path.name,
            "format": source_format,
            "sha256": source_sha256,
            "text_sha256": text_sha256,
        },
        "canonical": canonical_binding,
        "normalization": normalization,
        "leak_policy": asdict(policy),
        "generator": _sanitize_generator(generator),
        "candidates": preliminary,
        "transaction": None,
        "application": None,
        "summary": {},
        "integrity_sha256": "",
    }
    manifest_path = run_directory / MANIFEST_NAME
    _write_manifest(manifest_path, manifest)
    return manifest_path


def _manifest_path(path: Path) -> Path:
    path = path.resolve()
    return path / MANIFEST_NAME if path.is_dir() else path


@contextmanager
def _manifest_lock(manifest_path: Path):
    """Serialize cooperating edits of a private run manifest."""

    try:
        import fcntl
    except ImportError as exc:  # pragma: no cover - supported runtime is POSIX
        raise ReadingPackError("candidate locking requires POSIX fcntl", EXIT_IO) from exc
    lock_path = manifest_path.parent / ".manifest.lock"
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    except OSError as exc:
        raise ReadingPackError(f"cannot open candidate run lock: {exc}", EXIT_IO) from exc
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def load_candidate_run(path: Path, *, verify_integrity: bool = True) -> dict[str, Any]:
    manifest_path = _manifest_path(path)
    try:
        if manifest_path.stat().st_size > MAX_MANIFEST_BYTES:
            raise ReadingPackError(
                f"candidate manifest exceeds {MAX_MANIFEST_BYTES} bytes", EXIT_IO
            )
        manifest = _strict_json_loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise ReadingPackError(f"cannot read candidate run {manifest_path}: {exc}", EXIT_IO) from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != RUN_SCHEMA_VERSION:
        raise ReadingPackError("unsupported or invalid candidate run manifest")
    if verify_integrity:
        expected = _manifest_integrity(manifest)
        if manifest.get("integrity_sha256") != expected:
            raise ReadingPackError("candidate run integrity check failed")
    require_structure("candidate-run.schema.json", manifest, label="candidate run")
    if manifest.get("normalization") not in SUPPORTED_NORMALIZATIONS:
        raise ReadingPackError("unsupported candidate evidence normalization")
    required_top = {
        "schema_version", "run_id", "created_at", "status", "language", "source",
        "canonical", "normalization", "leak_policy", "generator", "candidates",
        "transaction", "summary", "integrity_sha256",
    }
    if not required_top <= set(manifest) or set(manifest) - (required_top | {"application"}):
        raise ReadingPackError("candidate run contains missing or unknown top-level fields")
    if manifest.get("status") != "complete" or manifest.get("language") not in {"ja", "en"}:
        raise ReadingPackError("candidate run metadata is invalid")
    for field in ("run_id", "created_at"):
        value = manifest.get(field)
        if not isinstance(value, str) or not value or len(value) > 200 or UNSAFE_TEXT.search(value):
            raise ReadingPackError("candidate run metadata contains an unsafe string")
    source = manifest.get("source")
    legacy_source_fields = {"name", "format", "sha256", "text_sha256"}
    support_source_fields = legacy_source_fields | {"id", "role"}
    if not isinstance(source, dict) or set(source) not in {
        frozenset(legacy_source_fields), frozenset(support_source_fields)
    }:
        raise ReadingPackError("candidate run source metadata is invalid")
    if set(source) == support_source_fields:
        if (
            not isinstance(source.get("id"), str)
            or not re.fullmatch(r"SRC-[A-Z0-9][A-Z0-9.-]{0,99}", source["id"])
            or source.get("role") not in {
                "primary-book", "author-qa", "author-canon", "author-data", "errata", "bibliography",
                "publisher-metadata", "translation",
            }
            or (source["role"] == "primary-book" and source["id"] != "SRC-1")
            or (source["role"] != "primary-book" and source["id"] == "SRC-1")
        ):
            raise ReadingPackError("candidate run source identity is invalid")
    source_name = source.get("name")
    if (
        not isinstance(source_name, str)
        or not source_name
        or len(source_name) > 500
        or Path(source_name).name != source_name
        or UNSAFE_TEXT.search(source_name)
    ):
        raise ReadingPackError("candidate run source name is invalid")
    if not isinstance(source.get("format"), str) or not source["format"]:
        raise ReadingPackError("candidate run source format is invalid")
    is_json_source = source["format"] == "json"
    if (manifest["normalization"] == JSON_STRING_NORMALIZATION) != is_json_source:
        raise ReadingPackError(
            "candidate run source format and evidence normalization are inconsistent"
        )
    hash_pattern = re.compile(r"[a-f0-9]{64}")
    if any(not isinstance(source.get(key), str) or not hash_pattern.fullmatch(source[key]) for key in ("sha256", "text_sha256")):
        raise ReadingPackError("candidate run source hash is invalid")
    canonical = manifest.get("canonical")
    if (
        not isinstance(canonical, dict)
        or set(canonical) != {"data_sha256", "project_data_sha256"}
        or not isinstance(canonical.get("data_sha256"), str)
        or not hash_pattern.fullmatch(canonical["data_sha256"])
        or not isinstance(canonical.get("project_data_sha256"), str)
        or not hash_pattern.fullmatch(canonical["project_data_sha256"])
    ):
        raise ReadingPackError("candidate run canonical binding is invalid")
    candidates = manifest.get("candidates")
    if not isinstance(candidates, list) or len(candidates) > MAX_CANDIDATES:
        raise ReadingPackError("candidate run candidates are invalid")
    if not all(isinstance(candidate, dict) for candidate in candidates):
        raise ReadingPackError("candidate run contains a non-object candidate")
    if manifest.get("summary") != _summary(candidates):
        raise ReadingPackError("candidate run summary does not match candidate states")
    leak_policy = manifest.get("leak_policy")
    if (
        not isinstance(leak_policy, dict)
        or set(leak_policy) != {"max_contiguous_characters"}
        or not isinstance(leak_policy.get("max_contiguous_characters"), int)
        or leak_policy["max_contiguous_characters"] < 64
    ):
        raise ReadingPackError("candidate run leak policy is invalid")
    generator = manifest.get("generator")
    if (
        not isinstance(generator, dict)
        or not {"adapter", "model"}.issubset(generator)
        or set(generator) - {
            "adapter", "model", "revision", "settings_hash", "prompt_hash",
            "seed", "temperature", "top_p",
        }
        or not isinstance(generator.get("adapter"), str)
        or not generator["adapter"]
        or not isinstance(generator.get("model"), str)
    ):
        raise ReadingPackError("candidate run generator metadata is invalid")
    transaction = manifest.get("transaction")
    if transaction is not None:
        if (
            not isinstance(transaction, dict)
            or set(transaction) != {
                "state", "before_sha256", "after_sha256",
                "before_project_sha256", "after_project_sha256", "candidate_ids",
            }
            or transaction.get("state") != "prepared"
            or any(
                not isinstance(transaction.get(key), str)
                or not hash_pattern.fullmatch(transaction[key])
                for key in (
                    "before_sha256", "after_sha256",
                    "before_project_sha256", "after_project_sha256",
                )
            )
            or not isinstance(transaction.get("candidate_ids"), list)
            or not all(
                isinstance(value, str) and re.fullmatch(r"CAND-[A-F0-9]{20}", value)
                for value in transaction.get("candidate_ids", [])
            )
        ):
            raise ReadingPackError("candidate run transaction is invalid")
    application = manifest.get("application")
    if application is not None:
        if (
            not isinstance(application, dict)
            or set(application) != {
                "application_id", "before_sha256", "after_sha256",
                "before_project_sha256", "after_project_sha256", "candidate_ids",
            }
            or not re.fullmatch(
                r"APP-[A-F0-9]{20}", str(application.get("application_id", ""))
            )
            or any(
                not isinstance(application.get(key), str)
                or not hash_pattern.fullmatch(application[key])
                for key in (
                    "before_sha256", "after_sha256",
                    "before_project_sha256", "after_project_sha256",
                )
            )
            or not isinstance(application.get("candidate_ids"), list)
            or not application["candidate_ids"]
            or len(application["candidate_ids"]) != len(set(application["candidate_ids"]))
            or not all(
                isinstance(value, str) and re.fullmatch(r"CAND-[A-F0-9]{20}", value)
                for value in application["candidate_ids"]
            )
            or transaction is not None
            or application["before_sha256"] != manifest["canonical"]["data_sha256"]
            or application["before_project_sha256"]
            != manifest["canonical"]["project_data_sha256"]
            or application["application_id"]
            != _application_record(application)["application_id"]
        ):
            raise ReadingPackError("candidate run application receipt is invalid")
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            raise ReadingPackError("candidate run contains a non-object candidate")
        required = {
            "candidate_id", "collection", "record_id", "record_sha256",
            "base_record_sha256", "evidence_refs", "candidate_state", "review", "qa",
        }
        if not required.issubset(candidate) or set(candidate) - (required | {"record"}):
            raise ReadingPackError("candidate run contains missing or unknown candidate fields")
        if not isinstance(candidate.get("candidate_id"), str) or not re.fullmatch(
            r"CAND-[A-F0-9]{20}", candidate["candidate_id"]
        ):
            raise ReadingPackError("candidate run contains an invalid candidate ID")
        if candidate.get("candidate_state") not in CANDIDATE_STATES:
            raise ReadingPackError("candidate run contains an invalid state")
        if candidate.get("collection") not in (*COLLECTIONS, "unknown"):
            raise ReadingPackError("candidate run contains an invalid collection")
        if (
            not isinstance(candidate.get("record_id"), str)
            or len(candidate["record_id"]) > 200
            or UNSAFE_TEXT.search(candidate["record_id"])
            or (
                candidate["record_id"]
                and candidate.get("collection") in ID_PATTERNS
                and not re.fullmatch(ID_PATTERNS[candidate["collection"]], candidate["record_id"])
            )
        ):
            raise ReadingPackError("candidate run contains an invalid record ID")
        if not isinstance(candidate.get("record_sha256"), str) or not hash_pattern.fullmatch(candidate["record_sha256"]):
            raise ReadingPackError("candidate run contains an invalid record hash")
        base_hash = candidate.get("base_record_sha256")
        if not isinstance(base_hash, str) or (base_hash and not hash_pattern.fullmatch(base_hash)):
            raise ReadingPackError("candidate run contains an invalid base record hash")
        record = candidate.get("record")
        if record is not None:
            if not isinstance(record, dict) or record.get("status") != "draft":
                raise ReadingPackError("candidate run contains a non-draft record")
            if _value_hash(record) != candidate.get("record_sha256"):
                raise ReadingPackError("candidate record integrity check failed")
            collection = candidate.get("collection")
            if (
                collection not in COLLECTIONS
                or set(record) - ALLOWED_FIELDS[collection]
                or len(_json_bytes(record)) > MAX_RECORD_BYTES
                or _record_reasons(collection, record, None)
            ):
                raise ReadingPackError("candidate run contains an invalid record")
            if candidate.get("record_id") != record.get("id"):
                raise ReadingPackError("candidate record ID binding is invalid")
        elif candidate.get("candidate_state") not in {"quarantined", "rejected"}:
            raise ReadingPackError("an actionable candidate has no record")
        expected_candidate_id = _candidate_id(
            source["sha256"],
            candidate.get("collection", "unknown"),
            candidate["record_sha256"],
            index,
        )
        if candidate["candidate_id"] != expected_candidate_id:
            raise ReadingPackError("candidate identifier verification failed")
        qa = candidate.get("qa")
        if (
            not isinstance(qa, dict)
            or set(qa) != {"passed", "reason_codes"}
            or not isinstance(qa.get("passed"), bool)
            or not isinstance(qa.get("reason_codes"), list)
            or not all(
                isinstance(code, str)
                and re.fullmatch(r"[a-z][a-z0-9_]{0,99}", code)
                for code in qa["reason_codes"]
            )
            or len(qa["reason_codes"]) != len(set(qa["reason_codes"]))
        ):
            raise ReadingPackError("candidate run QA record is invalid")
        if qa["passed"] != (not qa["reason_codes"]):
            raise ReadingPackError("candidate run QA result is inconsistent")
        review = candidate.get("review")
        review_fields = {
            "status", "reviewer", "reviewed_at", "candidate_record_sha256",
            "candidate_artifact_sha256",
        }
        review_provenance_fields = {
            "reviewer_type", "review_method", "review_artifact_sha256",
        }
        if (
            not isinstance(review, dict)
            or not review_fields.issubset(review)
            or set(review) - (review_fields | review_provenance_fields)
            or bool(set(review) & review_provenance_fields)
            != review_provenance_fields.issubset(review)
            or review.get("status") not in {"pending", "accepted", "rejected"}
            or review.get("candidate_record_sha256") != candidate.get("record_sha256")
            or not isinstance(review.get("candidate_artifact_sha256"), str)
            or not hash_pattern.fullmatch(review["candidate_artifact_sha256"])
            or not all(isinstance(review.get(key), str) for key in ("reviewer", "reviewed_at"))
            or any(len(review.get(key, "")) > 500 or UNSAFE_TEXT.search(review.get(key, "")) for key in ("reviewer", "reviewed_at"))
        ):
            raise ReadingPackError("candidate run review record is invalid")
        if review_provenance_fields.issubset(review):
            reviewer_type = review.get("reviewer_type")
            review_method = review.get("review_method")
            review_artifact_sha256 = review.get("review_artifact_sha256")
            if (
                reviewer_type not in REVIEWER_TYPES
                or not isinstance(review_method, str)
                or len(review_method) > 500
                or UNSAFE_TEXT.search(review_method)
                or not isinstance(review_artifact_sha256, str)
                or (
                    review_artifact_sha256
                    and not hash_pattern.fullmatch(review_artifact_sha256)
                )
                or (
                    review.get("status") == "accepted"
                    and not review_method
                )
                or (
                    reviewer_type == "ai"
                    and review.get("status") == "accepted"
                    and not review_artifact_sha256
                )
                or (reviewer_type == "human" and review_artifact_sha256)
            ):
                raise ReadingPackError("candidate review provenance is invalid")
        expected_artifact_hash = _candidate_artifact_hash(
            candidate,
            source_sha256=source["sha256"],
            text_sha256=source["text_sha256"],
            canonical_binding=canonical,
        )
        if review["candidate_artifact_sha256"] != expected_artifact_hash:
            raise ReadingPackError("candidate review artifact binding is stale")
        if candidate.get("candidate_state") in {"accepted", "applying", "applied"}:
            if review["status"] != "accepted" or not review["reviewer"] or not review["reviewed_at"]:
                raise ReadingPackError("candidate is not bound to an explicit reviewer acceptance")
        if review.get("status") == "rejected" and candidate.get("candidate_state") != "rejected":
            raise ReadingPackError("candidate review rejection state is inconsistent")
        if candidate.get("candidate_state") == "ready_for_review" and not qa["passed"]:
            raise ReadingPackError("ready candidate did not pass QA")
        if candidate.get("candidate_state") == "quarantined" and qa["passed"]:
            raise ReadingPackError("quarantined candidate has an inconsistent QA result")
        refs = candidate.get("evidence_refs")
        if not isinstance(refs, list) or len(refs) > MAX_EVIDENCE_PER_CANDIDATE:
            raise ReadingPackError("candidate evidence references are invalid")
        for evidence in refs:
            expected_evidence_fields = {
                "id", "source_sha256", "candidate_record_sha256", "locator",
                "representation", "span_sha256", "support", "excerpt_stored",
            }
            if not isinstance(evidence, dict) or set(evidence) not in {
                frozenset(expected_evidence_fields),
                frozenset(expected_evidence_fields | {"supports_field"}),
            }:
                raise ReadingPackError("candidate evidence reference is invalid")
            supports_field = evidence.get("supports_field", "")
            record = candidate.get("record")
            if supports_field and (
                not isinstance(supports_field, str)
                or len(supports_field) > 100
                or (isinstance(record, Mapping) and supports_field not in record)
            ):
                raise ReadingPackError("candidate evidence supported field is invalid")
            if evidence.get("excerpt_stored") is not False:
                raise ReadingPackError("candidate evidence must not retain excerpts")
            if evidence.get("source_sha256") != source["sha256"]:
                raise ReadingPackError("candidate evidence has a stale source binding")
            if evidence.get("candidate_record_sha256") != candidate.get("record_sha256"):
                raise ReadingPackError("candidate evidence has a stale record binding")
            locator = evidence.get("locator")
            if (
                not isinstance(locator, dict)
                or set(locator) != {"kind", "char_start", "char_end"}
                or locator.get("kind") != "normalized_text"
                or not isinstance(locator.get("char_start"), int)
                or not isinstance(locator.get("char_end"), int)
                or locator["char_start"] < 0
                or locator["char_end"] <= locator["char_start"]
                or locator["char_end"] - locator["char_start"] < MIN_EVIDENCE_CHARACTERS
                or evidence.get("representation") != manifest.get("normalization")
                or evidence.get("support") not in {"direct", "distributed"}
                or not isinstance(evidence.get("span_sha256"), str)
                or not hash_pattern.fullmatch(evidence["span_sha256"])
                or not isinstance(evidence.get("id"), str)
                or not re.fullmatch(r"EV-[A-F0-9]{16}", evidence["id"])
            ):
                raise ReadingPackError("candidate evidence locator is invalid")
        if qa["passed"] and not refs:
            raise ReadingPackError("a QA-passing candidate has no evidence")
    if application is not None and set(application["candidate_ids"]) != {
        candidate["candidate_id"]
        for candidate in candidates
        if candidate.get("candidate_state") == "applied"
    }:
        raise ReadingPackError(
            "candidate run application receipt does not match applied candidates"
        )
    return manifest


def author_review_suggestions(
    project: Path,
    runs: Sequence[Path],
    *,
    record_ids: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """Return current, QA-passed candidate replacements for a focused review.

    The returned bodies are intended for the editable Markdown review, not its
    private manifest.  A primary-language replacement in a multilingual
    project must be accompanied by a candidate for every configured language
    so one signed review can update and approve the linked records atomically.
    """

    project = Path(project).resolve()
    config = load_config(project)
    languages = list(config.get("languages", []))
    data_by_lang = {
        language: load_language_data(project, language) for language in languages
    }
    current_project_hash = _value_hash(data_by_lang)
    requested = set(record_ids) if record_ids is not None else None
    suggestions: dict[tuple[str, str, str], dict[str, Any]] = {}
    for run_path in runs:
        manifest = load_candidate_run(Path(run_path))
        language = manifest["language"]
        if language not in data_by_lang:
            raise ReadingPackError(
                f"candidate run language is not configured: {language}"
            )
        if manifest["canonical"]["project_data_sha256"] != current_project_hash:
            raise ReadingPackError(
                "candidate run is stale for the current multilingual canonical data"
            )
        source = manifest["source"]
        source_role = source.get("role", "primary-book")
        source_id = source.get("id", "SRC-1")
        if source_role == "primary-book":
            canonical_source = data_by_lang[language].get("source", {})
            if any(
                canonical_source.get(field) != source.get(field)
                for field in ("name", "format", "sha256")
            ):
                raise ReadingPackError(
                    "candidate review source does not match the canonical source"
                )
        else:
            from reading_pack.source_registry import registered_source

            registered = registered_source(project, source_id)
            if any(
                registered.get(field) != source.get(field)
                for field in ("role", "name", "format", "sha256")
            ):
                raise ReadingPackError(
                    "candidate review support source is stale or absent"
                )
        for candidate in manifest["candidates"]:
            if candidate.get("candidate_state") not in {
                "ready_for_review", "accepted"
            } or not candidate.get("qa", {}).get("passed"):
                continue
            record_id = candidate["record_id"]
            if requested is not None and record_id not in requested:
                continue
            collection = candidate["collection"]
            record = candidate.get("record")
            _, current = _record_at(data_by_lang[language], collection, record_id)
            if current is None or not isinstance(record, dict):
                raise ReadingPackError(
                    "candidate review suggestions can only replace current records"
                )
            if candidate["base_record_sha256"] != _value_hash(current):
                raise ReadingPackError(
                    f"candidate base record is stale: {language}.{collection}.{record_id}"
                )
            key = (language, collection, record_id)
            if key in suggestions:
                raise ReadingPackError(
                    "candidate review suggestions contain a duplicate record"
                )
            suggestions[key] = {
                "language": language,
                "collection": collection,
                "record_id": record_id,
                "candidate_id": candidate["candidate_id"],
                "run_id": manifest["run_id"],
                "record": copy.deepcopy(record),
            }
    if not suggestions:
        raise ReadingPackError("candidate runs contain no reviewable suggestions")
    if requested is not None:
        selected_ids = {item[2] for item in suggestions}
        missing_requested = sorted(requested - selected_ids)
        if missing_requested:
            raise ReadingPackError(
                "candidate runs contain no reviewable suggestion for: "
                + ",".join(missing_requested)
            )
    primary = config["primary_language"]
    for language, collection, record_id in list(suggestions):
        if language != primary or len(languages) == 1:
            continue
        missing = [
            configured
            for configured in languages
            if (configured, collection, record_id) not in suggestions
        ]
        if missing:
            raise ReadingPackError(
                f"primary candidate {collection}.{record_id} requires candidate "
                "suggestions for " + ",".join(missing)
            )
    language_order = {language: index for index, language in enumerate(languages)}
    return sorted(
        suggestions.values(),
        key=lambda item: (
            item["collection"],
            item["record_id"],
            language_order[item["language"]],
        ),
    )


def _verify_source_and_evidence(
    manifest: Mapping[str, Any],
    source_path: Path,
) -> tuple[str, str]:
    source = manifest.get("source", {})
    normalization = manifest.get("normalization")
    use_json_string_values = normalization == JSON_STRING_NORMALIZATION
    source_format = source.get("format")
    if use_json_string_values != (source_format == "json"):
        raise ReadingPackError("candidate JSON evidence normalization is not valid for this source")
    raw, text = _source_text_snapshot(
        source_path, source_format=source_format
    )
    source_sha256 = file_hash(raw)
    if source_path.name != source.get("name") or source_sha256 != source.get("sha256"):
        raise ReadingPackError("candidate source is stale or does not match the run")
    normalized_source = normalize_text(text)
    text_sha256 = hashlib.sha256(normalized_source.encode("utf-8")).hexdigest()
    if text_sha256 != source.get("text_sha256"):
        raise ReadingPackError("authorized source text is stale or does not match the run")
    for candidate in manifest.get("candidates", []):
        for evidence in candidate.get("evidence_refs", []):
            if evidence.get("source_sha256") != source_sha256:
                raise ReadingPackError("candidate evidence has a stale source hash")
            if evidence.get("representation") != normalization:
                raise ReadingPackError("candidate evidence uses an unknown representation")
            locator = evidence.get("locator", {})
            if locator.get("kind") != "normalized_text":
                raise ReadingPackError("candidate evidence has an invalid locator")
            start = locator.get("char_start")
            end = locator.get("char_end")
            if (
                not isinstance(start, int)
                or not isinstance(end, int)
                or start < 0
                or end <= start
                or end > len(normalized_source)
            ):
                raise ReadingPackError("candidate evidence offsets are invalid")
            span = normalized_source[start:end]
            if end - start < MIN_EVIDENCE_CHARACTERS:
                raise ReadingPackError("candidate evidence span is too short")
            if hashlib.sha256(span.encode("utf-8")).hexdigest() != evidence.get("span_sha256"):
                raise ReadingPackError("candidate evidence span verification failed")
            evidence_signature = (
                f"{source_sha256}:{candidate.get('record_sha256')}:{start}:{end}:"
                f"{evidence.get('span_sha256')}"
            )
            if evidence.get("supports_field"):
                evidence_signature += f":{evidence['supports_field']}"
            expected_digest = hashlib.sha256(evidence_signature.encode()).hexdigest()
            expected_id = f"EV-{expected_digest[:16].upper()}"
            if evidence.get("id") != expected_id:
                raise ReadingPackError("candidate evidence identifier verification failed")
    return normalized_source, source_sha256


def verify_candidate_run(
    run: Path,
    *,
    source_path: Path,
) -> dict[str, Any]:
    """Recheck a run against its exact source without exposing candidate prose."""

    manifest = load_candidate_run(run)
    _verify_source_and_evidence(manifest, source_path.resolve())
    return {
        "run_id": manifest.get("run_id", ""),
        "language": manifest.get("language", ""),
        "source": {
            "name": manifest.get("source", {}).get("name", ""),
            "sha256": manifest.get("source", {}).get("sha256", ""),
        },
        "summary": copy.deepcopy(manifest.get("summary", {})),
        "verified": True,
    }


def _requested_candidates(
    manifest: Mapping[str, Any],
    candidate_ids: Iterable[str] | None,
    *,
    required_state: str,
) -> list[dict[str, Any]]:
    if candidate_ids is None:
        raise ReadingPackError("explicit candidate IDs are required")
    requested = list(dict.fromkeys(candidate_ids))
    if not requested or any(
        not isinstance(identifier, str)
        or not re.fullmatch(r"CAND-[A-F0-9]{20}", identifier)
        for identifier in requested
    ):
        raise ReadingPackError("one or more candidate IDs are invalid")
    available = {
        candidate.get("candidate_id"): candidate
        for candidate in manifest.get("candidates", [])
    }
    if any(identifier not in available for identifier in requested):
        raise ReadingPackError("one or more candidate IDs were not found")
    selected = [available[identifier] for identifier in requested]
    if any(candidate.get("candidate_state") != required_state for candidate in selected):
        raise ReadingPackError(f"one or more candidates are not {required_state}")
    for candidate in selected:
        if not candidate.get("qa", {}).get("passed"):
            raise ReadingPackError("a selected candidate failed QA")
        if not isinstance(candidate.get("record"), dict):
            raise ReadingPackError("a selected candidate has no applicable record")
    return selected


def accept_candidates(
    run: Path,
    candidate_ids: Iterable[str],
    *,
    reviewer: str,
    reviewer_type: str = "human",
    review_method: str | None = None,
    review_artifact_sha256: str = "",
    reviewed_at: str | None = None,
) -> list[str]:
    """Bind an explicit human or AI acceptance to exact candidate hashes."""

    if (
        not isinstance(reviewer, str)
        or not reviewer.strip()
        or len(reviewer) > 500
        or UNSAFE_TEXT.search(reviewer)
    ):
        raise ReadingPackError("reviewer must be a non-empty safe string")
    if reviewer_type not in REVIEWER_TYPES:
        raise ReadingPackError("reviewer_type must be human or ai")
    method = review_method or (
        "manual-candidate-review" if reviewer_type == "human" else ""
    )
    if (
        not isinstance(method, str)
        or not method
        or len(method) > 500
        or UNSAFE_TEXT.search(method)
    ):
        raise ReadingPackError("review_method must be a non-empty safe string")
    hash_pattern = re.compile(r"[a-f0-9]{64}")
    if reviewer_type == "ai":
        if not isinstance(review_artifact_sha256, str) or not hash_pattern.fullmatch(
            review_artifact_sha256
        ):
            raise ReadingPackError(
                "AI acceptance requires a verified review artifact SHA-256"
            )
    elif review_artifact_sha256:
        raise ReadingPackError("human acceptance must not claim an AI review artifact")
    timestamp = reviewed_at or datetime.now(timezone.utc).isoformat()
    if len(timestamp) > 200 or UNSAFE_TEXT.search(timestamp):
        raise ReadingPackError("review timestamp is invalid")
    manifest_path = _manifest_path(run)
    with _manifest_lock(manifest_path):
        manifest = load_candidate_run(manifest_path)
        if manifest.get("transaction") is not None:
            raise ReadingPackError("candidate run has a pending canonical transaction")
        selected = _requested_candidates(
            manifest, candidate_ids, required_state="ready_for_review"
        )
        accepted: list[str] = []
        for candidate in selected:
            candidate["review"] = {
                "status": "accepted",
                "reviewer": reviewer.strip(),
                "reviewer_type": reviewer_type,
                "review_method": method,
                "review_artifact_sha256": review_artifact_sha256,
                "reviewed_at": timestamp,
                "candidate_record_sha256": candidate["record_sha256"],
                "candidate_artifact_sha256": _candidate_artifact_hash(
                    candidate,
                    source_sha256=manifest["source"]["sha256"],
                    text_sha256=manifest["source"]["text_sha256"],
                    canonical_binding=manifest["canonical"],
                ),
            }
            candidate["candidate_state"] = "accepted"
            accepted.append(candidate["candidate_id"])
        _write_manifest(manifest_path, manifest)
        return accepted


def load_ai_review_decisions(
    path: Path,
    *,
    run: Path,
    candidate_ids: Iterable[str],
    reviewer: str,
    decision: str,
) -> dict[str, str]:
    """Validate an excerpt-free AI review artifact for exact candidate decisions."""

    expected_decision = decision
    if expected_decision not in {"accept", "reject"}:
        raise ReadingPackError("AI review decision must be accept or reject")

    try:
        artifact_path = path.resolve()
        raw = artifact_path.read_bytes()
    except OSError as exc:
        raise ReadingPackError(f"cannot read AI review artifact {path}: {exc}", EXIT_IO) from exc
    if len(raw) > MAX_AI_REVIEW_BYTES:
        raise ReadingPackError(
            f"AI review artifact exceeds {MAX_AI_REVIEW_BYTES} bytes", EXIT_IO
        )
    try:
        artifact = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ReadingPackError(f"AI review artifact is not valid UTF-8 JSON: {exc}") from exc
    require_structure("ai-review.schema.json", artifact, label="AI review artifact")
    if not isinstance(artifact, dict) or set(artifact) != {
        "schema_version", "run_id", "run_integrity_sha256", "reviewer", "decisions",
    }:
        raise ReadingPackError("AI review artifact has invalid top-level fields")
    manifest = load_candidate_run(run)
    if (
        artifact.get("schema_version") != 1
        or artifact.get("run_id") != manifest.get("run_id")
        or artifact.get("run_integrity_sha256") != manifest.get("integrity_sha256")
    ):
        raise ReadingPackError("AI review artifact is stale or bound to another run")
    reviewer_record = artifact.get("reviewer")
    if not isinstance(reviewer_record, dict) or set(reviewer_record) != {
        "type", "name", "method", "reviewed_at",
    }:
        raise ReadingPackError("AI review artifact reviewer is invalid")
    name = reviewer_record.get("name")
    method = reviewer_record.get("method")
    reviewed_at = reviewer_record.get("reviewed_at")
    if (
        reviewer_record.get("type") != "ai"
        or name != reviewer.strip()
        or not isinstance(name, str)
        or not name
        or len(name) > 500
        or UNSAFE_TEXT.search(name)
        or not isinstance(method, str)
        or not method
        or len(method) > 500
        or UNSAFE_TEXT.search(method)
        or not isinstance(reviewed_at, str)
        or not reviewed_at
        or len(reviewed_at) > 200
        or UNSAFE_TEXT.search(reviewed_at)
    ):
        raise ReadingPackError("AI review artifact reviewer provenance is invalid")
    requested = list(dict.fromkeys(candidate_ids))
    selected = _requested_candidates(
        manifest, requested, required_state="ready_for_review"
    )
    decisions = artifact.get("decisions")
    if not isinstance(decisions, list) or len(decisions) != len(selected):
        raise ReadingPackError("AI review artifact decisions do not match requested candidates")
    by_id = {candidate["candidate_id"]: candidate for candidate in selected}
    seen: set[str] = set()
    for decision_record in decisions:
        if not isinstance(decision_record, dict) or set(decision_record) != {
            "candidate_id", "record_sha256", "candidate_artifact_sha256",
            "decision", "checks",
        }:
            raise ReadingPackError("AI review artifact contains an invalid decision")
        candidate_id = decision_record.get("candidate_id")
        candidate = by_id.get(candidate_id)
        checks = decision_record.get("checks")
        if (
            candidate is None
            or candidate_id in seen
            or decision_record.get("record_sha256") != candidate.get("record_sha256")
            or decision_record.get("candidate_artifact_sha256")
            != candidate.get("review", {}).get("candidate_artifact_sha256")
            or decision_record.get("decision") != expected_decision
            or not isinstance(checks, list)
            or set(checks) != AI_REVIEW_CHECKS
            or len(checks) != len(AI_REVIEW_CHECKS)
        ):
            raise ReadingPackError(
                f"AI review decision is stale, incomplete, or not {expected_decision}ed"
            )
        seen.add(candidate_id)
    if seen != set(requested):
        raise ReadingPackError("AI review artifact does not cover every requested candidate")
    return {
        "method": method,
        "reviewed_at": reviewed_at,
        "artifact_sha256": hashlib.sha256(raw).hexdigest(),
    }


def _record_at(
    canonical: Mapping[str, Any], collection: str, identifier: str
) -> tuple[int | None, Mapping[str, Any] | None]:
    records = canonical.get(collection, [])
    if not isinstance(records, list):
        return None, None
    for index, record in enumerate(records):
        if isinstance(record, Mapping) and record.get("id") == identifier:
            return index, record
    return None, None


def _commit_application_receipt(
    manifest: dict[str, Any], transaction: Mapping[str, Any]
) -> list[str]:
    application = _application_record(transaction)
    existing = manifest.get("application")
    if existing is not None and existing != application:
        raise ReadingPackError("candidate run already has another application receipt")
    candidate_ids = application["candidate_ids"]
    candidate_set = set(candidate_ids)
    for candidate in manifest["candidates"]:
        if candidate.get("candidate_id") in candidate_set:
            candidate["candidate_state"] = "applied"
    manifest["application"] = application
    manifest["transaction"] = None
    return list(candidate_ids)


def _recover_prepared_transaction(
    manifest_path: Path,
    manifest: dict[str, Any],
    canonical: Mapping[str, Any],
    project_data_by_lang: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    transaction = manifest.get("transaction")
    if transaction is None:
        return []
    current_hash = _value_hash(canonical)
    current_project_hash = _value_hash(project_data_by_lang)
    candidate_ids = transaction["candidate_ids"]
    candidate_set = set(candidate_ids)
    if (
        current_hash == transaction["before_sha256"]
        and current_project_hash == transaction["before_project_sha256"]
    ):
        for candidate in manifest["candidates"]:
            if candidate.get("candidate_id") in candidate_set:
                candidate["candidate_state"] = "accepted"
        manifest["transaction"] = None
        _write_manifest(manifest_path, manifest)
        return []
    if (
        current_hash == transaction["after_sha256"]
        and current_project_hash == transaction["after_project_sha256"]
    ):
        committed = _commit_application_receipt(manifest, transaction)
        _write_manifest(manifest_path, manifest)
        return committed
    raise ReadingPackError(
        "cannot recover candidate transaction because canonical data diverged"
    )


def apply_candidate_run(
    project: Path,
    *,
    language: str,
    run: Path,
    source_path: Path,
    candidate_ids: Iterable[str] | None = None,
) -> list[str]:
    """CAS-apply explicitly accepted candidates as canonical ``draft`` records."""

    project = project.resolve()
    source_path = source_path.resolve()
    manifest_path = _manifest_path(run)
    requested = list(candidate_ids) if candidate_ids is not None else None
    with project_lock(project), _manifest_lock(manifest_path):
        manifest = load_candidate_run(manifest_path)
        config = load_config(project)
        project_data_by_lang = {
            configured_language: load_language_data(project, configured_language)
            for configured_language in config.get("languages", [])
        }
        if language not in project_data_by_lang:
            raise ReadingPackError("candidate language is not configured in the project")
        canonical = project_data_by_lang[language]
        recovered = _recover_prepared_transaction(
            manifest_path, manifest, canonical, project_data_by_lang
        )
        if recovered:
            if requested is not None and set(requested).issubset(set(recovered)):
                return requested
            manifest = load_candidate_run(manifest_path)
            project_data_by_lang = {
                configured_language: load_language_data(project, configured_language)
                for configured_language in config.get("languages", [])
            }
            canonical = project_data_by_lang[language]
        elif manifest.get("transaction") is not None:
            manifest = load_candidate_run(manifest_path)

        if manifest.get("language") != language:
            raise ReadingPackError("candidate run language does not match the target language")
        normalized_source, _ = _verify_source_and_evidence(manifest, source_path)
        canonical_source = canonical.get("source", {})
        source_role = manifest["source"].get("role", "primary-book")
        source_id = manifest["source"].get("id", "SRC-1")
        if source_role == "primary-book":
            if (
                not isinstance(canonical_source, Mapping)
                or not canonical_source.get("sha256")
                or canonical_source.get("sha256") != manifest["source"]["sha256"]
                or canonical_source.get("name") != manifest["source"]["name"]
                or canonical_source.get("format") != manifest["source"]["format"]
            ):
                raise ReadingPackError(
                    "candidate run source does not match the imported canonical source"
                )
        else:
            # A support-source run remains bound to the project registry at
            # application time.  The caller still supplies the exact source
            # file for evidence revalidation above; no path is persisted.
            from reading_pack.source_registry import registered_source

            registered = registered_source(project, source_id)
            if any(
                registered.get(key) != manifest["source"].get(key)
                for key in ("role", "name", "format", "sha256")
            ):
                raise ReadingPackError(
                    "candidate run support source is stale or absent from the source registry"
                )
        before_hash = _value_hash(canonical)
        if manifest["canonical"]["data_sha256"] != before_hash:
            raise ReadingPackError(
                "canonical data changed after candidate generation; create a fresh run"
            )
        before_project_hash = _value_hash(project_data_by_lang)
        if manifest["canonical"]["project_data_sha256"] != before_project_hash:
            raise ReadingPackError(
                "canonical project data changed after candidate generation; create a fresh run"
            )
        selected = _requested_candidates(
            manifest, requested, required_state="accepted"
        )
        selected_record_ids = [candidate["record_id"] for candidate in selected]
        if len(selected_record_ids) != len(set(selected_record_ids)):
            raise ReadingPackError("selected candidates contain duplicate record IDs")
        languages = config.get("languages", [])
        primary_language = config.get("primary_language")
        if len(languages) > 1 and language == primary_language:
            raise ReadingPackError(
                "primary-language candidate application is blocked for bilingual projects; "
                "apply coordinated reviewed translations through the canonical workflow"
            )
        if len(languages) > 1 and any(
            _record_at(canonical, candidate["collection"], candidate["record_id"])[1]
            is None
            for candidate in selected
        ):
            raise ReadingPackError(
                "candidate application cannot add an unpaired record to a bilingual project"
            )

        policy_data = manifest.get("leak_policy", {})
        try:
            policy = LeakPolicy(
                max_contiguous_characters=int(policy_data["max_contiguous_characters"])
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ReadingPackError("candidate run has an invalid leak policy") from exc
        source_copy_index = _SourceCopyIndex(normalized_source, policy)

        updated = copy.deepcopy(canonical)
        for collection in COLLECTIONS:
            updated.setdefault(collection, [])
        future_chapters = {
            record.get("id")
            for record in updated.get("chapters", [])
            if isinstance(record, dict)
        }
        global_ids = {
            record.get("id"): collection
            for collection in COLLECTIONS
            for record in updated.get(collection, [])
            if isinstance(record, dict) and record.get("id")
        }
        applied_ids: list[str] = []
        for candidate in selected:
            collection = candidate["collection"]
            record = copy.deepcopy(candidate["record"])
            identifier = record["id"]
            existing_index, existing = _record_at(updated, collection, identifier)
            base_hash = candidate["base_record_sha256"]
            if base_hash:
                if existing is None or _value_hash(existing) != base_hash:
                    raise ReadingPackError(
                        "a candidate base record is stale; create a fresh run"
                    )
            elif existing is not None:
                raise ReadingPackError(
                    "a new candidate now conflicts with an existing canonical record"
                )
            existing_collection = global_ids.get(identifier)
            if existing_collection is not None and existing_collection != collection:
                raise ReadingPackError("candidate ID conflicts with another collection")

            if collection == "chapters":
                if existing is None:
                    raise ReadingPackError(
                        "chapter candidates cannot create structure; apply an import plan first"
                    )
                if any(
                    record.get(field) != existing.get(field)
                    for field in CHAPTER_STRUCTURAL_FIELDS
                ):
                    raise ReadingPackError("chapter structure changed after candidate review")
                merged = copy.deepcopy(existing)
                for field in CHAPTER_EDITABLE_FIELDS:
                    if field in record:
                        merged[field] = copy.deepcopy(record[field])
                record = merged
            if "source_locations" not in candidate["record"]:
                source_name = manifest["source"]["name"]
                locations = []
                for evidence in candidate.get("evidence_refs", []):
                    locator = evidence.get("locator", {})
                    if not isinstance(locator, Mapping):
                        continue
                    start = locator.get("char_start")
                    end = locator.get("char_end")
                    if isinstance(start, int) and isinstance(end, int):
                        locations.append(
                            f"{source_name}#normalized-text:{start}-{end}"
                        )
                if locations:
                    record["source_locations"] = list(dict.fromkeys(locations))
            if len(languages) > 1:
                primary_data = project_data_by_lang[primary_language]
                _, primary_record = _record_at(primary_data, collection, identifier)
                if primary_record is None:
                    raise ReadingPackError(
                        "translated candidate has no matching primary-language record"
                    )
                record["source_id"] = identifier
                record["source_hash"] = semantic_hash(dict(primary_record))
                record["translation_status"] = "draft"
            if source_role != "primary-book":
                # Keep support-source provenance distinct from source_id/source_hash,
                # which are reserved for primary-record translation linkage.
                record["provenance_source_id"] = source_id
                record["provenance_source_hash"] = manifest["source"]["sha256"]
            record["status"] = "draft"
            if "translation_status" in record:
                record["translation_status"] = "draft"
            reasons = _record_reasons(collection, record, future_chapters)
            if reasons:
                raise ReadingPackError(
                    "a candidate no longer passes canonical record checks"
                )
            if collection == "names" and not _exact_source_term(record["name"], normalized_source):
                raise ReadingPackError("a name candidate is no longer source-grounded")
            if collection == "glossary" and not _exact_source_term(record["term"], normalized_source):
                raise ReadingPackError("a glossary candidate is no longer source-grounded")
            if _copy_risk(collection, record, source_copy_index, policy):
                raise ReadingPackError("a candidate no longer passes source-copy checks")
            if _extractive_budget_exceeded(collection, record, normalized_source):
                raise ReadingPackError("a candidate exceeds the extractive text budget")
            records = updated.get(collection)
            if not isinstance(records, list):
                raise ReadingPackError("canonical collection is missing")
            if existing_index is None:
                records.append(record)
            else:
                records[existing_index] = record
            global_ids[identifier] = collection
            applied_ids.append(candidate["candidate_id"])

        proposed_project_data = dict(project_data_by_lang)
        proposed_project_data[language] = updated
        validation_errors = errors(validate_data_set(config, proposed_project_data))
        if validation_errors:
            codes = ",".join(sorted({issue.code for issue in validation_errors}))
            raise ReadingPackError(
                f"candidate result fails canonical validation "
                f"({len(validation_errors)} error(s): {codes})"
            )
        after_hash = _value_hash(updated)
        after_project_hash = _value_hash(proposed_project_data)

        # Recheck both external inputs immediately before the prepared write.
        _verify_source_and_evidence(manifest, source_path)
        current_project_data = {
            configured_language: load_language_data(project, configured_language)
            for configured_language in config.get("languages", [])
        }
        current = current_project_data[language]
        if (
            _value_hash(current) != before_hash
            or _value_hash(current_project_data) != before_project_hash
        ):
            raise ReadingPackError("canonical data changed during candidate application")
        applied_set = set(applied_ids)
        for candidate in manifest["candidates"]:
            if candidate.get("candidate_id") in applied_set:
                candidate["candidate_state"] = "applying"
        manifest["transaction"] = {
            "state": "prepared",
            "before_sha256": before_hash,
            "after_sha256": after_hash,
            "before_project_sha256": before_project_hash,
            "after_project_sha256": after_project_hash,
            "candidate_ids": applied_ids,
        }
        _write_manifest(manifest_path, manifest)
        write_json(project / "data" / f"pack.{language}.json", updated)
        _commit_application_receipt(manifest, manifest["transaction"])
        _write_manifest(manifest_path, manifest)
        return applied_ids


def reject_candidates(
    run: Path,
    candidate_ids: Iterable[str],
    *,
    reviewer: str | None = None,
    reviewer_type: str = "human",
    review_method: str | None = None,
    review_artifact_sha256: str = "",
    reviewed_at: str | None = None,
) -> list[str]:
    """Record an optional reviewer-bound rejection without canonical changes."""

    if reviewer is not None:
        if (
            not isinstance(reviewer, str)
            or not reviewer.strip()
            or len(reviewer) > 500
            or UNSAFE_TEXT.search(reviewer)
        ):
            raise ReadingPackError("reviewer must be a non-empty safe string")
        if reviewer_type not in REVIEWER_TYPES:
            raise ReadingPackError("reviewer_type must be human or ai")
        method = review_method or (
            "manual-candidate-review" if reviewer_type == "human" else ""
        )
        if (
            not isinstance(method, str)
            or not method
            or len(method) > 500
            or UNSAFE_TEXT.search(method)
        ):
            raise ReadingPackError("review_method must be a non-empty safe string")
        if reviewer_type == "ai":
            if not re.fullmatch(r"[a-f0-9]{64}", review_artifact_sha256):
                raise ReadingPackError(
                    "AI rejection requires a verified review artifact SHA-256"
                )
        elif review_artifact_sha256:
            raise ReadingPackError("human rejection must not claim an AI review artifact")
        timestamp = reviewed_at or datetime.now(timezone.utc).isoformat()
        if len(timestamp) > 200 or UNSAFE_TEXT.search(timestamp):
            raise ReadingPackError("review timestamp is invalid")
    elif reviewer_type != "human" or review_method or review_artifact_sha256 or reviewed_at:
        raise ReadingPackError("review provenance requires a reviewer")

    manifest_path = _manifest_path(run)
    with _manifest_lock(manifest_path):
        manifest = load_candidate_run(manifest_path)
        if manifest.get("transaction") is not None:
            raise ReadingPackError("candidate run has a pending canonical transaction")
        requested = list(dict.fromkeys(candidate_ids))
        if not requested or any(
            not isinstance(identifier, str)
            or not re.fullmatch(r"CAND-[A-F0-9]{20}", identifier)
            for identifier in requested
        ):
            raise ReadingPackError("one or more candidate IDs are invalid")
        available = {
            candidate.get("candidate_id"): candidate
            for candidate in manifest.get("candidates", [])
        }
        if any(identifier not in available for identifier in requested):
            raise ReadingPackError("one or more candidate IDs were not found")
        for identifier in requested:
            candidate = available[identifier]
            if candidate.get("candidate_state") in {"applying", "applied"}:
                raise ReadingPackError("an applying or applied candidate cannot be rejected")
            candidate["candidate_state"] = "rejected"
            if reviewer is not None:
                candidate["review"] = {
                    "status": "rejected",
                    "reviewer": reviewer.strip(),
                    "reviewer_type": reviewer_type,
                    "review_method": method,
                    "review_artifact_sha256": review_artifact_sha256,
                    "reviewed_at": timestamp,
                    "candidate_record_sha256": candidate["record_sha256"],
                    "candidate_artifact_sha256": _candidate_artifact_hash(
                        candidate,
                        source_sha256=manifest["source"]["sha256"],
                        text_sha256=manifest["source"]["text_sha256"],
                        canonical_binding=manifest["canonical"],
                    ),
                }
        _write_manifest(manifest_path, manifest)
        return sorted(requested)
