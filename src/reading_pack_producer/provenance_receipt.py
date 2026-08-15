"""Integrity-bound handoff receipt for sequentially applied candidate runs."""

from __future__ import annotations

import copy
import hashlib
import json
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from reading_pack.errors import EXIT_IO, ReadingPackError
from reading_pack.project import atomic_write_text, find_project, load_config, load_language_data
from reading_pack.schema_validation import require_structure

from .candidates import _value_hash, _verify_source_and_evidence, load_candidate_run


RECEIPT_SCHEMA_VERSION = 1
MAX_RECEIPT_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class AppliedRunArtifact:
    run: Path
    source_path: Path


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _integrity(value: Mapping[str, Any]) -> str:
    return _value_hash(
        {key: item for key, item in value.items() if key != "integrity_sha256"}
    )


def _receipt_id(value: Mapping[str, Any]) -> str:
    projection = {
        key: item
        for key, item in value.items()
        if key not in {"receipt_id", "integrity_sha256"}
    }
    return f"PR-{_value_hash(projection)[:20].upper()}"


def _ids_sha256(values: Sequence[str]) -> str:
    encoded = json.dumps(
        list(values), ensure_ascii=False, sort_keys=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_provenance_receipt(value: Any) -> dict[str, Any]:
    require_structure(
        "provenance-receipt.schema.json", value, label="provenance receipt"
    )
    if not isinstance(value, Mapping):
        raise ReadingPackError("provenance receipt root must be an object")
    if value.get("receipt_id") != _receipt_id(value):
        raise ReadingPackError("provenance receipt ID binding check failed")
    if value.get("integrity_sha256") != _integrity(value):
        raise ReadingPackError("provenance receipt integrity check failed")
    runs = value.get("runs", [])
    if [item.get("sequence") for item in runs] != list(range(1, len(runs) + 1)):
        raise ReadingPackError("provenance receipt run sequence is invalid")
    if len({item.get("run_id") for item in runs}) != len(runs):
        raise ReadingPackError("provenance receipt contains duplicate run IDs")
    continuity = value["continuity"]
    expected_status = (
        "verified"
        if continuity["verified_links"] == continuity["total_links"]
        else "legacy_unverified"
    )
    if continuity["total_links"] != len(runs) or continuity["status"] != expected_status:
        raise ReadingPackError("provenance receipt continuity summary is invalid")
    return copy.deepcopy(dict(value))


def create_provenance_receipt(
    project: Path,
    *,
    language: str,
    artifacts: Sequence[AppliedRunArtifact],
    allow_legacy: bool = False,
) -> dict[str, Any]:
    """Verify ordered terminal runs and bind them to the current canonical state."""

    project = find_project(project)
    if not artifacts:
        raise ReadingPackError("at least one applied candidate run is required")
    config = load_config(project)
    if language not in config.get("languages", []):
        raise ReadingPackError("provenance receipt language is not configured")
    project_data = {
        configured: load_language_data(project, configured)
        for configured in config["languages"]
    }
    current = {
        "data_sha256": _value_hash(project_data[language]),
        "project_data_sha256": _value_hash(project_data),
    }
    entries: list[dict[str, Any]] = []
    seen_run_ids: set[str] = set()
    for sequence, specification in enumerate(artifacts, start=1):
        manifest = load_candidate_run(specification.run)
        run_id = manifest["run_id"]
        if run_id in seen_run_ids:
            raise ReadingPackError(f"duplicate provenance run: {run_id}")
        seen_run_ids.add(run_id)
        if manifest["language"] != language:
            raise ReadingPackError("all provenance runs must use the requested language")
        if manifest.get("transaction") is not None:
            raise ReadingPackError("provenance run has a pending canonical transaction")
        states = {candidate["candidate_state"] for candidate in manifest["candidates"]}
        if not states <= {"applied", "rejected"} or manifest["summary"]["applied"] == 0:
            raise ReadingPackError(
                "provenance runs must be terminal and contain an applied candidate"
            )
        _verify_source_and_evidence(manifest, specification.source_path.resolve())
        application = manifest.get("application")
        if application is None and not allow_legacy:
            raise ReadingPackError(
                "applied run predates durable application receipts; use --allow-legacy "
                "to record an explicitly unverified historical link"
            )
        applied_ids = sorted(
            candidate["candidate_id"]
            for candidate in manifest["candidates"]
            if candidate["candidate_state"] == "applied"
        )
        rejected_ids = sorted(
            candidate["candidate_id"]
            for candidate in manifest["candidates"]
            if candidate["candidate_state"] == "rejected"
        )
        review_hashes = sorted(
            {
                candidate["review"].get("review_artifact_sha256", "")
                for candidate in manifest["candidates"]
                if candidate["candidate_state"] == "applied"
                and candidate["review"].get("review_artifact_sha256", "")
            }
        )
        entries.append(
            {
                "sequence": sequence,
                "run_id": run_id,
                "run_integrity_sha256": manifest["integrity_sha256"],
                "source": copy.deepcopy(manifest["source"]),
                "canonical_before": copy.deepcopy(manifest["canonical"]),
                "application": copy.deepcopy(application),
                "summary": {
                    "total": manifest["summary"]["total"],
                    "applied": len(applied_ids),
                    "rejected": len(rejected_ids),
                },
                "applied_candidate_ids_sha256": _ids_sha256(applied_ids),
                "rejected_candidate_ids_sha256": _ids_sha256(rejected_ids),
                "review_artifact_sha256s": review_hashes,
            }
        )

    verified_links = 0
    for index, entry in enumerate(entries):
        target = entries[index + 1]["canonical_before"] if index + 1 < len(entries) else current
        application = entry["application"]
        if application is None:
            continue
        if (
            application["after_sha256"] != target["data_sha256"]
            or application["after_project_sha256"] != target["project_data_sha256"]
        ):
            raise ReadingPackError(
                f"provenance run {entry['run_id']} does not connect to the next canonical state"
            )
        verified_links += 1

    receipt: dict[str, Any] = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "receipt_id": "",
        "project": {
            "slug": config["slug"],
            "config_sha256": _value_hash(config),
        },
        "language": language,
        "canonical": current,
        "continuity": {
            "status": "verified" if verified_links == len(entries) else "legacy_unverified",
            "verified_links": verified_links,
            "total_links": len(entries),
        },
        "runs": entries,
        "integrity_sha256": "",
    }
    receipt["receipt_id"] = _receipt_id(receipt)
    receipt["integrity_sha256"] = _integrity(receipt)
    return validate_provenance_receipt(receipt)


def write_provenance_receipt(path: Path, receipt: Mapping[str, Any]) -> Path:
    checked = validate_provenance_receipt(receipt)
    destination = path.resolve()
    if destination.exists():
        raise ReadingPackError(
            f"refusing to overwrite provenance receipt: {destination}", EXIT_IO
        )
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        encoded = _json_bytes(checked)
        if len(encoded) > MAX_RECEIPT_BYTES:
            raise ReadingPackError(
                f"provenance receipt exceeds {MAX_RECEIPT_BYTES} bytes", EXIT_IO
            )
        atomic_write_text(destination, encoded.decode("utf-8"))
        destination.chmod(0o600)
    except ReadingPackError:
        raise
    except OSError as exc:
        raise ReadingPackError(
            f"cannot write provenance receipt {destination}: {exc}", EXIT_IO
        ) from exc
    return destination


def load_provenance_receipt(path: Path) -> dict[str, Any]:
    source = path.resolve()
    try:
        info = source.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_RECEIPT_BYTES:
            raise ReadingPackError("provenance receipt is not a bounded regular file", EXIT_IO)
        value = json.loads(source.read_text(encoding="utf-8"))
    except ReadingPackError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReadingPackError(f"cannot read provenance receipt {source}: {exc}", EXIT_IO) from exc
    return validate_provenance_receipt(value)
