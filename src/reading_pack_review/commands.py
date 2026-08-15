"""Single-Markdown author-review commands."""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from .assisted_review import (
    apply_assisted_author_review_plan,
    assisted_author_review_status,
    create_assisted_author_review_plan,
    export_assisted_author_review,
)
from .author_review import REVIEW_MODULES, load_author_review_plan, write_author_review_plan
from .author_input import (
    AUTHORITY_TYPES as AUTHOR_INPUT_AUTHORITY_TYPES,
    COLLECTION_MODULES as AUTHOR_INPUT_COLLECTION_MODULES,
    FIELD_MODULES as AUTHOR_INPUT_FIELD_MODULES,
    MODULES as AUTHOR_INPUT_MODULES,
    apply_author_input_plan,
    create_author_input_plan,
    create_author_input_template,
    load_author_input_plan,
    load_author_input_state,
    write_author_input_plan,
)
from reading_pack.errors import EXIT_OK, ReadingPackError
from reading_pack.project import find_project, load_config, load_language_data


def register(
    commands: argparse._SubParsersAction,
    review: argparse.ArgumentParser,
    review_commands: argparse._SubParsersAction,
) -> None:
    review_export = review_commands.add_parser(
        "export",
        help="create one human-editable Markdown author review",
    )
    review_export.add_argument("--project", type=Path, default=Path.cwd())
    review_export.add_argument(
        "--output",
        type=Path,
        required=True,
        metavar="NAME",
        help="direct child name under PROJECT/.reading-pack/reviews",
    )
    review_export.add_argument(
        "--created-at", metavar="YYYY-MM-DD", help=argparse.SUPPRESS
    )
    review_export.add_argument(
        "--module",
        action="append",
        choices=REVIEW_MODULES,
        dest="modules",
        help=(
            "limit the form to one or more record modules; for example, "
            "--module policy creates a short policy-only approval form"
        ),
    )
    review_export.add_argument(
        "--record",
        action="append",
        dest="record_ids",
        metavar="RECORD_ID",
        help=(
            "limit the form to one or more record IDs; every configured "
            "language for each ID is included"
        ),
    )
    review_export.add_argument(
        "--candidate-run",
        action="append",
        type=Path,
        dest="candidate_runs",
        metavar="RUN",
        help=(
            "prefill an exact revise-and-approve suggestion from a current "
            "QA-passed candidate run; repeat for paired languages"
        ),
    )
    review_status = review_commands.add_parser(
        "status",
        help="validate an edited human review and report expanded progress",
    )
    review_status.add_argument("review", type=Path)
    review_status.add_argument("--evidence", type=Path, required=True)
    review_status.add_argument("--project", type=Path, default=Path.cwd())
    review_status.add_argument("--json", action="store_true")
    review_plan = review_commands.add_parser(
        "plan",
        help="create a body-free plan from an edited human review",
    )
    review_plan.add_argument("review", type=Path)
    review_plan.add_argument("--evidence", type=Path, required=True)
    review_plan.add_argument("--project", type=Path, default=Path.cwd())
    review_plan.add_argument("--output", type=Path, required=True)
    review_apply = review_commands.add_parser(
        "apply",
        help="apply an unchanged human review file and body-free plan",
    )
    review_apply.add_argument("plan", type=Path)
    review_apply.add_argument("--review", type=Path, required=True)
    review_apply.add_argument("--evidence", type=Path, required=True)
    review_apply.add_argument("--project", type=Path, default=Path.cwd())

    author_input = commands.add_parser(
        "author-input",
        help="declare, stage, apply, and report authority-provided module data",
    )
    author_input_commands = author_input.add_subparsers(
        dest="author_input_command", required=True
    )
    author_input_template = author_input_commands.add_parser(
        "template", help="create a complete Author Input Package template"
    )
    author_input_template.add_argument("directory", type=Path)
    author_input_template.add_argument("--package-id", required=True)
    author_input_template.add_argument("--lang", choices=("ja", "en"), required=True)
    author_input_template.add_argument(
        "--authority-type",
        choices=tuple(sorted(AUTHOR_INPUT_AUTHORITY_TYPES)),
        default="author",
    )
    author_input_template.add_argument("--authority-name", required=True)
    author_input_template.add_argument(
        "--supplied-at", default=date.today().isoformat(), metavar="YYYY-MM-DD"
    )
    author_input_plan = author_input_commands.add_parser(
        "plan", help="validate a package and write a body-free change plan"
    )
    author_input_plan.add_argument("package", type=Path, nargs="+")
    author_input_plan.add_argument("--project", type=Path, default=Path.cwd())
    author_input_plan.add_argument("--output", type=Path, required=True)
    author_input_apply = author_input_commands.add_parser(
        "apply", help="apply an unchanged reviewed plan and record provenance"
    )
    author_input_apply.add_argument("plan", type=Path)
    author_input_apply.add_argument(
        "--package", type=Path, action="append", required=True
    )
    author_input_apply.add_argument("--project", type=Path, default=Path.cwd())
    author_input_report = author_input_commands.add_parser(
        "report", help="show current module modes and supplied-source identities"
    )
    author_input_report.add_argument("--project", type=Path, default=Path.cwd())
    author_input_report.add_argument(
        "--lang", choices=("ja", "en", "all"), default="all"
    )
    author_input_report.add_argument("--json", action="store_true")

    review.set_defaults(_handler=command_review)
    author_input.set_defaults(_handler=command_author_input)


def command_review(args: argparse.Namespace) -> int:
    if args.review_command == "export":
        project = find_project(args.project)
        record_ids = tuple(args.record_ids) if args.record_ids else None
        suggestions = []
        if args.candidate_runs:
            from reading_pack_producer.candidates import author_review_suggestions

            suggestions = author_review_suggestions(
                project,
                args.candidate_runs,
                record_ids=record_ids,
            )
            if record_ids is None:
                record_ids = tuple(sorted({
                    item["record_id"] for item in suggestions
                }))
        review_file, evidence = export_assisted_author_review(
            project,
            args.output,
            created_at=args.created_at,
            modules=(tuple(args.modules) if args.modules else None),
            record_ids=record_ids,
            suggestions=suggestions,
        )
        print(
            f"created human-editable author review {review_file} "
            f"with private evidence {evidence}; the edited Markdown is the "
            "decision evidence; no content was approved or changed"
        )
        return EXIT_OK
    if args.review_command == "status":
        project = find_project(args.project)
        status = assisted_author_review_status(
            project, args.evidence, args.review
        )
        if args.json:
            print(json.dumps(status, ensure_ascii=False, indent=2))
        else:
            summary = status["summary"]
            print(
                f"review={status['review_id']} meaningful={status['meaningful_decisions']} "
                f"reviewed={summary['reviewed']}/{summary['total']} "
                f"approve={summary['approve']} revise={summary['revise']} "
                f"exclude={summary['exclude']} hold={summary['hold']} "
                f"pending={summary['pending']} "
                f"submitted={str(status['submitted']).lower()} "
                f"final_signoff={str(status['final_signoff']).lower()}"
            )
        return EXIT_OK
    if args.review_command == "plan":
        project = find_project(args.project)
        plan = create_assisted_author_review_plan(
            project, args.evidence, args.review
        )
        write_author_review_plan(args.output, plan)
        summary = plan["summary"]
        print(
            f"created body-free author review plan {args.output.resolve()} "
            f"plan={plan['plan_id']} approve={summary['approve']} "
            f"revise={summary['revise']} exclude={summary['exclude']} "
            f"final_signoff={str(plan['final_signoff']).lower()}"
        )
        return EXIT_OK
    if args.review_command == "apply":
        project = find_project(args.project)
        result = apply_assisted_author_review_plan(
            project,
            load_author_review_plan(args.plan),
            args.evidence,
            args.review,
        )
        summary = result["summary"]
        print(
            f"applied author review {result['review_id']} "
            f"plan={result['plan_id']} approve={summary['approve']} "
            f"revise={summary['revise']} exclude={summary['exclude']} "
            f"final_signoff={str(result['final_signoff']).lower()}; "
            "revise stays draft; signed revise_approve is counted as approve"
        )
        return EXIT_OK
    raise ReadingPackError(f"unknown review command: {args.review_command}", 2)


def command_author_input(args: argparse.Namespace) -> int:
    if args.author_input_command == "template":
        manifest = create_author_input_template(
            args.directory,
            language=args.lang,
            authority_type=args.authority_type,
            authority_name=args.authority_name,
            supplied_at=args.supplied_at,
            package_id=args.package_id,
        )
        print(f"created Author Input Package template {manifest.parent}")
        return EXIT_OK
    if args.author_input_command == "plan":
        project = find_project(args.project)
        plan = create_author_input_plan(project, args.package)
        write_author_input_plan(args.output, plan)
        changes = sum(
            len(summary["added_ids"])
            + len(summary["replaced_ids"])
            + len(summary["removed_ids"])
            for language in plan["languages"].values()
            for summary in language["modules"].values()
        )
        modes = ";".join(
            f"{language}:" + ",".join(
                f"{module}={details['modules'][module]['mode']}"
                for module in AUTHOR_INPUT_MODULES
            )
            for language, details in plan["languages"].items()
        )
        print(
            f"created body-free author input plan {args.output.resolve()} "
            f"plan={plan['plan_id']} changes={changes} modes={modes}"
        )
        return EXIT_OK
    if args.author_input_command == "apply":
        project = find_project(args.project)
        result = apply_author_input_plan(
            project,
            load_author_input_plan(args.plan),
            args.package,
        )
        changed = sum(
            len(summary["added_ids"])
            + len(summary["replaced_ids"])
            + len(summary["removed_ids"])
            for language in result["language_results"].values()
            for summary in language["modules"].values()
        )
        print(
            f"applied Author Input Package(s) {','.join(result['packages'])} "
            f"languages={','.join(result['languages'])} changes={changed}; "
            "supplied records are draft, not approved"
        )
        return EXIT_OK
    if args.author_input_command == "report":
        project = find_project(args.project)
        config = load_config(project)
        state = load_author_input_state(project, config)
        languages = (
            list(config.get("languages", []))
            if args.lang == "all"
            else [args.lang]
        )
        unknown = set(languages) - set(config.get("languages", []))
        if unknown:
            raise ReadingPackError(
                f"language is not configured: {', '.join(sorted(unknown))}"
            )
        report_languages: dict[str, dict] = {}
        for language in languages:
            language_state = dict(state["languages"][language])
            data = load_language_data(project, language)
            counts: dict[str, int] = {}
            for module in AUTHOR_INPUT_MODULES:
                if module in AUTHOR_INPUT_COLLECTION_MODULES:
                    counts[module] = len(
                        data[AUTHOR_INPUT_COLLECTION_MODULES[module]]
                    )
                else:
                    field, empty = AUTHOR_INPUT_FIELD_MODULES[module]
                    counts[module] = sum(
                        chapter.get(field) != empty for chapter in data["chapters"]
                    )
            language_state["canonical_counts"] = counts
            report_languages[language] = language_state
        report = {
            "schema_version": state["schema_version"],
            "languages": report_languages,
        }
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            for language in languages:
                language_state = report["languages"][language]
                print(
                    f"{language}: attachments={len(language_state['attachments'])} "
                    f"history={len(language_state['history'])}"
                )
                for module in AUTHOR_INPUT_MODULES:
                    current = language_state["modules"][module]
                    source = current["source"]
                    source_text = source["id"] if source is not None else "-"
                    print(
                        f"  {module}: mode={current['mode']} "
                        f"records={language_state['canonical_counts'][module]} "
                        f"provided={len(current['provided_record_ids'])} "
                        f"source={source_text} package={current['package_id'] or '-'}"
                    )
        return EXIT_OK
    raise ReadingPackError(
        f"unknown author-input command: {args.author_input_command}", 2
    )
