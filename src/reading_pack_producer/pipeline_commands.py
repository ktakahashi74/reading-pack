"""Command surface for bounded automatic production."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from reading_pack.errors import ReadingPackError
from reading_pack.project import write_json
from reading_pack.source_registry import SOURCE_ROLES
from .pipeline_contracts import SUPPORTED_CONTRACT_VERSIONS
from .pipeline_reuse import import_preparation, restart_pipeline
from .pipeline import _read, _seal, _unseal, default_recipe, finalize_pipeline, pipeline_status, resume_pipeline, start_pipeline


def register(commands: argparse._SubParsersAction) -> None:
    parent = commands.add_parser('pipeline', help='deliver a Pack with quantitative evaluation; preserve explicit legacy workflows')
    sub = parent.add_subparsers(dest='pipeline_command', required=True)
    delivery_recipe = sub.add_parser('delivery-recipe', help='configure fixed generation/evaluation without a quality gate')
    delivery_recipe.add_argument('--output', type=Path, required=True)
    for role in ('generator', 'evaluator'):
        delivery_recipe.add_argument('--' + role, required=True, help='local JSON adapter executable')
        delivery_recipe.add_argument('--' + role + '-model', required=True)
    delivery_recipe.add_argument('--max-cost-usd', type=float, required=True)
    delivery_recipe.add_argument('--call-allowance-usd', type=float, required=True)
    delivery_recipe.add_argument('--max-wall-seconds', type=int, required=True)
    delivery_recipe.add_argument('--timeout-seconds', type=int, default=300)
    delivery_recipe.add_argument('--prior-cost-usd', type=float, default=0)
    delivery_recipe.add_argument('--cumulative-cost-limit-usd', type=float)
    delivery_recipe.add_argument('--language', choices=('ja', 'en'), default='ja')
    delivery_recipe.add_argument('--scope', required=True)
    delivery_recipe.add_argument('--global-call-allowance-usd', type=float, help='allowance for the whole-Pack evaluation call')
    delivery_recipe.add_argument('--evaluator-timeout-seconds', type=int, help='controller timeout for chapter evaluation calls')
    delivery_recipe.add_argument('--global-timeout-seconds', type=int, help='controller timeout for the whole-Pack evaluation call')
    deliver = sub.add_parser('deliver', help='recommended: generate and deliver quality reports; user decides adoption')
    deliver.add_argument('source', type=Path, nargs='?')
    deliver.add_argument('--run', type=Path, required=True)
    deliver.add_argument('--recipe', type=Path)
    deliver.add_argument('--title')
    deliver.add_argument('--author', default='')
    deliver.add_argument('--format', choices=('markdown', 'org', 'text'))
    deliver.add_argument('--chapter-level', type=int, choices=range(1, 9))
    deliver.add_argument('--seed', type=Path, help='existing Reading Pack project whose author-provided modules are carried unchanged')
    deliver.add_argument('--chapter-map', type=Path, help='JSON object: seed chapter id -> exact manuscript heading')
    deliver.add_argument('--seed-policy', choices=('preserve', 'regenerate'), default='preserve',
                         help='preserve: fill only empty seed summaries/terms; regenerate: replace them as drafts')
    deliver.add_argument('--predecessor', type=Path, help='finished delivery run whose incomplete jobs are redone; completed exchanges are carried')
    deliver.add_argument('--output', type=Path, help='where deliverables and their manifest are written; default: a sibling of --run named <run>-delivery')
    export = sub.add_parser('export', help='copy only the deliverables and a hash manifest out of a finished delivery run')
    export.add_argument('--run', type=Path, required=True)
    export.add_argument('--output', type=Path)
    export.set_defaults(_handler=command_pipeline)
    deliver.add_argument('--prepare-only', action='store_true')
    delivery_recipe.set_defaults(_handler=command_pipeline)
    deliver.set_defaults(_handler=command_pipeline)
    recipe = sub.add_parser('recipe', help='write a bounded recipe for explicitly configured local JSON adapters')
    recipe.add_argument('--output', type=Path, required=True)
    recipe.add_argument('--operating-mode', choices=('qualification', 'production'))
    recipe.add_argument('--contract-version', choices=SUPPORTED_CONTRACT_VERSIONS,
                        help='explicitly select the frozen pipeline contract')
    recipe.add_argument('--experimental', action='store_true', help='explicitly create the legacy, unqualified research workflow')
    recipe.add_argument('--max-wall-seconds', type=int, default=7200)
    recipe.add_argument('--max-cost-usd', type=float)
    recipe.add_argument('--call-allowance-usd', type=float)
    recipe.add_argument('--phase-calls', help='JSON object allocating preparation, generation, development, repair and final calls')
    recipe.add_argument('--repair-rounds', type=int, choices=(0,1), help='artifact contract only: freeze zero or one repair round')
    recipe.add_argument('--scope', help='declared manuscript coverage, e.g. a named chapter excerpt; never inferred as a full edition')
    for role in ('generator', 'judge', 'reader'):
        recipe.add_argument('--' + role, required=role!='reader', help='local executable path; reader optional for artifact contract')
        recipe.add_argument('--' + role + '-model', required=role!='reader', help='exact expected model identity')
    start = sub.add_parser('start', help='snapshot manuscript and optional supplements, then run automatically')
    start.add_argument('source', type=Path)
    start.add_argument('--run', type=Path, required=True)
    start.add_argument('--recipe', type=Path, required=True)
    start.add_argument('--project', type=Path, help='optional canonical seed; original remains unchanged')
    start.add_argument('--supplement', action='append', nargs=2, metavar=('ROLE', 'PATH'), default=[])
    start.add_argument('--title')
    start.add_argument('--author')
    start.add_argument('--format', choices=('markdown', 'org', 'text', 'pdf', 'pdf-vertical', 'epub3'))
    start.add_argument('--reuse-preparation-from', type=Path, help='reuse identical source/structure/question exchanges from an earlier run with unused holdout')
    start.add_argument('--prepare-only', action='store_true', help='freeze inputs without invoking adapters')
    start.add_argument('--qualification', type=Path, help='measured qualification report required by production recipes')
    start.add_argument('--experimental', action='store_true', help='explicitly run a legacy recipe without an operating envelope')
    plan = sub.add_parser('plan', help='show the full operating envelope and reserved final evaluation, without model calls')
    plan.add_argument('--run', type=Path, required=True)
    registration = sub.add_parser('qualification-register', help='bind a fresh prepared trial to a preregistered qualification case')
    registration.add_argument('--run', type=Path, required=True)
    registration.add_argument('--suite', type=Path, required=True)
    registration.add_argument('--case', required=True)
    registration.add_argument('--repetition', type=int, required=True)
    qualification = sub.add_parser('qualification-report', help='assess every preregistered live trial, including failures, without sending')
    qualification.add_argument('--suite', type=Path, required=True)
    qualification.add_argument('--run', type=Path, action='append', required=True)
    qualification.add_argument('--output', type=Path, required=True)
    restart = sub.add_parser('restart', help='rebuild before holdout with current admission checks and exact saved exchanges')
    restart.add_argument('--from-run', type=Path, required=True)
    restart.add_argument('--run', type=Path, required=True)
    restart.add_argument('--recipe', type=Path, required=True)
    restart.add_argument('--prepare-only', action='store_true')
    restart.add_argument('--experimental', action='store_true')
    checkpoints = restart.add_mutually_exclusive_group()
    checkpoints.add_argument('--checkpoint-round', type=int, help='use a verified completed round as the new draft seed; no held-out work may have begun')
    checkpoints.add_argument('--working-checkpoint', action='store_true', help='snapshot a stopped, valid working draft; does not inherit a passing evaluation')
    restart.add_argument('--permit-draft-record', action='append', default=[], help='explicitly authorized supplied record ID to revise as an unapproved draft')
    restart.add_argument('--authorization-file', type=Path, help='local record of the controlling user authorization; required with --permit-draft-record')
    resume = sub.add_parser('resume', help='resume incomplete work using saved worker responses')
    resume.add_argument('--run', type=Path, required=True)
    resume.add_argument('--experimental', action='store_true')
    resume.add_argument('--retry-inflight', action='store_true', help='explicitly retry an interrupted call with unknown outcome, within budget')
    inspect = sub.add_parser('inspect-artifact', help='freeze and directly inspect candidate content, instructions and local delivery')
    inspect.add_argument('--run', type=Path, required=True)
    inspect.add_argument('--project', type=Path, required=True)
    inspect.add_argument('--prepare-only', action='store_true', help='save the check inventory without invoking adapters')
    inspect.add_argument('--experimental', action='store_true', help='explicitly inspect a recipe without an operating envelope')
    inspect.add_argument('--retry-inflight', action='store_true')
    reassess = sub.add_parser('reassess-artifact', help='snapshot an existing candidate in a separate artifact run and preserve cumulative resources')
    reassess.add_argument('--from-run', type=Path, required=True)
    reassess.add_argument('--run', type=Path, required=True)
    reassess.add_argument('--project', type=Path, required=True)
    reassess.add_argument('--recipe', type=Path, required=True)
    reassess.add_argument('--prepare-only', action='store_true')
    reassess.add_argument('--experimental', action='store_true')
    status = sub.add_parser('status', help='show persisted state without calling adapters')
    status.add_argument('--run', type=Path, required=True)
    finalize = sub.add_parser('finalize', help='apply submitted author review, rebuild approved artifacts, or evaluate requested revisions')
    finalize.add_argument('--run', type=Path, required=True)
    finalize.add_argument('--review', type=Path, help='edited author review; defaults to the candidate form')
    comparison = sub.add_parser('comparison-register', help='freeze a complete matrix of fresh artifact trials and cumulative budgets; no calls')
    comparison.add_argument('--definition', type=Path, required=True)
    comparison.add_argument('--output', type=Path, required=True)
    comparison.set_defaults(_handler=command_pipeline)
    for name in ('comparison-run', 'comparison-report'):
        child = sub.add_parser(name, help='execute once within the registered limits' if name.endswith('run') else 'verify and summarize registered model comparisons without calls')
        child.add_argument('--comparison', type=Path, required=True)
        child.add_argument('--output', type=Path, required=True)
        child.set_defaults(_handler=command_pipeline)
    for command in (recipe, start, restart, resume, status, finalize, plan, registration, qualification, inspect, reassess):
        command.set_defaults(_handler=command_pipeline)


def command_pipeline(args: argparse.Namespace) -> int:
    if args.pipeline_command == 'delivery-recipe':
        from .delivery import recipe
        if args.output.exists():
            raise ReadingPackError('refusing to overwrite delivery recipe')
        value = recipe([args.generator], [args.evaluator], generator_model=args.generator_model,
            evaluator_model=args.evaluator_model, max_cost_usd=args.max_cost_usd,
            call_allowance_usd=args.call_allowance_usd, max_wall_seconds=args.max_wall_seconds,
            timeout_seconds=args.timeout_seconds, prior_cost_usd=args.prior_cost_usd,
            cumulative_cost_limit_usd=args.cumulative_cost_limit_usd, language=args.language, scope=args.scope,
            global_call_allowance_usd=args.global_call_allowance_usd,
            evaluator_timeout_seconds=args.evaluator_timeout_seconds, global_timeout_seconds=args.global_timeout_seconds)
        write_json(args.output, value)
        print(f'created {args.output}')
        return 0
    if args.pipeline_command == 'deliver':
        from .delivery import prepare, prepare_successor, run, status
        if args.predecessor is not None:
            if args.source is not None or args.recipe is None:
                raise ReadingPackError('a successor delivery takes --predecessor and --recipe, not a manuscript')
            if args.title or args.author or args.format or args.chapter_level or args.seed or args.chapter_map or args.seed_policy != 'preserve':
                raise ReadingPackError('a successor delivery inherits manuscript, structure and seed from its predecessor')
            result = prepare_successor(args.run, args.predecessor, _read(args.recipe), output=args.output)
        elif args.source is not None:
            if args.recipe is None:
                raise ReadingPackError('new delivery requires --recipe')
            result = prepare(args.run, args.source, _read(args.recipe), title=args.title, author=args.author,
                             source_format=args.format, chapter_level=args.chapter_level, seed=args.seed,
                             chapter_map=_read(args.chapter_map) if args.chapter_map else None,
                             seed_policy=args.seed_policy, output=args.output)
        else:
            if (args.recipe or args.title or args.author or args.format or args.chapter_level or args.seed
                    or args.chapter_map or args.seed_policy != 'preserve' or args.output):
                raise ReadingPackError('resume uses frozen delivery settings; do not supply replacements')
            result = status(args.run)
        if not args.prepare_only:
            result = run(args.run)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result['state'] in {'prepared', 'delivered', 'delivered_partial'} else 1
    if args.pipeline_command == 'export':
        from .delivery import export as export_delivery
        result = export_delivery(args.run, args.output)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.pipeline_command in {'resume', 'status', 'plan'} and (args.run / 'delivery-plan.json').exists():
        from .delivery import run, status
        if getattr(args, 'retry_inflight', False):
            raise ReadingPackError('report-only deliveries do not automatically retry started calls')
        result = run(args.run) if args.pipeline_command == 'resume' else status(args.run)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result['state'] in {'prepared', 'running', 'delivered', 'delivered_partial'} else 1
    if args.pipeline_command.startswith('comparison-'):
        from .pipeline_comparison import register_comparison, run_comparison, report_comparison
        if args.output.exists():
            raise ReadingPackError('refusing to overwrite comparison output')
        if args.pipeline_command == 'comparison-register':
            result = register_comparison(_read(args.definition),args.output)
        else:
            operation = run_comparison if args.pipeline_command == 'comparison-run' else report_comparison
            result = operation(args.comparison)
            _seal(args.output,result)
        print(json.dumps({k:v for k,v in result.items() if k not in {'definition','trials'}},ensure_ascii=False,indent=2))
        # A completed comparison may contain failed Packs; inspect acceptance separately.
        return 0 if result['admitted'] else 1
    if args.pipeline_command == 'reassess-artifact':
        from .pipeline_acceptance_reassessment import reassess_artifact
        recipe = _read(args.recipe)
        if not recipe.get('operating_envelope') and not args.experimental:
            raise ReadingPackError('un-enveloped reassessment requires --experimental')
        result = reassess_artifact(args.from_run, args.run, args.project, recipe)
        if not args.prepare_only:
            result = resume_pipeline(args.run)
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 0 if result['state'] in {'ready','awaiting_author_approval','needs_author_decision','artifact_completed'} else 1
    if args.pipeline_command == 'inspect-artifact':
        from .pipeline_acceptance import inspect_artifact
        result = inspect_artifact(args.run, args.project, prepare_only=args.prepare_only,
                                  experimental=args.experimental, retry_inflight=args.retry_inflight)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        # 0 means preparation/inspection finished, never overall Pack acceptance.
        return 0 if result['execution']['status'] in {'ready', 'completed'} else 1
    if args.pipeline_command == 'recipe':
        if args.output.exists():
            raise ReadingPackError('refusing to overwrite recipe')
        if not args.operating_mode and not args.experimental:
            raise ReadingPackError('choose an operating mode, or explicitly use --experimental; the legacy workflow is not qualified production')
        if args.operating_mode and args.experimental:
            raise ReadingPackError('choose one operating mode or --experimental')
        artifact = args.contract_version == 'artifact-acceptance-1'
        if (not args.reader or not args.reader_model) and not artifact:
            raise ReadingPackError('legacy contract requires reader command and model')
        if getattr(args,'repair_rounds',None) is not None and not artifact:
            raise ReadingPackError('--repair-rounds requires artifact-acceptance-1')
        value = default_recipe([args.generator], [args.judge], [args.reader or args.judge],
                               generator_model=args.generator_model, judge_model=args.judge_model,
                               reader_model=args.reader_model or args.judge_model)
        if args.contract_version:
            value['contract_version'] = args.contract_version
            if args.contract_version == 'artifact-acceptance-1':
                value.update(max_rounds=2,max_attempts=1,profile='general-navigation')
        if args.operating_mode:
            if args.max_cost_usd is None or args.call_allowance_usd is None or args.phase_calls is None:
                raise ReadingPackError('operating mode requires explicit monetary allowance and phase allocations')
            try:
                caps = json.loads(args.phase_calls)
            except ValueError as exc:
                raise ReadingPackError('phase allocations must be a JSON object') from exc
            from .pipeline_resources import PHASES
            if not isinstance(caps,dict) or set(caps)!=set(PHASES) or any(type(v) is not int or v<0 for v in caps.values()):
                raise ReadingPackError('phase allocations must assign a nonnegative integer to every phase')
            value.update(max_rounds=2, max_attempts=1, answer_repetitions=1, evaluation_batch_size=4,
                         generation_chunk_characters=50000, chunk_characters=50000,
                         max_generation_candidates=8, benchmark_chunk_limit=6,
                         max_calls=sum(caps.values()))
            value['operating_envelope'] = {'mode':args.operating_mode, 'max_wall_seconds':args.max_wall_seconds,
                'local_reserve_seconds':120, 'max_cost_usd':args.max_cost_usd,
                'call_allowance_usd':args.call_allowance_usd, 'phase_call_limits':caps}
        if getattr(args,'repair_rounds',None) is not None:
            value['max_rounds'] = args.repair_rounds + 1
        if getattr(args,'scope',None) is not None:
            value['scope'] = args.scope
        from reading_pack.schema_validation import require_structure
        require_structure('pipeline-recipe.schema.json',value,label='pipeline recipe')
        write_json(args.output, value)
        print(f'created {args.output}')
        return 0
    if args.pipeline_command == 'plan':
        from .pipeline_resources import operating_plan, populated_seed
        layout = args.run / 'source-layout.json'
        result = operating_plan(_unseal(args.run / 'manifest.json'), _unseal(args.run / 'chunks.json')['chunks'],
            layout_chapters=len(_unseal(layout)['chapters']) if layout.exists() else 0,
            seed_populated=populated_seed(args.run,_unseal(args.run / 'manifest.json')))
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 0 if result['admitted'] else 1
    if args.pipeline_command == 'qualification-register':
        from .pipeline_qualification import register_trial
        result = register_trial(args.run,args.suite,args.case,args.repetition)
        print(json.dumps({k:v for k,v in result.items() if k!='suite'},indent=2))
        return 0
    if args.pipeline_command == 'qualification-report':
        from .pipeline_qualification import assess_qualification
        if args.output.exists():
            raise ReadingPackError('refusing to overwrite qualification report')
        result = assess_qualification(_read(args.suite),args.run)
        _seal(args.output,result)
        print(json.dumps({k:v for k,v in result.items() if k not in {'suite','evidence_sha256','trials'}},indent=2))
        return 0 if result.get('kind') == 'artifact-workflow-observation' or result['qualified'] else 1
    if args.pipeline_command == 'start':
        recipe_value=_read(args.recipe)
        if not recipe_value.get('operating_envelope') and not args.experimental:
            raise ReadingPackError('legacy recipe requires --experimental; use a measured production recipe for predictable delivery')
        supplements = []
        for role, path in args.supplement:
            if role not in SOURCE_ROLES - {'primary-book'}:
                raise ReadingPackError('invalid supplementary source role', 2)
            supplements.append((Path(path), role))
        result = start_pipeline(args.run, args.source, recipe_value,
                                supplements=supplements, project=args.project,
                                title=args.title, author=args.author, source_format=args.format)
        if args.reuse_preparation_from:
            import_preparation(args.reuse_preparation_from, args.run)
        if args.qualification:
            from .pipeline_qualification import attach_qualification
            attach_qualification(args.run,args.qualification)
        if not args.prepare_only:
            result = resume_pipeline(args.run)
    elif args.pipeline_command == 'restart':
        if not _read(args.recipe).get('operating_envelope') and not args.experimental:
            raise ReadingPackError('legacy restart requires --experimental')
        restart_pipeline(args.from_run, args.run, _read(args.recipe), checkpoint_round=args.checkpoint_round, working_checkpoint=args.working_checkpoint, permit_draft_records=args.permit_draft_record, authorization_file=args.authorization_file)
        result = pipeline_status(args.run) if args.prepare_only else resume_pipeline(args.run)
    elif args.pipeline_command == 'finalize':
        result = finalize_pipeline(args.run, args.review)
    elif args.pipeline_command == 'resume':
        if not _unseal(args.run / 'manifest.json')['recipe'].get('operating_envelope') and not args.experimental:
            raise ReadingPackError('legacy resume requires --experimental')
        result = resume_pipeline(args.run, retry_inflight=args.retry_inflight)
    else:
        result = pipeline_status(args.run)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['state'] in {'ready', 'awaiting_author_approval', 'needs_author_decision', 'artifact_completed', 'release_ready'} else 1
