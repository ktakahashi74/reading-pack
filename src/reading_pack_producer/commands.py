"""CLI registration for the optional producer plugin."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from reading_pack.errors import EXIT_IO, EXIT_OK, EXIT_VALIDATION, ReadingPackError
from reading_pack.project import find_project, load_config, load_language_data
from reading_pack.source_registry import registered_source, verify_registered_source
from reading_pack.validation import COLLECTIONS, errors, validate_project

from .agent_skill import build_agent_skill, check_agent_skill
from .author_qa import (
    classify_qa_plan,
    create_qa_candidate_run,
    create_qa_plan,
    load_qa_classifications,
    load_qa_plan,
    write_qa_plan,
)
from .candidates import (
    accept_candidates,
    apply_candidate_run,
    create_candidate_run,
    load_ai_review_decisions,
    load_candidate_run,
    reject_candidates,
    verify_candidate_run,
)
from .generation_session import (
    close_generation_work,
    create_generation_session,
    finalize_generation_session,
    generation_session_status,
    ingest_generation_response_file,
    next_generation_request,
    retry_generation_work,
    run_generation_adapter,
)
from .catalog_extraction import (
    CATALOG_COLLECTIONS,
    create_catalog_candidate_run,
    create_catalog_context_candidate_run,
    create_catalog_context_plan,
    extract_catalog,
    load_catalog_context_plan,
    load_catalog_inventory,
    load_chapter_spans,
    write_catalog_context_plan,
    write_catalog_inventory,
)
from .private_review import render_private_candidate_review
from .provenance_receipt import (
    AppliedRunArtifact,
    create_provenance_receipt,
    write_provenance_receipt,
)
from .review_bundle import ReviewBundleArtifact, render_private_review_bundle
from .work_ledger import (
    MODULES as WORK_MODULES,
    adjudicate_semantic_findings,
    coverage_report,
    create_semantic_review,
    create_work_ledger,
    load_semantic_findings,
    load_semantic_review,
    load_work_ledger,
    load_work_results,
    reconcile_work_results,
    write_semantic_review,
    write_work_ledger,
)

MAX_CANDIDATE_RESPONSE_BYTES = 16 * 1024 * 1024


def _validated(project: Path, *, release: bool = False) -> tuple[dict, dict[str, dict]]:
    config, data_by_lang, issues = validate_project(project, release=release)
    fatal = errors(issues)
    if fatal:
        raise ReadingPackError(f"validation failed with {len(fatal)} error(s)", EXIT_VALIDATION)
    return config, data_by_lang


def register(commands: argparse._SubParsersAction, review_commands: argparse._SubParsersAction) -> None:
    agent_skill = commands.add_parser(
        "agent-skill",
        help="build or check an optional Agent Skills-compatible distribution",
    )
    agent_skill_commands = agent_skill.add_subparsers(
        dest="agent_skill_command", required=True
    )
    agent_skill_build = agent_skill_commands.add_parser(
        "build", help="build a deterministic Agent Skill from current Reading Packs"
    )
    agent_skill_build.add_argument("--project", type=Path, default=Path.cwd())
    agent_skill_build.add_argument(
        "--release", action="store_true", help="enforce rights and human-review gates"
    )
    agent_skill_check = agent_skill_commands.add_parser(
        "check", help="read-only check of the Agent Skill directory and ZIP"
    )
    agent_skill_check.add_argument("--project", type=Path, default=Path.cwd())
    agent_skill_check.add_argument(
        "--release", action="store_true", help="enforce rights and human-review gates"
    )

    candidates = commands.add_parser(
        "candidates",
        help="quarantine, verify, and explicitly apply source-grounded draft candidates",
    )
    candidate_commands = candidates.add_subparsers(dest="candidate_command", required=True)

    candidate_create = candidate_commands.add_parser(
        "create", help="ingest transient candidate JSON into a private excerpt-free run"
    )
    candidate_create.add_argument("responses", type=Path)
    candidate_create.add_argument("--run-directory", type=Path, required=True)
    candidate_create.add_argument("--source", type=Path, required=True)
    candidate_create.add_argument("--project", type=Path, default=Path.cwd())
    candidate_create.add_argument("--lang", choices=("ja", "en"), required=True)
    candidate_create.add_argument("--run-id")
    candidate_create.add_argument(
        "--source-id",
        help=(
            "registered support-source ID; omit when --source is the imported "
            "primary book"
        ),
    )

    candidate_report = candidate_commands.add_parser(
        "report", help="show candidate states and reason codes without candidate prose"
    )
    candidate_report.add_argument("run", type=Path)
    candidate_report.add_argument("--json", action="store_true")

    candidate_review = candidate_commands.add_parser(
        "review",
        help="render a source-rehydrated private HTML review with per-candidate commands",
    )
    candidate_review.add_argument("run", type=Path)
    candidate_review.add_argument("--source", type=Path, required=True)
    candidate_review.add_argument("--project", type=Path, default=Path.cwd())
    candidate_review.add_argument(
        "--output",
        type=Path,
        metavar="NAME.html",
        help="direct child name under PROJECT/.reading-pack/reviews",
    )
    candidate_review.add_argument("--id", action="append", dest="candidate_ids")
    candidate_review.add_argument(
        "--semantic-review",
        type=Path,
        help="optional integrity-bound semantic review to show beside candidates",
    )
    candidate_review.add_argument("--context-characters", type=int, default=120)

    candidate_verify = candidate_commands.add_parser(
        "verify", help="recheck manifest integrity, source freshness, and evidence spans"
    )
    candidate_verify.add_argument("run", type=Path)
    candidate_verify.add_argument("--source", type=Path, required=True)
    candidate_verify.add_argument("--json", action="store_true")

    candidate_accept = candidate_commands.add_parser(
        "accept", help="bind explicit human or audited AI acceptance to exact candidate content"
    )
    candidate_accept.add_argument("run", type=Path)
    candidate_accept.add_argument("--id", action="append", dest="candidate_ids", required=True)
    candidate_accept.add_argument("--reviewer", required=True)
    candidate_accept.add_argument(
        "--reviewer-type", choices=("human", "ai"), default="human"
    )
    candidate_accept.add_argument(
        "--review-artifact",
        type=Path,
        help="required excerpt-free, run-bound decision artifact for --reviewer-type ai",
    )

    candidate_apply = candidate_commands.add_parser(
        "apply", help="CAS-apply explicitly accepted candidates as canonical draft data"
    )
    candidate_apply.add_argument("run", type=Path)
    candidate_apply.add_argument("--source", type=Path, required=True)
    candidate_apply.add_argument("--project", type=Path, default=Path.cwd())
    candidate_apply.add_argument("--lang", choices=("ja", "en"), required=True)
    candidate_apply.add_argument("--id", action="append", dest="candidate_ids", required=True)

    candidate_reject = candidate_commands.add_parser(
        "reject", help="record explicit candidate rejection without changing canonical data"
    )
    candidate_reject.add_argument("run", type=Path)
    candidate_reject.add_argument("--id", action="append", dest="candidate_ids", required=True)
    candidate_reject.add_argument("--reviewer")
    candidate_reject.add_argument(
        "--reviewer-type", choices=("human", "ai"), default="human"
    )
    candidate_reject.add_argument(
        "--review-artifact",
        type=Path,
        help="required excerpt-free, run-bound decision artifact for --reviewer-type ai",
    )
    candidate_receipt = candidate_commands.add_parser(
        "receipt",
        help="bind ordered terminal applied runs to one current canonical handoff receipt",
    )
    candidate_receipt.add_argument("--project", type=Path, default=Path.cwd())
    candidate_receipt.add_argument("--lang", choices=("ja", "en"), required=True)
    candidate_receipt.add_argument(
        "--artifact",
        action="append",
        nargs=2,
        metavar=("RUN", "SOURCE"),
        required=True,
        help="applied candidate run and exact source in application order",
    )
    candidate_receipt.add_argument(
        "--output",
        required=True,
        metavar="NAME.json",
        help="direct child name under PROJECT/.reading-pack/receipts",
    )
    candidate_receipt.add_argument(
        "--allow-legacy",
        action="store_true",
        help="include pre-receipt runs while marking their continuity unverified",
    )

    work = commands.add_parser(
        "work", help="plan and account for every module/chapter generation unit"
    )
    work_commands = work.add_subparsers(dest="work_command", required=True)
    work_plan = work_commands.add_parser(
        "plan", help="create a work ledger or a resumable generation session"
    )
    work_plan.add_argument("--project", type=Path, default=Path.cwd())
    work_plan.add_argument("--lang", choices=("ja", "en"), required=True)
    work_plan.add_argument("--module", action="append", dest="modules", choices=WORK_MODULES)
    work_plan.add_argument("--output", type=Path)
    work_plan.add_argument(
        "--session-directory",
        type=Path,
        help="create a private resumable session; modules default from AIP state",
    )
    work_plan.add_argument(
        "--source", type=Path, help="imported primary source for a resumable session"
    )
    work_plan.add_argument(
        "--chapter-map",
        type=Path,
        help=(
            "optional reviewed normalized-text chapter spans; when supplied, "
            "every chapter-scoped evidence snippet is checked at ingest"
        ),
    )
    work_plan.add_argument(
        "--purpose",
        choices=("initial", "coverage"),
        default="initial",
        help="initial drafting or a structured post-draft whole-book gap audit",
    )
    work_next = work_commands.add_parser(
        "next", help="return the next bounded prompt and JSON Schema as JSON"
    )
    work_next.add_argument("session", type=Path)
    work_next.add_argument("--project", type=Path, default=Path.cwd())
    work_next.add_argument("--source", type=Path, required=True)
    work_ingest = work_commands.add_parser(
        "ingest", help="ingest one bound response or run one explicit local adapter"
    )
    work_ingest.add_argument("session", type=Path)
    work_ingest.add_argument("response", type=Path, nargs="?")
    work_ingest.add_argument("--project", type=Path, default=Path.cwd())
    work_ingest.add_argument("--source", type=Path, required=True)
    work_ingest.add_argument("--adapter-executable")
    work_ingest.add_argument("--adapter-arg", action="append", default=[])
    work_ingest.add_argument("--timeout", type=float, default=60.0)
    work_close = work_commands.add_parser(
        "close", help="record a validated zero-result outcome for the next work item"
    )
    work_close.add_argument("session", type=Path)
    work_close.add_argument("--project", type=Path, default=Path.cwd())
    work_close.add_argument("--source", type=Path, required=True)
    work_close.add_argument(
        "--outcome",
        choices=("no_supported_candidate", "skipped"),
        required=True,
    )
    work_close.add_argument("--reason", dest="reason_code", required=True)
    work_status = work_commands.add_parser(
        "status", help="report resumable response and finalized ledger state"
    )
    work_status.add_argument("session", type=Path)
    work_status.add_argument("--json", action="store_true")
    work_retry = work_commands.add_parser(
        "retry", help="explicitly return one ingested work item for regeneration"
    )
    work_retry.add_argument("session", type=Path)
    work_retry.add_argument("--id", dest="work_id", required=True)
    work_retry.add_argument("--project", type=Path, default=Path.cwd())
    work_retry.add_argument("--source", type=Path, required=True)
    work_finalize = work_commands.add_parser(
        "finalize", help="create one standard candidate run and reconcile the ledger"
    )
    work_finalize.add_argument("session", type=Path)
    work_finalize.add_argument("--project", type=Path, default=Path.cwd())
    work_finalize.add_argument("--source", type=Path, required=True)
    work_finalize.add_argument("--run-directory", type=Path, required=True)
    work_reconcile = work_commands.add_parser("reconcile", help="bind terminal work outcomes to a candidate run")
    work_reconcile.add_argument("ledger", type=Path)
    work_reconcile.add_argument("results", type=Path)
    work_reconcile.add_argument("--run", type=Path, required=True)
    work_reconcile.add_argument("--output", type=Path, required=True)
    work_report = work_commands.add_parser("report", help="report coverage without assigning a quality score")
    work_report.add_argument("ledger", type=Path)
    work_report.add_argument("--semantic-review", type=Path)
    work_report.add_argument("--json", action="store_true")

    semantic = commands.add_parser(
        "semantic", help="bind and adjudicate excerpt-free independent semantic findings"
    )
    semantic_commands = semantic.add_subparsers(dest="semantic_command", required=True)
    semantic_ingest = semantic_commands.add_parser("ingest", help="ingest findings into a private semantic review")
    semantic_ingest.add_argument("findings", type=Path)
    semantic_ingest.add_argument("--ledger", type=Path, required=True)
    semantic_ingest.add_argument("--run", type=Path, required=True)
    semantic_ingest.add_argument("--output", type=Path, required=True)
    semantic_adjudicate = semantic_commands.add_parser("adjudicate", help="record a named human decision on exact findings")
    semantic_adjudicate.add_argument("review", type=Path)
    semantic_adjudicate.add_argument("--id", action="append", dest="finding_ids", required=True)
    semantic_adjudicate.add_argument("--decision", choices=("confirmed", "dismissed", "accepted_risk"), required=True)
    semantic_adjudicate.add_argument("--reviewer", required=True)
    semantic_report = semantic_commands.add_parser("report", help="show semantic review summary without excerpts")
    semantic_report.add_argument("review", type=Path)
    semantic_report.add_argument("--json", action="store_true")


    qa = commands.add_parser(
        "qa", help="plan, classify, and create draft candidates from author Q&A"
    )
    qa_commands = qa.add_subparsers(dest="qa_command", required=True)
    qa_plan = qa_commands.add_parser("plan", help="extract a body-free four-facet Q&A plan")
    qa_plan.add_argument("source", type=Path)
    qa_plan.add_argument("--source-id", required=True)
    qa_plan.add_argument("--project", type=Path, default=Path.cwd())
    qa_plan.add_argument("--output", type=Path, required=True)
    qa_classify = qa_commands.add_parser("classify", help="apply explicit source-key QA classifications")
    qa_classify.add_argument("plan", type=Path)
    qa_classify.add_argument("classifications", type=Path)
    qa_classify.add_argument("--output", type=Path, required=True)
    qa_candidates = qa_commands.add_parser("candidates", help="rehydrate an author-QA plan directly into a private candidate run")
    qa_candidates.add_argument("plan", type=Path)
    qa_candidates.add_argument("--source", type=Path, required=True)
    qa_candidates.add_argument("--project", type=Path, default=Path.cwd())
    qa_candidates.add_argument("--lang", choices=("ja", "en"), required=True)
    qa_candidates.add_argument("--run-directory", type=Path, required=True)
    qa_candidates.add_argument("--run-id")
    qa_candidates.add_argument(
        "--responses",
        type=Path,
        help=(
            "concise generated candidates covering every classified plan item; "
            "omit only when the Q&A facets are already concise"
        ),
    )


    review_bundle = review_commands.add_parser(
        "bundle",
        help="combine chapter, claim, index, reference, and author-Q&A runs",
    )
    review_bundle.add_argument("--project", type=Path, default=Path.cwd())
    review_bundle.add_argument(
        "--artifact",
        action="append",
        nargs=2,
        metavar=("RUN", "SOURCE"),
        required=True,
        help="candidate run and its exact source; repeat for multiple workflows",
    )
    review_bundle.add_argument(
        "--ledger",
        action="append",
        nargs=2,
        metavar=("RUN_ID", "FILE"),
        help="optional reconciled work ledger for one run ID",
    )
    review_bundle.add_argument(
        "--semantic-review",
        action="append",
        nargs=2,
        metavar=("RUN_ID", "FILE"),
        help="optional semantic review for one run ID; requires its ledger",
    )
    review_bundle.add_argument(
        "--catalog",
        action="append",
        nargs=2,
        metavar=("RUN_ID", "INVENTORY"),
        help="optional bound catalog inventory for one run ID; repeat as needed",
    )
    review_bundle.add_argument(
        "--output",
        type=Path,
        metavar="NAME.html",
        help="direct child name under PROJECT/.reading-pack/reviews",
    )
    review_bundle.add_argument("--context-characters", type=int, default=120)

    catalog = commands.add_parser(
        "catalog", help="extract and review people, subject terms, and references"
    )
    catalog_commands = catalog.add_subparsers(dest="catalog_command", required=True)
    catalog_extract = catalog_commands.add_parser(
        "extract", help="create a private source-grounded catalog inventory"
    )
    catalog_extract.add_argument("source", type=Path)
    catalog_extract.add_argument("--source-id", default="SRC-1")
    catalog_extract.add_argument("--project", type=Path, default=Path.cwd())
    catalog_extract.add_argument("--lang", choices=("ja", "en"), required=True)
    catalog_extract.add_argument(
        "--chapter-map",
        type=Path,
        help="explicit normalized-text chapter spans; otherwise infer by title sequence",
    )
    catalog_extract.add_argument("--output", type=Path, required=True)
    catalog_report = catalog_commands.add_parser(
        "report", help="show catalog coverage counts without candidate prose"
    )
    catalog_report.add_argument("inventory", type=Path)
    catalog_report.add_argument("--json", action="store_true")
    catalog_candidates = catalog_commands.add_parser(
        "candidates", help="create one combined private catalog candidate run"
    )
    catalog_candidates.add_argument("inventory", type=Path)
    catalog_candidates.add_argument("--source", type=Path, required=True)
    catalog_candidates.add_argument("--project", type=Path, default=Path.cwd())
    catalog_candidates.add_argument("--lang", choices=("ja", "en"), required=True)
    catalog_candidates.add_argument(
        "--collection",
        action="append",
        choices=CATALOG_COLLECTIONS,
        dest="collections",
        help="repeat to limit modules; omit to combine all catalog modules",
    )
    catalog_candidates.add_argument("--run-directory", type=Path, required=True)
    catalog_candidates.add_argument("--run-id")
    catalog_candidates.add_argument("--ledger-output", type=Path, required=True)
    catalog_candidates.add_argument(
        "--responses",
        type=Path,
        help=(
            "optional model/NER additions; every value and chapter assignment "
            "is rechecked against the catalog source map"
        ),
    )
    catalog_context_plan = catalog_commands.add_parser(
        "context-plan",
        help="plan source-grounded descriptions for retained people and terms",
    )
    catalog_context_plan.add_argument("inventory", type=Path)
    catalog_context_plan.add_argument("--project", type=Path, default=Path.cwd())
    catalog_context_plan.add_argument("--lang", choices=("ja", "en"), required=True)
    catalog_context_plan.add_argument(
        "--collection",
        action="append",
        choices=("names", "glossary"),
        dest="collections",
        help="repeat to limit modules; omit to describe both people and terms",
    )
    catalog_context_plan.add_argument(
        "--refresh-existing",
        action="store_true",
        help="include existing descriptions so they can be source-grounded and replaced",
    )
    catalog_context_plan.add_argument("--output", type=Path, required=True)
    catalog_context_candidates = catalog_commands.add_parser(
        "context-candidates",
        help="verify complete book-specific descriptions and create update candidates",
    )
    catalog_context_candidates.add_argument("plan", type=Path)
    catalog_context_candidates.add_argument("--source", type=Path, required=True)
    catalog_context_candidates.add_argument("--responses", type=Path, required=True)
    catalog_context_candidates.add_argument("--project", type=Path, default=Path.cwd())
    catalog_context_candidates.add_argument("--lang", choices=("ja", "en"), required=True)
    catalog_context_candidates.add_argument("--run-directory", type=Path, required=True)
    catalog_context_candidates.add_argument("--run-id")

    agent_skill.set_defaults(_handler=command_agent_skill)
    candidates.set_defaults(_handler=command_candidates)
    work.set_defaults(_handler=command_work)
    semantic.set_defaults(_handler=command_semantic)
    qa.set_defaults(_handler=command_qa)
    review_bundle.set_defaults(_handler=command_review_bundle)
    catalog.set_defaults(_handler=command_catalog)


def command_agent_skill(args: argparse.Namespace) -> int:
    project = find_project(args.project)
    config, data_by_lang = _validated(project, release=args.release)
    if args.agent_skill_command == "build":
        directory, archive = build_agent_skill(project, config, data_by_lang)
        print(f"built {directory.relative_to(project)}")
        print(f"built {archive.relative_to(project)}")
        return EXIT_OK
    if args.agent_skill_command == "check":
        directory, archive = check_agent_skill(project, config, data_by_lang)
        print(f"OK byte-identical: {directory.relative_to(project)}")
        print(f"OK byte-identical: {archive.relative_to(project)}")
        mode = "release" if args.release else "technical"
        print(f"Agent Skill {mode} check passed")
        return EXIT_OK
    raise ReadingPackError(
        f"unknown agent-skill command: {args.agent_skill_command}", 2
    )


def _candidate_report(manifest: dict) -> dict:
    return {
        "run_id": manifest.get("run_id", ""),
        "language": manifest.get("language", ""),
        "source": {
            "name": manifest.get("source", {}).get("name", ""),
            "sha256": manifest.get("source", {}).get("sha256", ""),
        },
        "summary": manifest.get("summary", {}),
        "candidates": [
            {
                "candidate_id": item.get("candidate_id", ""),
                "collection": item.get("collection", ""),
                "record_id": item.get("record_id", ""),
                "state": item.get("candidate_state", ""),
                "reason_codes": item.get("qa", {}).get("reason_codes", []),
            }
            for item in manifest.get("candidates", [])
        ],
    }


def command_candidates(args: argparse.Namespace) -> int:
    if args.candidate_command == "create":
        project = find_project(args.project)
        config = load_config(project)
        data = load_language_data(project, args.lang)
        support_source = None
        if args.source_id:
            support_source = verify_registered_source(
                project,
                args.source_id,
                args.source.resolve(),
            )
            if support_source["role"] == "primary-book":
                raise ReadingPackError(
                    "--source-id is for a registered support source; omit it for the primary book"
                )
        project_data_by_lang = {
            language: load_language_data(project, language)
            for language in config.get("languages", [])
        }
        run_directory = args.run_directory.resolve()
        if run_directory.is_relative_to(project) and not run_directory.is_relative_to(
            project / ".reading-pack"
        ):
            raise ReadingPackError(
                "candidate runs inside a project must be under .reading-pack/ so they remain private"
            )
        try:
            response_path = args.responses.resolve()
            if response_path.stat().st_size > MAX_CANDIDATE_RESPONSE_BYTES:
                raise ReadingPackError(
                    f"candidate responses exceed {MAX_CANDIDATE_RESPONSE_BYTES} bytes",
                    EXIT_IO,
                )
            responses = response_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise ReadingPackError(
                f"cannot read candidate responses {args.responses}: {exc}", EXIT_IO
            ) from exc
        manifest_path = create_candidate_run(
            run_directory,
            source_path=args.source.resolve(),
            responses=responses,
            language=args.lang,
            canonical_data=data,
            project_data_by_lang=project_data_by_lang,
            known_chapter_ids={
                record.get("id")
                for record in data.get("chapters", [])
                if isinstance(record, dict) and isinstance(record.get("id"), str)
            },
            run_id=args.run_id,
            generator={"adapter": "manual-json", "model": ""},
            support_source=support_source,
        )
        report = _candidate_report(load_candidate_run(manifest_path))
        summary = report["summary"]
        print(
            f"created private candidate run {manifest_path.parent} "
            f"(ready={summary.get('ready_for_review', 0)}, "
            f"quarantined={summary.get('quarantined', 0)}); evidence excerpts were discarded"
        )
        return EXIT_OK

    if args.candidate_command == "report":
        report = _candidate_report(load_candidate_run(args.run.resolve()))
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            summary = report["summary"]
            print(
                f"run={report['run_id']} source={report['source']['name']} "
                f"total={summary.get('total', 0)} ready={summary.get('ready_for_review', 0)} "
                f"quarantined={summary.get('quarantined', 0)} applied={summary.get('applied', 0)}"
            )
            for item in report["candidates"]:
                reasons = ",".join(item["reason_codes"]) or "-"
                print(
                    f"{item['candidate_id']} {item['collection']} {item['record_id']} "
                    f"state={item['state']} reasons={reasons}"
                )
        return EXIT_OK

    if args.candidate_command == "verify":
        result = verify_candidate_run(
            args.run.resolve(),
            source_path=args.source.resolve(),
        )
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(
                f"verified run={result['run_id']} source={result['source']['name']} "
                f"candidates={result['summary'].get('total', 0)}"
            )
        return EXIT_OK

    if args.candidate_command == "review":
        project = find_project(args.project)
        output = render_private_candidate_review(
            project,
            run=args.run.resolve(),
            source_path=args.source.resolve(),
            output_path=args.output,
            candidate_ids=args.candidate_ids,
            context_characters=args.context_characters,
            semantic_review_path=args.semantic_review,
        )
        print(f"created private review {output}; it contains source excerpts and must not be published")
        return EXIT_OK

    if args.candidate_command == "accept":
        review: dict[str, str] = {}
        if args.reviewer_type == "ai":
            if args.review_artifact is None:
                raise ReadingPackError(
                    "--review-artifact is required for --reviewer-type ai", 2
                )
            review = load_ai_review_decisions(
                args.review_artifact,
                run=args.run.resolve(),
                candidate_ids=args.candidate_ids,
                reviewer=args.reviewer,
                decision="accept",
            )
        elif args.review_artifact is not None:
            raise ReadingPackError(
                "--review-artifact is only valid with --reviewer-type ai", 2
            )
        accepted = accept_candidates(
            args.run.resolve(),
            args.candidate_ids,
            reviewer=args.reviewer,
            reviewer_type=args.reviewer_type,
            review_method=review.get("method"),
            review_artifact_sha256=review.get("artifact_sha256", ""),
            reviewed_at=review.get("reviewed_at"),
        )
        print(
            f"accepted {len(accepted)} candidate(s) by {args.reviewer_type} review for draft application; "
            "canonical data was unchanged"
        )
        return EXIT_OK

    if args.candidate_command == "apply":
        project = find_project(args.project)
        applied = apply_candidate_run(
            project,
            language=args.lang,
            run=args.run.resolve(),
            source_path=args.source.resolve(),
            candidate_ids=args.candidate_ids,
        )
        print(f"applied {len(applied)} candidate(s) as draft; no approval was granted")
        return EXIT_OK

    if args.candidate_command == "receipt":
        project = find_project(args.project)
        output = Path(args.output)
        if output.name != str(output) or output.suffix != ".json":
            raise ReadingPackError(
                "--output must be one direct child JSON filename", 2
            )
        receipt = create_provenance_receipt(
            project,
            language=args.lang,
            artifacts=[
                AppliedRunArtifact(Path(run), Path(source))
                for run, source in args.artifact
            ],
            allow_legacy=args.allow_legacy,
        )
        destination = write_provenance_receipt(
            project / ".reading-pack" / "receipts" / output.name,
            receipt,
        )
        print(
            json.dumps(
                {
                    "receipt_id": receipt["receipt_id"],
                    "path": str(destination),
                    "language": receipt["language"],
                    "runs": len(receipt["runs"]),
                    "continuity": receipt["continuity"],
                    "approval_granted": False,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return EXIT_OK

    if args.candidate_command == "reject":
        review: dict[str, str] = {}
        if args.reviewer_type == "ai":
            if not args.reviewer or args.review_artifact is None:
                raise ReadingPackError(
                    "--reviewer and --review-artifact are required for --reviewer-type ai",
                    2,
                )
            review = load_ai_review_decisions(
                args.review_artifact,
                run=args.run.resolve(),
                candidate_ids=args.candidate_ids,
                reviewer=args.reviewer,
                decision="reject",
            )
        elif args.review_artifact is not None:
            raise ReadingPackError(
                "--review-artifact is only valid with --reviewer-type ai", 2
            )
        rejected = reject_candidates(
            args.run.resolve(),
            args.candidate_ids,
            reviewer=args.reviewer,
            reviewer_type=args.reviewer_type,
            review_method=review.get("method"),
            review_artifact_sha256=review.get("artifact_sha256", ""),
            reviewed_at=review.get("reviewed_at"),
        )
        print(
            f"rejected {len(rejected)} candidate(s)"
            + (f" by {args.reviewer_type} review" if args.reviewer else "")
            + "; canonical data was unchanged"
        )
        return EXIT_OK

    raise ReadingPackError(f"unknown candidates command: {args.candidate_command}", 2)


def command_work(args: argparse.Namespace) -> int:
    if args.work_command == "plan":
        project = find_project(args.project)
        if args.session_directory is not None:
            if args.output is not None:
                raise ReadingPackError(
                    "--output cannot be combined with --session-directory", 2
                )
            if args.source is None:
                raise ReadingPackError(
                    "--source is required with --session-directory", 2
                )
            session = create_generation_session(
                args.session_directory,
                project=project,
                language=args.lang,
                source_path=args.source,
                modules=args.modules,
                purpose=args.purpose,
                chapter_map=args.chapter_map,
            )
            print(
                json.dumps(
                    {
                        "session_id": session["session_id"],
                        "session_directory": str(args.session_directory.resolve()),
                        "modules": sorted({item["module"] for item in session["items"]}),
                        "purpose": session.get("purpose", "initial"),
                        "summary": session["summary"],
                        "approval_granted": False,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return EXIT_OK
        if args.output is None or not args.modules:
            raise ReadingPackError(
                "ordinary work plan requires --module and --output", 2
            )
        if args.purpose != "initial":
            raise ReadingPackError("--purpose requires --session-directory", 2)
        if args.source is not None:
            raise ReadingPackError("--source requires --session-directory", 2)
        if args.chapter_map is not None:
            raise ReadingPackError("--chapter-map requires --session-directory", 2)
        ledger = create_work_ledger(
            language=args.lang,
            canonical_data=load_language_data(project, args.lang),
            modules=args.modules,
        )
        write_work_ledger(args.output, ledger)
        print(f"created work ledger {args.output.resolve()} with {ledger['summary']['total']} pending item(s)")
        return EXIT_OK
    if args.work_command == "next":
        print(
            json.dumps(
                next_generation_request(
                    args.session,
                    project=args.project,
                    source_path=args.source,
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return EXIT_OK
    if args.work_command == "ingest":
        if args.adapter_executable:
            if args.response is not None:
                raise ReadingPackError(
                    "response path cannot be combined with --adapter-executable", 2
                )
            result = run_generation_adapter(
                args.session,
                project=args.project,
                source_path=args.source,
                command=[args.adapter_executable, *args.adapter_arg],
                timeout=args.timeout,
            )
        else:
            if args.response is None or args.adapter_arg:
                raise ReadingPackError(
                    "ingest requires a response path or --adapter-executable", 2
                )
            result = ingest_generation_response_file(
                args.session,
                args.response,
                project=args.project,
                source_path=args.source,
            )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return EXIT_OK
    if args.work_command == "close":
        result = close_generation_work(
            args.session,
            project=args.project,
            source_path=args.source,
            outcome=args.outcome,
            reason_code=args.reason_code,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return EXIT_OK
    if args.work_command == "status":
        report = generation_session_status(args.session)
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            summary = report["summary"]
            print(
                f"session={report['session_id']} state={report['state']} "
                f"total={summary['total']} ingested={summary['ingested']} "
                f"awaiting={summary['awaiting_response']}"
            )
        return EXIT_OK
    if args.work_command == "retry":
        result = retry_generation_work(
            args.session,
            args.work_id,
            project=args.project,
            source_path=args.source,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return EXIT_OK
    if args.work_command == "finalize":
        result = finalize_generation_session(
            args.session,
            project=args.project,
            source_path=args.source,
            run_directory=args.run_directory,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return EXIT_OK
    if args.work_command == "reconcile":
        ledger = load_work_ledger(args.ledger)
        run = load_candidate_run(args.run)
        results = load_work_results(args.results, ledger=ledger, run_id=run["run_id"])
        reconciled = reconcile_work_results(ledger, results, run)
        write_work_ledger(args.output, reconciled)
        print(f"reconciled {len(results['results'])} work item(s); no approval was granted")
        return EXIT_OK
    if args.work_command == "report":
        ledger = load_work_ledger(args.ledger)
        review = load_semantic_review(args.semantic_review) if args.semantic_review else None
        report = coverage_report(ledger, review)
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            summary = report["summary"]
            print(
                f"plan={report['plan_id']} generation={report['generation_state']} "
                f"semantic={report['semantic']['state']} total={summary['total']} "
                f"pending={summary['pending']} failed={summary['failed']}"
            )
        return EXIT_OK
    raise ReadingPackError(f"unknown work command: {args.work_command}", 2)


def command_semantic(args: argparse.Namespace) -> int:
    if args.semantic_command == "ingest":
        ledger = load_work_ledger(args.ledger)
        run = load_candidate_run(args.run)
        findings = load_semantic_findings(args.findings, ledger=ledger, run_id=run["run_id"])
        review = create_semantic_review(ledger=ledger, candidate_run=run, findings_input=findings)
        write_semantic_review(args.output, review)
        print(f"created semantic review {args.output.resolve()} with {review['summary']['total']} finding(s)")
        return EXIT_OK
    if args.semantic_command == "adjudicate":
        changed = adjudicate_semantic_findings(
            args.review,
            args.finding_ids,
            decision=args.decision,
            reviewer=args.reviewer,
        )
        print(f"adjudicated {len(changed)} semantic finding(s); no content approval was granted")
        return EXIT_OK
    if args.semantic_command == "report":
        review = load_semantic_review(args.review)
        report = {
            "review_id": review["review_id"],
            "plan_id": review["plan_id"],
            "ledger_integrity_sha256": review["ledger_integrity_sha256"],
            "run_id": review["run"]["run_id"],
            "assessment": review["assessment"],
            "summary": review["summary"],
            "findings": [
                {
                    "finding_id": item["finding_id"],
                    "candidate_id": item["candidate_id"],
                    "category": item["category"],
                    "severity": item["severity"],
                    "reason_code": item["reason_code"],
                    "decision": item["adjudication"]["decision"],
                }
                for item in review["findings"]
            ],
        }
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            summary = report["summary"]
            print(
                f"review={report['review_id']} total={summary['total']} "
                f"pending={summary['pending']} blocking_errors={summary['blocking_errors']}"
            )
        return EXIT_OK
    raise ReadingPackError(f"unknown semantic command: {args.semantic_command}", 2)


def command_qa(args: argparse.Namespace) -> int:
    if args.qa_command == "plan":
        project = find_project(args.project)
        plan = create_qa_plan(args.source, registered_source(project, args.source_id))
        write_qa_plan(args.output, plan)
        print(
            f"created body-free Q&A plan {args.output.resolve()} "
            f"items={len(plan['items'])} outcome={plan['outcome']}"
        )
        return EXIT_OK
    if args.qa_command == "classify":
        plan = classify_qa_plan(
            load_qa_plan(args.plan),
            load_qa_classifications(args.classifications),
        )
        write_qa_plan(args.output, plan)
        print(f"classified Q&A plan {args.output.resolve()} outcome={plan['outcome']}")
        return EXIT_OK
    if args.qa_command == "candidates":
        project = find_project(args.project)
        plan = load_qa_plan(args.plan)
        generated_responses = None
        if args.responses is not None:
            try:
                response_path = args.responses.resolve()
                if response_path.stat().st_size > MAX_CANDIDATE_RESPONSE_BYTES:
                    raise ReadingPackError(
                        f"Q&A candidate responses exceed {MAX_CANDIDATE_RESPONSE_BYTES} bytes",
                        EXIT_IO,
                    )
                generated_responses = response_path.read_text(encoding="utf-8")
            except ReadingPackError:
                raise
            except (OSError, UnicodeError) as exc:
                raise ReadingPackError(
                    f"cannot read Q&A candidate responses {args.responses}: {exc}",
                    EXIT_IO,
                ) from exc
        run_directory = args.run_directory.resolve()
        if run_directory.is_relative_to(project) and not run_directory.is_relative_to(project / ".reading-pack"):
            raise ReadingPackError("Q&A candidate runs inside a project must be under .reading-pack/")
        manifest_path = create_qa_candidate_run(
            project,
            language=args.lang,
            plan=plan,
            source_path=args.source,
            run_directory=run_directory,
            run_id=args.run_id,
            generated_responses=generated_responses,
        )
        summary = load_candidate_run(manifest_path)["summary"]
        print(
            f"created author-Q&A candidate run {manifest_path.parent} "
            f"ready={summary['ready_for_review']} quarantined={summary['quarantined']}"
        )
        return EXIT_OK
    raise ReadingPackError(f"unknown qa command: {args.qa_command}", 2)


def _review_bundle_paths(
    values: list[list[str]] | None, label: str
) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for run_id, value in values or []:
        if run_id in result:
            raise ReadingPackError(f"duplicate {label} for run ID: {run_id}", 2)
        result[run_id] = Path(value)
    return result


def command_review_bundle(args: argparse.Namespace) -> int:
    project = find_project(args.project)
    ledgers = _review_bundle_paths(args.ledger, "ledger")
    semantic_reviews = _review_bundle_paths(
        args.semantic_review, "semantic review"
    )
    catalogs = _review_bundle_paths(args.catalog, "catalog inventory")
    artifacts: list[ReviewBundleArtifact] = []
    seen_run_ids: set[str] = set()
    for run_value, source_value in args.artifact:
        run = Path(run_value)
        manifest = load_candidate_run(run)
        run_id = manifest["run_id"]
        if run_id in seen_run_ids:
            raise ReadingPackError(
                f"duplicate candidate run in review bundle: {run_id}", 2
            )
        seen_run_ids.add(run_id)
        artifacts.append(
            ReviewBundleArtifact(
                run=run,
                source_path=Path(source_value),
                work_ledger_path=ledgers.pop(run_id, None),
                semantic_review_path=semantic_reviews.pop(run_id, None),
                catalog_inventory_path=catalogs.pop(run_id, None),
            )
        )
    unused = sorted(set(ledgers) | set(semantic_reviews) | set(catalogs))
    if unused:
        raise ReadingPackError(
            "review metadata references a run ID not supplied by --artifact: "
            + ", ".join(unused),
            2,
        )
    destination = render_private_review_bundle(
        project,
        artifacts=artifacts,
        output_path=args.output,
        context_characters=args.context_characters,
    )
    print(
        f"created read-only private review bundle {destination} "
        f"runs={len(artifacts)}; no candidates were accepted or applied"
    )
    return EXIT_OK


def _require_private_project_path(project: Path, path: Path, label: str) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(project / ".reading-pack"):
        raise ReadingPackError(
            f"{label} must be under the project's .reading-pack/ directory", EXIT_IO
        )
    return resolved


def command_catalog(args: argparse.Namespace) -> int:
    if args.catalog_command == "extract":
        project = find_project(args.project)
        output = _require_private_project_path(
            project, args.output, "catalog inventory"
        )
        spans = (
            load_chapter_spans(args.chapter_map)
            if args.chapter_map is not None
            else None
        )
        inventory = extract_catalog(
            project,
            args.source_id,
            args.source,
            language=args.lang,
            chapter_spans=spans,
        )
        write_catalog_inventory(output, inventory)
        summary = inventory["summary"]
        chapter_map = inventory["chapter_map"]
        print(
            f"created private catalog inventory {output} "
            f"people={summary['people']} terms={summary['terms']} "
            f"references={summary['references']} "
            f"unresolved_people={summary['unresolved_people']} "
            f"unresolved_terms={summary['unresolved_terms']} "
            f"chapter_map={chapter_map['method']} "
            f"review_required={str(chapter_map['review_required']).lower()}"
        )
        return EXIT_OK
    if args.catalog_command == "report":
        inventory = load_catalog_inventory(args.inventory)
        report = {
            "inventory_id": inventory["inventory_id"],
            "language": inventory["language"],
            "source": {
                "id": inventory["source"]["id"],
                "role": inventory["source"]["role"],
                "name": inventory["source"]["name"],
                "sha256": inventory["source"]["sha256"],
            },
            "chapter_map": inventory["chapter_map"],
            "summary": inventory["summary"],
        }
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            summary = report["summary"]
            print(
                f"inventory={report['inventory_id']} people={summary['people']} "
                f"terms={summary['terms']} references={summary['references']} "
                f"unresolved_people={summary['unresolved_people']} "
                f"unresolved_terms={summary['unresolved_terms']} "
                f"chapter_map={report['chapter_map']['method']}"
            )
        return EXIT_OK
    if args.catalog_command == "candidates":
        project = find_project(args.project)
        run_directory = _require_private_project_path(
            project, args.run_directory, "catalog candidate run"
        )
        ledger_output = _require_private_project_path(
            project, args.ledger_output, "catalog work ledger"
        )
        generated_responses = None
        if args.responses is not None:
            try:
                response_path = args.responses.resolve()
                if response_path.stat().st_size > MAX_CANDIDATE_RESPONSE_BYTES:
                    raise ReadingPackError(
                        f"catalog candidate responses exceed {MAX_CANDIDATE_RESPONSE_BYTES} bytes",
                        EXIT_IO,
                    )
                generated_responses = response_path.read_text(encoding="utf-8")
            except ReadingPackError:
                raise
            except (OSError, UnicodeError) as exc:
                raise ReadingPackError(
                    f"cannot read catalog candidate responses {args.responses}: {exc}",
                    EXIT_IO,
                ) from exc
        manifest_path, ledger_path = create_catalog_candidate_run(
            project,
            language=args.lang,
            inventory=load_catalog_inventory(args.inventory),
            source_path=args.source,
            run_directory=run_directory,
            run_id=args.run_id,
            collections=args.collections or CATALOG_COLLECTIONS,
            ledger_output=ledger_output,
            generated_responses=generated_responses,
        )
        manifest = load_candidate_run(manifest_path)
        ledger = load_work_ledger(ledger_path)
        print(
            f"created combined catalog candidate run {manifest_path.parent} "
            f"ready={manifest['summary']['ready_for_review']} "
            f"quarantined={manifest['summary']['quarantined']} "
            f"accounted={ledger['summary']['total'] - ledger['summary']['pending']}/"
            f"{ledger['summary']['total']} "
            f"failed={ledger['summary']['failed']} "
            f"no_supported_candidate={ledger['summary']['no_supported_candidate']}; "
            "failed scopes require omission review; no candidates were accepted or applied"
        )
        return EXIT_OK
    if args.catalog_command == "context-plan":
        project = find_project(args.project)
        output = _require_private_project_path(
            project, args.output, "catalog context plan"
        )
        plan = create_catalog_context_plan(
            project,
            language=args.lang,
            inventory=load_catalog_inventory(args.inventory),
            collections=args.collections or ("names", "glossary"),
            refresh_existing=args.refresh_existing,
        )
        write_catalog_context_plan(output, plan)
        print(
            f"created private catalog context plan {output} "
            f"targets={plan['summary']['total']} names={plan['summary']['names']} "
            f"glossary={plan['summary']['glossary']}; no manuscript excerpts stored"
        )
        return EXIT_OK
    if args.catalog_command == "context-candidates":
        project = find_project(args.project)
        run_directory = _require_private_project_path(
            project, args.run_directory, "catalog context candidate run"
        )
        try:
            response_path = args.responses.resolve()
            if response_path.stat().st_size > MAX_CANDIDATE_RESPONSE_BYTES:
                raise ReadingPackError(
                    f"catalog context responses exceed {MAX_CANDIDATE_RESPONSE_BYTES} bytes",
                    EXIT_IO,
                )
            responses = response_path.read_text(encoding="utf-8")
        except ReadingPackError:
            raise
        except (OSError, UnicodeError) as exc:
            raise ReadingPackError(
                f"cannot read catalog context responses {args.responses}: {exc}",
                EXIT_IO,
            ) from exc
        manifest_path = create_catalog_context_candidate_run(
            project,
            language=args.lang,
            plan=load_catalog_context_plan(args.plan),
            source_path=args.source,
            responses=responses,
            run_directory=run_directory,
            run_id=args.run_id,
        )
        manifest = load_candidate_run(manifest_path)
        print(
            f"created catalog context candidate run {manifest_path.parent} "
            f"ready={manifest['summary']['ready_for_review']} "
            f"quarantined={manifest['summary']['quarantined']}; "
            "every plan target was accounted for, but no candidate was accepted or applied"
        )
        return EXIT_OK
    raise ReadingPackError(f"unknown catalog command: {args.catalog_command}", 2)
