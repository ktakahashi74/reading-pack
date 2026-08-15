"""Core command definitions with no producer dependency."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import platform
import shutil
import sys
from pathlib import Path

from reading_pack.errors import EXIT_CHECK, EXIT_IO, EXIT_OK, EXIT_VALIDATION, ReadingPackError
from reading_pack.hashing import semantic_hash
from reading_pack.importers import import_manuscript
from reading_pack.profiles import AUTHORITY_TYPES, POLICY_RUBRICS, PROFILES, content_metrics
from reading_pack.project import (
    create_project,
    find_project,
    load_config,
    load_language_data,
    project_lock,
    selected_languages,
    write_json,
)
from reading_pack.rendering import build_packs, output_path, render_pack
from reading_pack.source_registry import (
    SOURCE_FORMATS,
    SOURCE_LANGUAGES,
    SOURCE_ROLES,
    apply_source_plan,
    create_source_plan,
    list_sources,
    load_source_plan,
    write_source_plan,
)
from reading_pack.staging import (
    apply_import_plan,
    apply_manual_outline,
    create_import_plan,
    load_manual_outline,
    load_plan,
    write_plan,
)
from reading_pack.validation import COLLECTIONS, Issue, errors, validate_project


def register(commands: argparse._SubParsersAction) -> None:
    init = commands.add_parser("init", help="create a new Reading Pack project")
    init.add_argument("directory", type=Path)
    init.add_argument("--title", required=True)
    init.add_argument("--author", required=True)
    init.add_argument("--lang", action="append", required=True, help="ja, en, or repeat for both")
    init.add_argument("--primary-language", choices=("ja", "en"))
    init.add_argument("--slug")
    init.add_argument(
        "--profile",
        choices=tuple(PROFILES),
        default="general-navigation",
        help="genre/use contract; quality is evaluated by critical gates, not a score",
    )
    init.add_argument("--scope", default="complete published edition")
    init.add_argument(
        "--authority-type",
        choices=tuple(sorted(AUTHORITY_TYPES)),
        default="author",
    )

    profiles = commands.add_parser("profiles", help="list built-in quality profiles")
    profiles.add_argument("--json", action="store_true")

    measure = commands.add_parser(
        "measure",
        help="report reproducible canonical content counts for a no-regression floor",
    )
    measure.add_argument("--project", type=Path, default=Path.cwd())
    measure.add_argument("--json", action="store_true")

    imp = commands.add_parser(
        "import",
        help="compatibility shortcut: extract structure directly into an empty project",
    )
    imp.add_argument("manuscript", type=Path)
    imp.add_argument("--project", type=Path, default=Path.cwd())
    imp.add_argument("--lang", choices=("ja", "en"), required=True)
    imp.add_argument(
        "--format",
        choices=("markdown", "org", "epub3", "pdf", "pdf-vertical", "text"),
    )
    imp.add_argument("--force", action="store_true", help="replace existing extracted chapter structure")

    plan = commands.add_parser(
        "import-plan",
        help="extract a body-free, reviewable structure plan without changing canonical data",
    )
    plan.add_argument("manuscript", type=Path)
    plan.add_argument("--output", type=Path, required=True)
    plan.add_argument(
        "--force-output",
        action="store_true",
        help="replace an existing non-source plan file after verifying its exact path",
    )
    plan.add_argument(
        "--format",
        choices=("markdown", "org", "epub3", "pdf", "pdf-vertical", "text"),
    )
    plan.add_argument(
        "--outline-sidecar",
        type=Path,
        help="body-free, source-hash-bound human outline for scans or complex layouts",
    )

    apply_plan = commands.add_parser(
        "import-apply",
        help="apply an explicitly reviewed import plan as draft structure",
    )
    apply_plan.add_argument("plan", type=Path)
    apply_plan.add_argument("--source", type=Path, required=True)
    apply_plan.add_argument("--project", type=Path, default=Path.cwd())
    apply_plan.add_argument("--lang", choices=("ja", "en"), required=True)

    validate = commands.add_parser("validate", help="validate schemas, IDs, references, and translations")
    validate.add_argument("--project", type=Path, default=Path.cwd())
    validate.add_argument("--release", action="store_true", help="also enforce human publication gates")
    validate.add_argument("--json", action="store_true", help="emit machine-readable findings")

    build = commands.add_parser("build", help="deterministically generate one or both packs")
    build.add_argument("--project", type=Path, default=Path.cwd())
    build.add_argument("--lang", action="append", choices=("ja", "en", "all"))

    check = commands.add_parser("check", help="validate and compare generated output byte-for-byte")
    check.add_argument("--project", type=Path, default=Path.cwd())
    check.add_argument("--lang", action="append", choices=("ja", "en", "all"))
    check.add_argument("--release", action="store_true", help="enforce rights and human-review gates")


    doctor = commands.add_parser("doctor", help="diagnose the local offline environment")
    doctor.add_argument("--project", type=Path, default=Path.cwd())

    link = commands.add_parser(
        "link-translations",
        help="record current primary hashes after a human updates translations",
    )
    link.add_argument("--project", type=Path, default=Path.cwd())
    link.add_argument("--lang", choices=("ja", "en"), required=True)


    sources = commands.add_parser(
        "sources", help="register body-free identities for primary and support sources"
    )
    source_commands = sources.add_subparsers(dest="source_command", required=True)
    source_plan = source_commands.add_parser("plan", help="create a body-free source registration plan")
    source_plan.add_argument("source", type=Path)
    source_plan.add_argument("--id", dest="source_id", required=True)
    source_plan.add_argument("--role", choices=tuple(sorted(SOURCE_ROLES)), required=True)
    source_plan.add_argument("--lang", choices=tuple(sorted(SOURCE_LANGUAGES)), default="und")
    source_plan.add_argument("--format", choices=tuple(sorted(SOURCE_FORMATS)))
    source_plan.add_argument("--output", type=Path, required=True)
    source_apply = source_commands.add_parser("apply", help="register an explicitly reviewed source plan")
    source_apply.add_argument("plan", type=Path)
    source_apply.add_argument("--source", type=Path, required=True)
    source_apply.add_argument("--project", type=Path, default=Path.cwd())
    source_list = source_commands.add_parser("list", help="list source identities without source paths or prose")
    source_list.add_argument("--project", type=Path, default=Path.cwd())
    source_list.add_argument("--json", action="store_true")


    init.set_defaults(_handler=command_init)
    profiles.set_defaults(_handler=command_profiles)
    measure.set_defaults(_handler=command_measure)
    imp.set_defaults(_handler=command_import)
    plan.set_defaults(_handler=command_import_plan)
    apply_plan.set_defaults(_handler=command_import_apply)
    validate.set_defaults(_handler=command_validate)
    build.set_defaults(_handler=command_build)
    check.set_defaults(_handler=command_check)
    doctor.set_defaults(_handler=command_doctor)
    link.set_defaults(_handler=command_link_translations)
    sources.set_defaults(_handler=command_sources)


def _languages(values: list[str] | None) -> list[str]:
    if not values:
        return []
    result = []
    for value in values:
        result.extend(item.strip() for item in value.split(",") if item.strip())
    return list(dict.fromkeys(result))


def _print_issues(issues: list[Issue], machine: bool = False) -> None:
    if machine:
        print(json.dumps([issue.__dict__ for issue in issues], ensure_ascii=False, indent=2))
        return
    for issue in issues:
        print(issue.format(), file=sys.stderr if issue.severity == "error" else sys.stdout)


def _validated(project: Path, *, release: bool = False) -> tuple[dict, dict[str, dict]]:
    config, data_by_lang, issues = validate_project(project, release=release)
    if issues:
        _print_issues(issues)
    fatal = errors(issues)
    if fatal:
        raise ReadingPackError(f"validation failed with {len(fatal)} error(s)", EXIT_VALIDATION)
    return config, data_by_lang


def command_init(args: argparse.Namespace) -> int:
    languages = _languages(args.lang)
    if not languages:
        raise ReadingPackError("at least one non-empty --lang value is required", 2)
    primary = args.primary_language or languages[0]
    create_project(
        args.directory,
        title=args.title,
        author=args.author,
        languages=languages,
        primary_language=primary,
        slug=args.slug,
        profile=args.profile,
        scope=args.scope,
        authority_type=args.authority_type,
    )
    print(f"initialized {args.directory.resolve()}")
    print(
        f"quality profile: {args.profile}; human authority and critical policies remain pending"
    )
    print("next: run reading-pack import-plan, review the plan, then run reading-pack import-apply")
    return EXIT_OK


def command_profiles(args: argparse.Namespace) -> int:
    records = [
        {
            "name": profile.name,
            "required_modules": sorted(profile.required_modules),
            "required_chapter_fields": sorted(profile.required_chapter_fields),
            "critical_policies": sorted(profile.critical_policies),
            "default_spoiler_policy": profile.default_spoiler_policy,
            "minimum_level": profile.minimum_level,
            "policy_rubrics": {
                policy: POLICY_RUBRICS[policy]
                for policy in sorted(profile.critical_policies)
            },
        }
        for profile in PROFILES.values()
    ]
    if args.json:
        print(json.dumps(records, ensure_ascii=False, indent=2))
    else:
        for record in records:
            print(
                f"{record['name']}: modules={','.join(record['required_modules'])}; "
                f"chapter-fields={','.join(record['required_chapter_fields'])}; "
                f"level>={record['minimum_level']}; spoiler={record['default_spoiler_policy']}; "
                f"policies={','.join(record['critical_policies'])}"
            )
    return EXIT_OK


def command_measure(args: argparse.Namespace) -> int:
    project = find_project(args.project)
    _, data_by_lang, issues = validate_project(project)
    for issue in issues:
        print(issue.format(), file=sys.stderr)
    fatal = errors(issues)
    if fatal:
        raise ReadingPackError(
            f"validation failed with {len(fatal)} error(s)", EXIT_VALIDATION
        )
    metrics = content_metrics(data_by_lang)
    if args.json:
        print(json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        for language, values in metrics.items():
            print(
                f"{language}: "
                + " ".join(f"{key}={value}" for key, value in sorted(values.items()))
            )
    return EXIT_OK


def command_import(args: argparse.Namespace) -> int:
    project = find_project(args.project)
    extracted = import_manuscript(
        project,
        args.manuscript.resolve(),
        lang=args.lang,
        explicit_format=args.format,
        force=args.force,
    )
    print(
        f"imported {len(extracted.chapters)} chapter(s) from {args.manuscript.name} "
        f"as {extracted.source_format}; manuscript prose was not copied"
    )
    return EXIT_OK


def command_import_plan(args: argparse.Namespace) -> int:
    manuscript = args.manuscript.resolve()
    output = args.output.resolve()
    sidecar = args.outline_sidecar.resolve() if args.outline_sidecar else None
    if output == manuscript or (sidecar is not None and output == sidecar):
        raise ReadingPackError("import plan output must not overwrite its source or outline sidecar", EXIT_IO)
    if output.exists() and not args.force_output:
        raise ReadingPackError(
            f"refusing to overwrite existing import plan: {output}; pass --force-output after checking the path",
            EXIT_IO,
        )
    if output.name in {"reading-pack.toml", "quality-plan.json"} or (
        output.parent.name in {"data", "templates", "dist"}
    ):
        raise ReadingPackError("refusing to write an import plan over a canonical or generated project path", EXIT_IO)
    plan = create_import_plan(manuscript, args.format)
    if args.outline_sidecar:
        plan = apply_manual_outline(plan, load_manual_outline(sidecar))
    write_plan(output, plan)
    counts: dict[str, int] = {}
    for unit in plan["units"]:
        counts[unit["kind"]] = counts.get(unit["kind"], 0) + 1
    count_text = ", ".join(f"{key}={value}" for key, value in sorted(counts.items()))
    print(
        f"wrote body-free import plan {output} "
        f"(outcome={plan['outcome']}; {count_text})"
    )
    if plan["outcome"] == "blocked":
        return EXIT_VALIDATION
    return EXIT_OK


def command_import_apply(args: argparse.Namespace) -> int:
    project = find_project(args.project)
    plan = load_plan(args.plan.resolve())
    updated = apply_import_plan(
        project,
        plan,
        args.lang,
        args.source.resolve(),
    )
    print(
        f"applied reviewed plan {plan['plan_id']} as {len(updated['chapters'])} draft chapter(s); "
        "metadata candidates were not applied"
    )
    return EXIT_OK


def command_validate(args: argparse.Namespace) -> int:
    project = find_project(args.project)
    _, _, issues = validate_project(project, release=args.release)
    _print_issues(issues, args.json)
    fatal = errors(issues)
    if fatal:
        if not args.json:
            print(f"validation failed: {len(fatal)} error(s)", file=sys.stderr)
        return EXIT_VALIDATION
    if not args.json:
        print("validation passed")
    return EXIT_OK


def command_build(args: argparse.Namespace) -> int:
    project = find_project(args.project)
    config, data_by_lang = _validated(project)
    languages = selected_languages(config, _languages(args.lang))
    outputs = build_packs(project, languages, config, data_by_lang)
    for output in outputs:
        print(f"built {output.relative_to(project)}")
    return EXIT_OK


def command_check(args: argparse.Namespace) -> int:
    project = find_project(args.project)
    config, data_by_lang = _validated(project, release=args.release)
    languages = selected_languages(config, _languages(args.lang))
    failures = 0
    for lang in languages:
        expected = render_pack(project, lang, config, data_by_lang[lang]).encode("utf-8")
        path = output_path(project, config, lang)
        try:
            current = path.read_bytes()
        except OSError:
            print(f"ERROR RP400 {path.relative_to(project)}: generated output is missing", file=sys.stderr)
            failures += 1
            continue
        if current != expected:
            print(
                f"ERROR RP401 {path.relative_to(project)}: generated output differs from canonical data/templates; run reading-pack build",
                file=sys.stderr,
            )
            failures += 1
        else:
            print(f"OK byte-identical: {path.relative_to(project)}")
    if failures:
        return EXIT_CHECK
    mode = "release" if args.release else "technical"
    print(f"{mode} check passed")
    return EXIT_OK


def command_doctor(args: argparse.Namespace) -> int:
    ok = True
    version = sys.version_info
    if version < (3, 11):
        print(f"FAIL Python {platform.python_version()} (3.11 or newer required)")
        ok = False
    else:
        print(f"OK Python {platform.python_version()}")
    try:
        jsonschema_version = importlib.metadata.version("jsonschema")
    except importlib.metadata.PackageNotFoundError:
        print("FAIL runtime dependency: jsonschema is not installed")
        ok = False
    else:
        print(f"OK runtime dependency: jsonschema {jsonschema_version}")
    print("OK network/API keys: not required")
    pdf_tools = [name for name in ("pdfinfo", "pdftotext") if shutil.which(name) is not None]
    if len(pdf_tools) == 2:
        print("OK optional PDF import: Poppler pdfinfo and pdftotext available")
    else:
        missing = ", ".join(sorted(set(("pdfinfo", "pdftotext")) - set(pdf_tools)))
        print(f"INFO optional PDF import unavailable: missing {missing}")
    try:
        project = find_project(args.project)
        config = load_config(project)
        print(f"OK project: {project}")
        for lang in config.get("languages", []):
            load_language_data(project, lang)
            print(f"OK canonical data: data/pack.{lang}.json")
            template = project / "templates" / f"pack.{lang}.md"
            template.read_text(encoding="utf-8")
            print(f"OK template: templates/pack.{lang}.md")
    except (ReadingPackError, OSError, UnicodeError) as exc:
        print(f"FAIL project: {exc}")
        ok = False
    return EXIT_OK if ok else EXIT_IO


def command_link_translations(args: argparse.Namespace) -> int:
    project = find_project(args.project)
    with project_lock(project):
        config = load_config(project)
        primary_lang = config["primary_language"]
        if args.lang == primary_lang:
            raise ReadingPackError("--lang must select a non-primary language")
        if args.lang not in config["languages"]:
            raise ReadingPackError(f"language is not configured: {args.lang}")
        primary = load_language_data(project, primary_lang)
        translated = load_language_data(project, args.lang)
        changed = 0
        for collection in COLLECTIONS:
            primary_by_id = {
                record["id"]: record for record in primary.get(collection, [])
            }
            for record in translated.get(collection, []):
                source = primary_by_id.get(record["id"])
                if not source:
                    continue
                new_hash = semantic_hash(source)
                if record.get("source_hash") != new_hash or record.get("source_id") != source["id"]:
                    record["source_id"] = source["id"]
                    record["source_hash"] = new_hash
                    record["translation_status"] = "draft"
                    record["status"] = "draft"
                    changed += 1
        write_json(project / "data" / f"pack.{args.lang}.json", translated)
    print(f"linked {changed} translated record(s); changed records are draft and require human approval")
    return EXIT_OK


def command_sources(args: argparse.Namespace) -> int:
    if args.source_command == "plan":
        plan = create_source_plan(
            args.source,
            source_id=args.source_id,
            role=args.role,
            language=args.lang,
            explicit_format=args.format,
        )
        write_source_plan(args.output, plan)
        print(f"created body-free source plan {args.output.resolve()} for {args.source_id}")
        return EXIT_OK
    if args.source_command == "apply":
        project = find_project(args.project)
        source = apply_source_plan(project, load_source_plan(args.plan), args.source)
        print(f"registered {source['id']} role={source['role']} hash={source['sha256']}")
        return EXIT_OK
    if args.source_command == "list":
        project = find_project(args.project)
        records = list_sources(project)
        if args.json:
            print(json.dumps(records, ensure_ascii=False, indent=2))
        else:
            for source in records:
                print(
                    f"{source['id']} role={source['role']} lang={source['language']} "
                    f"format={source['format']} name={source['name']} sha256={source['sha256']}"
                )
        return EXIT_OK
    raise ReadingPackError(f"unknown sources command: {args.source_command}", 2)
