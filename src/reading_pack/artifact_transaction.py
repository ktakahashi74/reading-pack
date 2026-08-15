"""Recoverable transactions for canonical project artifacts.

Domain modules remain responsible for building and validating their plans.
This module owns the common compare-and-swap boundary: hash the exact before
and after values, persist a prepared record, atomically write every artifact,
and restore the previous values after a known failure or interrupted apply.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Literal, Mapping

from .errors import EXIT_IO, ReadingPackError
from .project import atomic_write_text, write_json


ArtifactKind = Literal["json", "text"]
PathPolicy = Callable[[str, ArtifactKind], bool]
Validator = Callable[[], None]


@dataclass(frozen=True)
class ArtifactChange:
    """One project-relative artifact replacement."""

    path: str
    kind: ArtifactKind
    before: Any
    after: Any
    before_exists: bool = True


def json_hash(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def text_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def artifact_hash(kind: ArtifactKind, value: Any) -> str:
    if kind == "json":
        return json_hash(value)
    if kind == "text" and isinstance(value, str):
        return text_hash(value)
    raise ReadingPackError("artifact transaction value has an invalid kind")


def _safe_relative(path: str) -> bool:
    candidate = PurePosixPath(path)
    return (
        bool(path)
        and not candidate.is_absolute()
        and ".." not in candidate.parts
        and candidate.as_posix() == path
    )


def _strict_json(path: Path, maximum: int, label: str) -> Any:
    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in values:
            if key in result:
                raise ValueError(f"duplicate object key: {key}")
            result[key] = value
        return result

    try:
        if path.stat().st_size > maximum:
            raise ReadingPackError(f"{label} exceeds {maximum} bytes", EXIT_IO)
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON number: {value}")
            ),
        )
    except ReadingPackError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ReadingPackError(f"cannot read {label} {path}: {exc}", EXIT_IO) from exc


_MISSING = object()


def _read_artifact(
    project: Path, path: str, kind: ArtifactKind, *, allow_missing: bool = False
) -> Any:
    target = project / path
    if allow_missing and not target.exists():
        return _MISSING
    if kind == "json":
        return _strict_json(target, 64 * 1024 * 1024, "transaction artifact")
    try:
        return target.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ReadingPackError(f"cannot read transaction artifact {target}: {exc}", EXIT_IO) from exc


def _write_artifact(project: Path, path: str, kind: ArtifactKind, value: Any) -> None:
    target = project / path
    if kind == "json":
        write_json(target, value)
    elif kind == "text" and isinstance(value, str):
        atomic_write_text(target, value)
    else:  # pragma: no cover - guarded when changes are normalized
        raise ReadingPackError("artifact transaction value has an invalid kind")


def _prepared_path(project: Path, prepared_name: str) -> Path:
    if not _safe_relative(prepared_name) or "/" in prepared_name:
        raise ReadingPackError("artifact transaction prepared name is unsafe", EXIT_IO)
    return project / ".reading-pack" / prepared_name


def _validate_record(
    value: Any,
    *,
    path_policy: PathPolicy,
    label: str,
) -> list[dict[str, Any]]:
    if (
        not isinstance(value, Mapping)
        or set(value) != {"schema_version", "artifacts"}
        or value.get("schema_version") != 1
        or not isinstance(value.get("artifacts"), list)
        or not value["artifacts"]
    ):
        raise ReadingPackError(
            f"prepared {label} transaction is invalid; manual recovery is required",
            EXIT_IO,
        )
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in value["artifacts"]:
        if (
            not isinstance(item, Mapping)
            or set(item) != {
                "path", "kind", "before", "before_exists", "after_sha256"
            }
            or item.get("kind") not in {"json", "text"}
            or not isinstance(item.get("path"), str)
            or not isinstance(item.get("before_exists"), bool)
            or not _safe_relative(item["path"])
            or item["path"] in seen
            or not path_policy(item["path"], item["kind"])
            or not isinstance(item.get("after_sha256"), str)
            or len(item["after_sha256"]) != 64
            or any(character not in "0123456789abcdef" for character in item["after_sha256"])
        ):
            raise ReadingPackError(
                f"prepared {label} transaction is invalid; manual recovery is required",
                EXIT_IO,
            )
        try:
            artifact_hash(item["kind"], item["before"])
        except ReadingPackError as exc:
            raise ReadingPackError(
                f"prepared {label} transaction is invalid; manual recovery is required",
                EXIT_IO,
            ) from exc
        seen.add(item["path"])
        result.append(dict(item))
    return result


def recover_artifact_transaction(
    project: Path,
    *,
    prepared_name: str,
    path_policy: PathPolicy,
    label: str,
    maximum_bytes: int = 64 * 1024 * 1024,
) -> bool:
    """Restore a previously prepared transaction after checking for conflicts."""

    project = Path(project).resolve()
    prepared = _prepared_path(project, prepared_name)
    if not prepared.exists():
        return False
    items = _validate_record(
        _strict_json(prepared, maximum_bytes, f"prepared {label} transaction"),
        path_policy=path_policy,
        label=label,
    )
    for item in items:
        current = _read_artifact(
            project, item["path"], item["kind"], allow_missing=True
        )
        before_sha256 = artifact_hash(item["kind"], item["before"])
        valid = (
            (current is _MISSING and not item["before_exists"])
            or (
                current is not _MISSING
                and artifact_hash(item["kind"], current)
                in {before_sha256, item["after_sha256"]}
            )
        )
        if not valid:
            raise ReadingPackError(
                f"prepared {label} transaction overlaps an unknown edit; manual recovery is required",
                EXIT_IO,
            )
    for item in items:
        if item["before_exists"]:
            _write_artifact(project, item["path"], item["kind"], item["before"])
        else:
            (project / item["path"]).unlink(missing_ok=True)
    prepared.unlink()
    return True


def apply_artifact_transaction(
    project: Path,
    *,
    prepared_name: str,
    changes: list[ArtifactChange],
    path_policy: PathPolicy,
    label: str,
    validate_after: Validator | None = None,
) -> None:
    """Apply exact artifact replacements with a recoverable prepared record.

    The caller must hold the project lock while building ``changes`` and while
    calling this function. Existing prepared work must be recovered first with
    :func:`recover_artifact_transaction`.
    """

    project = Path(project).resolve()
    normalized: list[ArtifactChange] = []
    seen: set[str] = set()
    for change in changes:
        if (
            change.kind not in {"json", "text"}
            or not _safe_relative(change.path)
            or change.path in seen
            or not path_policy(change.path, change.kind)
        ):
            raise ReadingPackError(f"{label} transaction target is unsafe", EXIT_IO)
        artifact_hash(change.kind, change.before)
        artifact_hash(change.kind, change.after)
        current = _read_artifact(
            project, change.path, change.kind, allow_missing=True
        )
        current_matches = (
            (current is _MISSING and not change.before_exists)
            or (
                current is not _MISSING
                and change.before_exists
                and artifact_hash(change.kind, current)
                == artifact_hash(change.kind, change.before)
            )
        )
        if not current_matches:
            raise ReadingPackError(f"{label} transaction target changed during apply", EXIT_IO)
        seen.add(change.path)
        if not change.before_exists or change.before != change.after:
            normalized.append(change)
    if not normalized:
        if validate_after is not None:
            validate_after()
        return

    prepared = _prepared_path(project, prepared_name)
    if prepared.exists():
        raise ReadingPackError(f"prepared {label} transaction already exists", EXIT_IO)
    write_json(
        prepared,
        {
            "schema_version": 1,
            "artifacts": [
                {
                    "path": change.path,
                    "kind": change.kind,
                    "before": change.before,
                    "before_exists": change.before_exists,
                    "after_sha256": artifact_hash(change.kind, change.after),
                }
                for change in normalized
            ],
        },
    )
    try:
        for change in normalized:
            _write_artifact(project, change.path, change.kind, change.after)
        if validate_after is not None:
            validate_after()
    except BaseException:
        # Ordinary failures recover immediately. A killed process leaves the
        # prepared record for the next locked invocation.
        for change in normalized:
            if change.before_exists:
                _write_artifact(project, change.path, change.kind, change.before)
            else:
                (project / change.path).unlink(missing_ok=True)
        prepared.unlink(missing_ok=True)
        raise
    prepared.unlink()
