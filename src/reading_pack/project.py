"""Project creation, loading, and atomic canonical-source writes."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import tomllib
from contextlib import contextmanager
from datetime import date
from importlib import resources
from pathlib import Path
from typing import Any

from .errors import EXIT_IO, ReadingPackError

CONFIG_NAME = "reading-pack.toml"
SUPPORTED_LANGUAGES = {"ja", "en"}
REVIEW_STATES = {"pending", "draft", "reviewed", "approved", "not_required"}


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "my-reading-pack"


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def default_config_text(
    *,
    title: str,
    author: str,
    languages: list[str],
    primary_language: str,
    slug: str,
    level: int = 1,
) -> str:
    lang_text = ", ".join(_toml_string(lang) for lang in languages)
    today = date.today().isoformat()
    return f'''# Canonical project configuration. Generated packs are written to dist/.
format_version = 1
slug = {_toml_string(slug)}
version = "0.6.0-draft"
pack_date = "{today}"
status = "draft"
primary_language = {_toml_string(primary_language)}
languages = [{lang_text}]
level = {level}
output_basename = "reading-pack"

[book]
title = {_toml_string(title)}
author = {_toml_string(author)}
publisher = ""
publication_date = ""
isbn = ""
official_url = ""
contents_note = ""
copyright_holder = {_toml_string(author)}
copyright_year = {date.today().year}
pack_license = "rights-holder decision pending"

[workflow]
design_constraints = "pending"
rights_review = "pending"
author_review = "pending"
publisher_review = "pending"
reconstruction_review = "pending"
publication_decision = "pending"

[limits]
max_summary_characters = 500
max_pack_characters = 100000
'''


def empty_language_data(lang: str, title: str, author: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "language": lang,
        "source": {"format": "none", "name": "", "sha256": ""},
        "book": {"title": title, "author": author},
        "chapters": [],
        "certainty": [],
        "claims": [],
        "misreadings": [],
        "policies": [],
        "names": [],
        "glossary": [],
        "references": [],
    }


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(temporary, path)
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise


def write_json(path: Path, value: Any) -> None:
    atomic_write_text(
        path,
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
    )


@contextmanager
def project_lock(project: Path):
    """Serialize cooperating canonical mutations within one local project."""

    lock_directory = project.resolve() / ".reading-pack"
    lock_directory.mkdir(parents=True, exist_ok=True)
    lock_path = lock_directory / "project.lock"
    try:
        import fcntl
    except ImportError as exc:  # pragma: no cover - current supported runtime is POSIX
        raise ReadingPackError("project mutation locking requires POSIX fcntl", EXIT_IO) from exc
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    except OSError as exc:
        raise ReadingPackError(f"cannot open project lock {lock_path}: {exc}", EXIT_IO) from exc
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def create_project(
    path: Path,
    *,
    title: str,
    author: str,
    languages: list[str],
    primary_language: str,
    slug: str | None = None,
    profile: str = "general-navigation",
    scope: str = "complete published edition",
    authority_type: str = "author",
) -> None:
    path = path.resolve()
    if path.exists():
        if not path.is_dir():
            raise ReadingPackError(f"refusing to initialize over a file: {path}", EXIT_IO)
        if any(path.iterdir()):
            raise ReadingPackError(
                f"refusing to initialize non-empty directory: {path}", EXIT_IO
            )
    path.mkdir(parents=True, exist_ok=True)
    if primary_language not in languages:
        raise ReadingPackError("primary language must be included in languages")
    unknown = set(languages) - SUPPORTED_LANGUAGES
    if unknown:
        raise ReadingPackError(f"unsupported language(s): {', '.join(sorted(unknown))}")

    # Imported lazily to keep the low-level project helpers independent from
    # profile validation while still opting every newly created project into a
    # concrete, gate-based quality contract.
    from .profiles import PROFILES, create_default_quality_plan

    try:
        quality_plan = create_default_quality_plan(profile, scope, authority_type)
    except ValueError as exc:
        raise ReadingPackError(str(exc)) from exc

    project_slug = slug or slugify(title)
    level = PROFILES[profile].minimum_level
    atomic_write_text(
        path / CONFIG_NAME,
        default_config_text(
            title=title,
            author=author,
            languages=languages,
            primary_language=primary_language,
            slug=project_slug,
            level=level,
        ),
    )
    write_json(path / "quality-plan.json", quality_plan)
    # Multi-source provenance is additive: legacy projects without this file
    # load as an empty registry, while new projects make the boundary visible.
    write_json(path / "sources.json", {"schema_version": 1, "sources": []})
    # This ledger records whether each module is authority-provided, augmented,
    # generated, or intentionally omitted. It contains no supplied prose.
    from reading_pack_review.author_input import default_author_input_state

    write_json(
        path / "author-input-state.json",
        default_author_input_state({"languages": languages}),
    )
    for lang in languages:
        write_json(path / "data" / f"pack.{lang}.json", empty_language_data(lang, title, author))
        default = resources.files("reading_pack").joinpath("defaults", f"pack.{lang}.md")
        atomic_write_text(path / "templates" / f"pack.{lang}.md", default.read_text(encoding="utf-8"))
    for directory in ("manuscripts", "dist", "evaluation"):
        (path / directory).mkdir(exist_ok=True)
    atomic_write_text(
        path / ".gitignore",
        "# Private transient candidate runs and locally extracted evidence.\n"
        ".reading-pack/\n",
    )
    atomic_write_text(
        path / "evaluation" / "README.md",
        "# Evaluation records\n\n"
        "Store model, date, settings, predeclared rubric, results, and the human "
        "reconstruction judgment here. Do not commit confidential manuscripts or "
        "vendor credentials.\n",
    )


def find_project(path: Path) -> Path:
    candidate = path.resolve()
    if candidate.is_file():
        candidate = candidate.parent
    for current in (candidate, *candidate.parents):
        if (current / CONFIG_NAME).is_file():
            return current
    raise ReadingPackError(f"{CONFIG_NAME} not found from {path}", EXIT_IO)


def load_config(project: Path) -> dict[str, Any]:
    config_path = project / CONFIG_NAME
    try:
        with config_path.open("rb") as handle:
            return tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ReadingPackError(f"cannot read {config_path}: {exc}", EXIT_IO) from exc


def load_language_data(project: Path, lang: str) -> dict[str, Any]:
    path = project / "data" / f"pack.{lang}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReadingPackError(f"cannot read {path}: {exc}", EXIT_IO) from exc


def selected_languages(config: dict[str, Any], requested: list[str] | None) -> list[str]:
    configured = list(config.get("languages", []))
    if not requested or "all" in requested:
        return configured
    unknown = set(requested) - set(configured)
    if unknown:
        raise ReadingPackError(
            f"language(s) not configured: {', '.join(sorted(unknown))}"
        )
    return [lang for lang in configured if lang in requested]


def copy_project(source: Path, target: Path) -> None:
    """Test helper: copy a sample project while excluding generated caches."""
    shutil.copytree(
        source,
        target,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"),
    )
