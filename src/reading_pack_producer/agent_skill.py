"""Optional deterministic Agent Skills distribution for a Reading Pack."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from reading_pack.errors import EXIT_CHECK, EXIT_IO, EXIT_VALIDATION, ReadingPackError
from reading_pack.rendering import output_path, render_pack


AGENT_SKILL_FORMAT_VERSION = "1"
MAX_SKILL_NAME_LENGTH = 64
MAX_REFERENCE_BYTES = 16 * 1024 * 1024
MAX_SKILL_BYTES = 128 * 1024
MAX_MANIFEST_BYTES = 256 * 1024
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_DISTRIBUTION_FILES = 66
MAX_ARCHIVE_PATH_BYTES = 512
FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)

_SKILL_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_SAFE_FILE_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _yaml_string(value: str) -> str:
    # JSON double-quoted strings are also valid YAML scalars.
    return json.dumps(value, ensure_ascii=False)


def validate_skill_name(name: Any) -> str:
    if not isinstance(name, str) or not name:
        raise ReadingPackError(
            "Agent Skill name must be a non-empty string", EXIT_VALIDATION
        )
    if len(name) > MAX_SKILL_NAME_LENGTH:
        raise ReadingPackError(
            f"Agent Skill name exceeds {MAX_SKILL_NAME_LENGTH} characters; "
            "change the project slug explicitly (automatic shortening is not allowed)",
            EXIT_VALIDATION,
        )
    if not _SKILL_NAME.fullmatch(name):
        raise ReadingPackError(
            "Agent Skill name must contain only lowercase ASCII letters, digits, and "
            "single hyphens, and must not start or end with a hyphen",
            EXIT_VALIDATION,
        )
    return name


def _safe_file_segment(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _SAFE_FILE_SEGMENT.fullmatch(value):
        raise ReadingPackError(
            f"unsafe {label} for Agent Skill distribution: {value!r}",
            EXIT_VALIDATION,
        )
    if value in {".", ".."} or ".." in value:
        raise ReadingPackError(
            f"unsafe {label} for Agent Skill distribution: {value!r}",
            EXIT_VALIDATION,
        )
    return value


def _skill_markdown(config: dict[str, Any], references: list[tuple[str, str]]) -> bytes:
    name = validate_skill_name(config.get("slug"))
    book = config.get("book", {})
    title = str(book.get("title", "this book"))
    license_value = str(book.get("pack_license", ""))
    primary = str(config.get("primary_language", ""))
    languages = [language for language, _ in references]
    description = (
        f"Use when a reader asks about {title}: consult its reviewed Reading Pack to "
        "locate topics, explain recorded claims, and guide the reader back to the book."
    )
    compatibility = (
        "Requires a host that can read bundled Markdown references. Network access is "
        "optional and may be used only when the selected Reading Pack requests it."
    )
    if not 1 <= len(description) <= 1024:
        raise ReadingPackError(
            "Agent Skill description must contain 1 to 1024 characters",
            EXIT_VALIDATION,
        )
    if not 1 <= len(compatibility) <= 500:
        raise ReadingPackError(
            "Agent Skill compatibility must contain 1 to 500 characters",
            EXIT_VALIDATION,
        )
    metadata = {
        "reading-pack-languages": ",".join(languages),
        "reading-pack-manifest": "manifest.json",
        "reading-pack-primary-language": primary,
        "reading-pack-status": str(config.get("status", "")),
        "reading-pack-version": str(config.get("version", "")),
    }
    lines = [
        "---",
        f"name: {_yaml_string(name)}",
        f"description: {_yaml_string(description)}",
        f"license: {_yaml_string(license_value)}",
        f"compatibility: {_yaml_string(compatibility)}",
        "metadata:",
    ]
    lines.extend(
        f"  {key}: {_yaml_string(value)}" for key, value in sorted(metadata.items())
    )
    lines.extend(
        [
            "---",
            "",
            "# Reading Pack",
            "",
            "Use the bundled Reading Pack as follows:",
            "",
            f"1. Identify the language of the reader's question. Use the matching reference when it is available; otherwise use the primary language ({primary}).",
            "2. Read the selected reference before answering.",
            "3. Follow the selected Reading Pack's `SYS` section when composing the answer.",
            "4. Keep the Reading Pack's distinction between verified information, interpretation, uncertainty, and directions back to the book.",
            "5. Use web access only if the host provides it and the selected Reading Pack requests it. Otherwise, do not browse.",
            "",
            "Bundled references:",
            "",
        ]
    )
    for language, path in references:
        role = "primary" if language == primary else "translation"
        lines.append(f"- `{language}` ({role}): [{path}]({path})")
    lines.append("")
    value = "\n".join(lines).encode("utf-8")
    if len(value) > MAX_SKILL_BYTES:
        raise ReadingPackError("generated SKILL.md is too large", EXIT_VALIDATION)
    return value


def _read_regular_file(path: Path, maximum: int, label: str, exit_code: int) -> bytes:
    try:
        before = path.lstat()
    except FileNotFoundError as exc:
        raise ReadingPackError(f"{label} is missing: {path}", exit_code) from exc
    except OSError as exc:
        raise ReadingPackError(f"cannot inspect {label} {path}: {exc}", exit_code) from exc
    if not stat.S_ISREG(before.st_mode):
        raise ReadingPackError(f"{label} is not a regular file: {path}", exit_code)
    if before.st_size > maximum:
        raise ReadingPackError(f"{label} exceeds {maximum} bytes: {path}", exit_code)
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
        try:
            opened = os.fstat(descriptor)
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                raise ReadingPackError(f"{label} changed while it was opened", exit_code)
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = os.read(descriptor, min(1024 * 1024, maximum + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > maximum:
                    raise ReadingPackError(
                        f"{label} exceeds {maximum} bytes: {path}", exit_code
                    )
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
    except ReadingPackError:
        raise
    except OSError as exc:
        raise ReadingPackError(f"cannot read {label} {path}: {exc}", exit_code) from exc
    if (after.st_size, after.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
        raise ReadingPackError(f"{label} changed while it was read", exit_code)
    return b"".join(chunks)


def _ordinary_pack_artifacts(
    project: Path,
    config: dict[str, Any],
    data_by_lang: dict[str, dict[str, Any]],
) -> list[tuple[str, str, bytes]]:
    basename = _safe_file_segment(
        config.get("output_basename", "reading-pack"), "output_basename"
    )
    languages = config.get("languages", [])
    if not isinstance(languages, list) or not languages:
        raise ReadingPackError(
            "Agent Skill requires at least one configured language", EXIT_VALIDATION
        )
    if len(languages) + 2 > MAX_DISTRIBUTION_FILES:
        raise ReadingPackError("Agent Skill contains too many files", EXIT_VALIDATION)
    result: list[tuple[str, str, bytes]] = []
    seen: set[str] = set()
    for language in languages:
        language = _safe_file_segment(language, "language")
        if language not in data_by_lang:
            raise ReadingPackError(
                f"canonical data is missing for configured language: {language}",
                EXIT_VALIDATION,
            )
        reference_path = f"references/{basename}.{language}.md"
        if reference_path in seen:
            raise ReadingPackError(
                f"duplicate Agent Skill reference path: {reference_path}",
                EXIT_VALIDATION,
            )
        seen.add(reference_path)
        expected = render_pack(project, language, config, data_by_lang[language]).encode(
            "utf-8"
        )
        maximum_characters = config.get("limits", {}).get(
            "max_pack_characters", 100000
        )
        if len(expected.decode("utf-8")) > maximum_characters:
            raise ReadingPackError(
                f"generated pack for {language} exceeds max_pack_characters",
                EXIT_VALIDATION,
            )
        if len(expected) > MAX_REFERENCE_BYTES:
            raise ReadingPackError(
                f"generated pack for {language} exceeds {MAX_REFERENCE_BYTES} bytes",
                EXIT_VALIDATION,
            )
        ordinary = output_path(project, config, language)
        current = _read_regular_file(
            ordinary,
            MAX_REFERENCE_BYTES,
            f"ordinary Reading Pack for {language}",
            EXIT_CHECK,
        )
        if current != expected:
            raise ReadingPackError(
                f"ordinary Reading Pack is stale: {ordinary.relative_to(project)}; "
                "run reading-pack build first",
                EXIT_CHECK,
            )
        result.append((language, reference_path, current))
    return result


def expected_agent_skill(
    project: Path,
    config: dict[str, Any],
    data_by_lang: dict[str, dict[str, Any]],
) -> dict[str, bytes]:
    """Return all expected files after proving ordinary Packs are current."""

    name = validate_skill_name(config.get("slug"))
    ordinary = _ordinary_pack_artifacts(project, config, data_by_lang)
    references = [(language, path) for language, path, _ in ordinary]
    skill = _skill_markdown(config, references)
    files: dict[str, bytes] = {"SKILL.md": skill}
    files.update({path: value for _, path, value in ordinary})
    manifest_files = [
        {"bytes": len(value), "path": path, "sha256": _sha256(value)}
        for path, value in sorted(files.items())
    ]
    manifest = {
        "files": manifest_files,
        "format_version": AGENT_SKILL_FORMAT_VERSION,
        "kind": "reading-pack-agent-skill",
        "reading_pack": {
            "languages": [language for language, _, _ in ordinary],
            "license": str(config.get("book", {}).get("pack_license", "")),
            "primary_language": str(config.get("primary_language", "")),
            "status": str(config.get("status", "")),
            "version": str(config.get("version", "")),
        },
        "skill": {"name": name},
    }
    manifest_bytes = _json_bytes(manifest)
    if len(manifest_bytes) > MAX_MANIFEST_BYTES:
        raise ReadingPackError("generated Agent Skill manifest is too large", EXIT_VALIDATION)
    files["manifest.json"] = manifest_bytes
    return files


def _archive_name_is_safe(name: str, skill_name: str) -> bool:
    if not name or "\\" in name or "\x00" in name:
        return False
    try:
        if len(name.encode("utf-8")) > MAX_ARCHIVE_PATH_BYTES:
            return False
    except UnicodeEncodeError:
        return False
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return False
    return len(path.parts) >= 2 and path.parts[0] == skill_name


def archive_bytes(skill_name: str, files: dict[str, bytes]) -> bytes:
    validate_skill_name(skill_name)
    if len(files) > MAX_DISTRIBUTION_FILES:
        raise ReadingPackError("Agent Skill contains too many files", EXIT_VALIDATION)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        for relative, value in sorted(files.items()):
            if not isinstance(relative, str) or not isinstance(value, bytes):
                raise ReadingPackError(
                    "Agent Skill files must use string paths and byte values",
                    EXIT_VALIDATION,
                )
            maximum = _distribution_file_limit(relative)
            if maximum == 0 or len(value) > maximum:
                raise ReadingPackError(
                    f"unsafe or oversized generated Agent Skill file: {relative}",
                    EXIT_VALIDATION,
                )
            name = f"{skill_name}/{relative}"
            if not _archive_name_is_safe(name, skill_name):
                raise ReadingPackError(
                    f"unsafe generated ZIP member path: {name}", EXIT_VALIDATION
                )
            info = zipfile.ZipInfo(name, FIXED_ZIP_TIME)
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, value)
    value = buffer.getvalue()
    if len(value) > MAX_ARCHIVE_BYTES:
        raise ReadingPackError("generated Agent Skill ZIP is too large", EXIT_VALIDATION)
    return value


def distribution_paths(project: Path, skill_name: str) -> tuple[Path, Path]:
    validate_skill_name(skill_name)
    return (
        project / "dist" / "agent-skill" / skill_name,
        project / "dist" / f"{skill_name}-agent-skill.zip",
    )


def _ensure_directory(path: Path, label: str, exit_code: int) -> None:
    try:
        current = path.lstat()
    except FileNotFoundError as exc:
        raise ReadingPackError(f"{label} is missing: {path}", exit_code) from exc
    except OSError as exc:
        raise ReadingPackError(f"cannot inspect {label} {path}: {exc}", exit_code) from exc
    if not stat.S_ISDIR(current.st_mode):
        raise ReadingPackError(f"{label} is not a regular directory: {path}", exit_code)


def _distribution_file_limit(path: str) -> int:
    if path == "SKILL.md":
        return MAX_SKILL_BYTES
    if path == "manifest.json":
        return MAX_MANIFEST_BYTES
    if path.startswith("references/"):
        return MAX_REFERENCE_BYTES
    return 0


def _directory_files(
    root: Path, expected_paths: set[str], exit_code: int
) -> dict[str, bytes]:
    _ensure_directory(root, "Agent Skill directory", exit_code)
    found: dict[str, bytes] = {}
    pending = [(root, PurePosixPath())]
    entries = 0
    while pending:
        directory, relative_directory = pending.pop()
        try:
            children = sorted(os.scandir(directory), key=lambda item: item.name)
        except OSError as exc:
            raise ReadingPackError(
                f"cannot inspect Agent Skill directory {directory}: {exc}", exit_code
            ) from exc
        for child in children:
            entries += 1
            if entries > MAX_DISTRIBUTION_FILES + 2:
                raise ReadingPackError("Agent Skill directory contains too many entries", exit_code)
            relative = relative_directory / child.name
            if child.is_symlink():
                raise ReadingPackError(
                    f"Agent Skill distribution contains a symlink: {relative}", exit_code
                )
            if child.is_dir(follow_symlinks=False):
                if relative != PurePosixPath("references"):
                    raise ReadingPackError(
                        f"Agent Skill distribution contains an extra directory: {relative}",
                        exit_code,
                    )
                pending.append((Path(child.path), relative))
                continue
            if not child.is_file(follow_symlinks=False):
                raise ReadingPackError(
                    f"Agent Skill distribution contains a non-regular file: {relative}",
                    exit_code,
                )
            key = relative.as_posix()
            if key not in expected_paths:
                raise ReadingPackError(
                    f"Agent Skill distribution contains an extra file: {key}",
                    exit_code,
                )
            found[key] = _read_regular_file(
                Path(child.path),
                _distribution_file_limit(key),
                f"Agent Skill file {key}",
                exit_code,
            )
    return found


def _validate_directory(root: Path, expected: dict[str, bytes], exit_code: int) -> None:
    found = _directory_files(root, set(expected), exit_code)
    if set(found) != set(expected):
        missing = sorted(set(expected) - set(found))
        detail = []
        if missing:
            detail.append(f"missing={missing}")
        raise ReadingPackError(
            "Agent Skill directory file set differs from expected: " + "; ".join(detail),
            exit_code,
        )
    for path, value in expected.items():
        if found[path] != value:
            raise ReadingPackError(
                f"Agent Skill directory file differs from expected: {path}", exit_code
            )


def _validate_archive(
    value: bytes, skill_name: str, expected: dict[str, bytes], exit_code: int
) -> None:
    expected_names = sorted(f"{skill_name}/{path}" for path in expected)
    expected_name_set = set(expected_names)
    try:
        with zipfile.ZipFile(io.BytesIO(value), "r") as archive:
            if archive.comment:
                raise ReadingPackError("Agent Skill ZIP has an unexpected comment", exit_code)
            infos = archive.infolist()
            if len(infos) > MAX_DISTRIBUTION_FILES:
                raise ReadingPackError("Agent Skill ZIP contains too many entries", exit_code)
            names = [info.filename for info in infos]
            if len(names) != len(set(names)):
                raise ReadingPackError("Agent Skill ZIP contains duplicate entries", exit_code)
            for info in infos:
                if not _archive_name_is_safe(info.filename, skill_name):
                    raise ReadingPackError(
                        f"Agent Skill ZIP contains an unsafe path: {info.filename}",
                        exit_code,
                    )
                if info.filename not in expected_name_set:
                    raise ReadingPackError(
                        f"Agent Skill ZIP contains an extra entry: {info.filename}",
                        exit_code,
                    )
                file_type = (info.external_attr >> 16) & 0o170000
                if info.is_dir() or file_type not in {0, stat.S_IFREG}:
                    raise ReadingPackError(
                        f"Agent Skill ZIP contains a non-regular entry: {info.filename}",
                        exit_code,
                    )
                if info.compress_type != zipfile.ZIP_STORED:
                    raise ReadingPackError(
                        f"Agent Skill ZIP member is not stored: {info.filename}", exit_code
                    )
                relative = PurePosixPath(info.filename).relative_to(skill_name).as_posix()
                if info.file_size > _distribution_file_limit(relative):
                    raise ReadingPackError(
                        f"Agent Skill ZIP member is too large: {info.filename}", exit_code
                    )
            if names != expected_names:
                raise ReadingPackError(
                    "Agent Skill ZIP member set or ordering differs from expected", exit_code
                )
            for info in infos:
                relative = PurePosixPath(info.filename).relative_to(skill_name).as_posix()
                if archive.read(info) != expected[relative]:
                    raise ReadingPackError(
                        f"Agent Skill ZIP member differs from expected: {info.filename}",
                        exit_code,
                    )
    except ReadingPackError:
        raise
    except (OSError, zipfile.BadZipFile, RuntimeError, ValueError) as exc:
        raise ReadingPackError(f"invalid Agent Skill ZIP: {exc}", exit_code) from exc


def _write_staged_directory(root: Path, files: dict[str, bytes]) -> None:
    root.mkdir(mode=0o755)
    root.chmod(0o755)
    references = root / "references"
    references.mkdir(mode=0o755)
    references.chmod(0o755)
    for relative, value in sorted(files.items()):
        path = root / PurePosixPath(relative)
        try:
            with path.open("xb") as handle:
                handle.write(value)
            path.chmod(0o644)
        except OSError as exc:
            raise ReadingPackError(
                f"cannot stage Agent Skill file {relative}: {exc}", EXIT_IO
            ) from exc


def _existing_target_kind(path: Path, expected_directory: bool) -> bool:
    """Return whether a target exists, rejecting unsafe target types."""

    try:
        current = path.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise ReadingPackError(f"cannot inspect Agent Skill output {path}: {exc}", EXIT_IO) from exc
    wanted = stat.S_ISDIR(current.st_mode) if expected_directory else stat.S_ISREG(current.st_mode)
    if not wanted:
        kind = "directory" if expected_directory else "regular file"
        raise ReadingPackError(
            f"refusing to replace Agent Skill output that is not a {kind}: {path}",
            EXIT_IO,
        )
    return True


def _cleanup_stage(path: Path) -> None:
    try:
        shutil.rmtree(path)
    except FileNotFoundError:
        pass
    except OSError:
        # A completed distribution must not be reported as failed solely because
        # best-effort cleanup of an internal staging directory was unsuccessful.
        pass


def build_agent_skill(
    project: Path,
    config: dict[str, Any],
    data_by_lang: dict[str, dict[str, Any]],
) -> tuple[Path, Path]:
    """Build and transactionally replace the directory and deterministic ZIP."""

    name = validate_skill_name(config.get("slug"))
    expected = expected_agent_skill(project, config, data_by_lang)
    archive = archive_bytes(name, expected)
    final_directory, final_archive = distribution_paths(project, name)
    dist = project / "dist"
    _ensure_directory(dist, "dist directory", EXIT_IO)
    parent = final_directory.parent
    if parent.exists():
        _ensure_directory(parent, "Agent Skill output parent", EXIT_IO)
    directory_exists = _existing_target_kind(final_directory, True)
    archive_exists = _existing_target_kind(final_archive, False)

    try:
        stage_root = Path(tempfile.mkdtemp(prefix=".agent-skill-stage-", dir=dist))
    except OSError as exc:
        raise ReadingPackError(f"cannot create Agent Skill staging directory: {exc}", EXIT_IO) from exc
    staged_directory = stage_root / name
    staged_archive = stage_root / final_archive.name
    backup_directory = stage_root / ".previous-directory"
    backup_archive = stage_root / ".previous-archive"
    parent_created = False
    installed_directory = False
    installed_archive = False
    backed_up_directory = False
    backed_up_archive = False
    preserve_stage = False
    try:
        _write_staged_directory(staged_directory, expected)
        with staged_archive.open("xb") as handle:
            handle.write(archive)
        staged_archive.chmod(0o644)
        _validate_directory(staged_directory, expected, EXIT_IO)
        staged_zip = _read_regular_file(
            staged_archive, MAX_ARCHIVE_BYTES, "staged Agent Skill ZIP", EXIT_IO
        )
        _validate_archive(staged_zip, name, expected, EXIT_IO)
        if staged_zip != archive:
            raise ReadingPackError("staged Agent Skill ZIP changed unexpectedly", EXIT_IO)

        if not parent.exists():
            parent.mkdir(mode=0o755)
            parent_created = True
        if directory_exists:
            os.replace(final_directory, backup_directory)
            backed_up_directory = True
        if archive_exists:
            os.replace(final_archive, backup_archive)
            backed_up_archive = True
        os.replace(staged_directory, final_directory)
        installed_directory = True
        os.replace(staged_archive, final_archive)
        installed_archive = True
    except ReadingPackError as exc:
        rollback_complete = _rollback_outputs(
            final_directory,
            final_archive,
            staged_directory,
            staged_archive,
            backup_directory,
            backup_archive,
            installed_directory,
            installed_archive,
            backed_up_directory,
            backed_up_archive,
            parent,
            parent_created,
        )
        if not rollback_complete:
            preserve_stage = True
            raise ReadingPackError(
                f"{exc}; automatic rollback was incomplete; recovery files remain at "
                f"{stage_root}",
                EXIT_IO,
            ) from exc
        raise
    except OSError as exc:
        rollback_complete = _rollback_outputs(
            final_directory,
            final_archive,
            staged_directory,
            staged_archive,
            backup_directory,
            backup_archive,
            installed_directory,
            installed_archive,
            backed_up_directory,
            backed_up_archive,
            parent,
            parent_created,
        )
        if not rollback_complete:
            preserve_stage = True
            raise ReadingPackError(
                "cannot install Agent Skill distribution and automatic rollback was "
                f"incomplete; recovery files remain at {stage_root}: {exc}",
                EXIT_IO,
            ) from exc
        raise ReadingPackError(f"cannot install Agent Skill distribution: {exc}", EXIT_IO) from exc
    finally:
        if not preserve_stage:
            _cleanup_stage(stage_root)
    return final_directory, final_archive


def _rollback_outputs(
    final_directory: Path,
    final_archive: Path,
    staged_directory: Path,
    staged_archive: Path,
    backup_directory: Path,
    backup_archive: Path,
    installed_directory: bool,
    installed_archive: bool,
    backed_up_directory: bool,
    backed_up_archive: bool,
    parent: Path,
    parent_created: bool,
) -> bool:
    """Best-effort rollback; preserve the original exception at the call site."""

    try:
        if installed_archive and final_archive.exists() and not staged_archive.exists():
            os.replace(final_archive, staged_archive)
        if installed_directory and final_directory.exists() and not staged_directory.exists():
            os.replace(final_directory, staged_directory)
        if backed_up_archive and backup_archive.exists():
            os.replace(backup_archive, final_archive)
        if backed_up_directory and backup_directory.exists():
            os.replace(backup_directory, final_directory)
        if parent_created:
            try:
                parent.rmdir()
            except OSError:
                pass
    except OSError:
        return False
    return True


def check_agent_skill(
    project: Path,
    config: dict[str, Any],
    data_by_lang: dict[str, dict[str, Any]],
) -> tuple[Path, Path]:
    """Read-only comparison against a freshly rendered expected distribution."""

    name = validate_skill_name(config.get("slug"))
    expected = expected_agent_skill(project, config, data_by_lang)
    expected_archive = archive_bytes(name, expected)
    directory, archive_path = distribution_paths(project, name)
    _validate_directory(directory, expected, EXIT_CHECK)
    current_archive = _read_regular_file(
        archive_path, MAX_ARCHIVE_BYTES, "Agent Skill ZIP", EXIT_CHECK
    )
    _validate_archive(current_archive, name, expected, EXIT_CHECK)
    if current_archive != expected_archive:
        raise ReadingPackError(
            "Agent Skill ZIP differs from the deterministic expected archive",
            EXIT_CHECK,
        )
    return directory, archive_path
