"""Closed, producer-declared semantics for official companion references."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit


COMPANION_FIELDS = frozenset({"relation", "url_scope", "retrieval_policy"})
COMPANION_RELATION = "official_companion"
COMPANION_RETRIEVAL_POLICY = "proactive_when_relevant"
COMPANION_URL_SCOPES = frozenset({"exact", "prefix"})
MAX_COMPANION_REFERENCES = 32
MAX_COMPANION_URL_CHARACTERS = 2_048

_UNSAFE_URL_TEXT = frozenset(
    chr(value) for value in (*range(0x00, 0x20), *range(0x7F, 0xA0))
) | frozenset("\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")


@dataclass(frozen=True)
class CompanionFinding:
    kind: str
    index: int | None
    field: str
    message: str


def is_companion_reference(record: Mapping[str, Any]) -> bool:
    """Return true only for a complete supported companion declaration."""

    return (
        record.get("relation") == COMPANION_RELATION
        and record.get("url_scope") in COMPANION_URL_SCOPES
        and record.get("retrieval_policy") == COMPANION_RETRIEVAL_POLICY
    )


def _normalized_url_identity(value: str) -> tuple[str, str, int, str, str, str] | None:
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme.lower() != "https" or not hostname:
        return None
    return (
        "https",
        hostname.lower(),
        port or 443,
        parsed.path or "/",
        parsed.query,
        parsed.fragment,
    )


def companion_findings(
    records: Sequence[Mapping[str, Any] | Any],
) -> list[CompanionFinding]:
    """Validate the optional companion declarations in a reference collection."""

    findings: list[CompanionFinding] = []
    declared: list[tuple[int, Mapping[str, Any]]] = []
    for index, raw in enumerate(records):
        if not isinstance(raw, Mapping):
            continue
        present = COMPANION_FIELDS.intersection(raw)
        if not present:
            continue
        declared.append((index, raw))
        missing = COMPANION_FIELDS - set(raw)
        if missing:
            findings.append(
                CompanionFinding(
                    "declaration",
                    index,
                    "relation",
                    "companion declarations must provide relation, url_scope, and retrieval_policy together",
                )
            )
            continue
        if raw.get("relation") != COMPANION_RELATION:
            findings.append(
                CompanionFinding(
                    "declaration",
                    index,
                    "relation",
                    f"must equal {COMPANION_RELATION}",
                )
            )
        if raw.get("url_scope") not in COMPANION_URL_SCOPES:
            findings.append(
                CompanionFinding(
                    "declaration",
                    index,
                    "url_scope",
                    f"must be one of {sorted(COMPANION_URL_SCOPES)}",
                )
            )
        if raw.get("retrieval_policy") != COMPANION_RETRIEVAL_POLICY:
            findings.append(
                CompanionFinding(
                    "declaration",
                    index,
                    "retrieval_policy",
                    f"must equal {COMPANION_RETRIEVAL_POLICY}",
                )
            )

    if len(declared) > MAX_COMPANION_REFERENCES:
        findings.append(
            CompanionFinding(
                "collection",
                None,
                "references",
                f"must contain at most {MAX_COMPANION_REFERENCES} companion declarations",
            )
        )

    identities: dict[tuple[str, str, int, str, str, str], int] = {}
    for index, record in declared:
        value = record.get("url")
        if not isinstance(value, str):
            findings.append(
                CompanionFinding("url", index, "url", "must be an HTTPS URL string")
            )
            continue
        invalid_text = (
            not value
            or len(value) > MAX_COMPANION_URL_CHARACTERS
            or any(character.isspace() or character in _UNSAFE_URL_TEXT for character in value)
        )
        try:
            parsed = urlsplit(value)
            hostname = parsed.hostname
            _ = parsed.port
        except ValueError:
            parsed = None
            hostname = None
        if (
            invalid_text
            or parsed is None
            or parsed.scheme.lower() != "https"
            or not hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            findings.append(
                CompanionFinding(
                    "url",
                    index,
                    "url",
                    "must be an absolute HTTPS URL of at most 2048 characters without credentials or unsafe text",
                )
            )
            continue
        if record.get("url_scope") == "prefix" and (
            not parsed.path.endswith("/") or parsed.query or parsed.fragment
        ):
            findings.append(
                CompanionFinding(
                    "url",
                    index,
                    "url",
                    "a prefix URL must end in / and must not contain a query or fragment",
                )
            )
        identity = _normalized_url_identity(value)
        if identity is None:
            continue
        if identity in identities:
            findings.append(
                CompanionFinding(
                    "collection",
                    index,
                    "url",
                    f"duplicates companion URL declared at references[{identities[identity]}]",
                )
            )
        else:
            identities[identity] = index
    return findings
