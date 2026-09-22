# Predictable production and qualification

For new generation with evaluation delivery, use [Pack delivery and quality reporting](pipeline-delivery.en.md). Scores do not decide adoption or trigger repairs. The older acceptance/approval workflows on this page remain separate.

For generator selection and measurement of completion within limits under the artifact contract, see [model comparison](pipeline-model-comparison.en.md). Compare finite trials without adding stages until they pass.

This guide describes `legacy-reader-evaluation-1`. For the current direct-inspection contract, see [artifact production](pipeline-artifact-workflow.en.md); its acceptance does not require reader-answer success or a workflow certificate.

Fresh runs freeze a reader-utility contract before questions or answers. Generated questions no longer create mandatory Pack content. Central meanings, material qualifications and correct attribution remain essential; incidental numbers may be handled by an honest limitation and a specific relevant location already present in the Pack. Generic deflection and false statements still fail. Existing studies retain their original scope and results on restart; changing scope requires a separately identified reassessment, without implying that the candidate must be regenerated.

A workflow is ready for routine use only when it has repeatedly delivered useful, source-faithful Packs within a declared time and cost allowance. Merely stopping after a timeout is not successful production. Version 0.8.0 remains an unqualified alpha: the repository contains control tests, not a measured certificate for real books.

## One operating contract per run

A recipe with `operating_envelope` fixes the total elapsed-time limit, per-call timeout, monetary allowance and call allocation for five phases: preparation, generation, development evaluation, repair with reevaluation, and final evaluation. Input preparation has a local time allowance; its measured duration is included when execution starts. Waiting between `--prepare-only` and the first execution is excluded. Once execution starts, pauses and resumes consume the original deadline.

Before sending, `pipeline plan` checks input size and source roles, local PDF recovery needs, mandatory calls, the cost allocations and the deadline. It reserves the final evaluation before generation begins. A repair allocation must at least cover one generation, its independent review and full development reevaluation. Minimum call counts do not include every dispute, split review or rejected question: phase ceilings bound those branches, and measured qualification establishes whether they are sufficient in practice.

The controlled workflow permits at most two candidate rounds (initial generation plus one repair) and one transport attempt per job. Generation windows may be as large as 50,000 characters; this is a processing window, not permission to copy that much source text. `benchmark_chunk_limit` selects a deterministic bounded sample for questions, retaining every supplied source. Source-fidelity auditing still visits every source chunk. This selection is fixed before answers; it does not replace or relax questions in an existing run.

Resuming or restarting carries the same deadline and cumulative reservations. A restart transfers the allowance to one successor; the parent and a second branch cannot spend it again. Failure and unknown outcomes consume their full call allowance. Neither unused final slots nor apparently cheap earlier calls enlarge another phase. Author-requested content changes after the final test become `revision_requested`; the completed run records the request and does not automatically open another budget. Ordinary author approval still builds local release artifacts.

## Cost meaning

The controller bounds reserved USD allowances and sends the per-call allowance to the included Claude adapter. The adapter passes it to the provider budget option. CLI-reported cost, allowance and an actual invoice are different quantities. A reported allowance overshoot stops execution and fails qualification. An unknown cost is never zero or evidence of meeting the monetary target. This implementation cannot guarantee that a provider will never bill above its requested allowance. A strict invoice ceiling additionally needs a provider-side mechanism with that guarantee.

## Prepare and inspect without sending

These numbers demonstrate configuration for a small supported input; they are not a recommended model budget or evidence of a two-hour completion rate. Choose provider settings from measurements and freeze them before testing.

```sh
reading-pack pipeline recipe --output qualification-recipe.json \
  --generator /path/to/generator --generator-model generator-model-id \
  --judge /path/to/judge --judge-model judge-model-id \
  --reader /path/to/reader --reader-model reader-model-id \
  --operating-mode qualification --max-wall-seconds 7200 \
  --max-cost-usd 35 --call-allowance-usd 1 \
  --phase-calls '{"preparation":8,"generation":6,"development":8,"repair":10,"final":3}'
reading-pack pipeline start book.md --run private/trial-1 \
  --recipe qualification-recipe.json --prepare-only
reading-pack pipeline plan --run private/trial-1
```

Add `--supplement author-data appendix.md` when the author supplies additional material. Its role and exact bytes remain part of the input. A populated canonical seed and manuscript-only generation are different measured input modes. A certificate for a small seeded Markdown example does not qualify a large raw PDF.

`admitted` in the plan means the declared allocations clear local feasibility checks. It does not mean quality is qualified. `status` reports the absolute deadline, elapsed time, reservations, known reported cost and unknown-cost calls. `input_outside_envelope`, `deadline_exceeded`, `phase_budget_exhausted`, `cost_allowance_exceeded` and `workflow_unqualified` remain distinct from quality failure and successful author handoff.

## Freeze a finite qualification study

Configure the service once; the book author need not run this study for every book. Before any trial sends, define representative clean inputs and defective controls. Use at least two distinct books, two fresh repetitions per case and three materially different defect classes, such as incorrect attribution, a missing essential condition and an invented claim. Set the required observed completion rate in advance, normally `1.0` for an initial small qualification study. Select input formats, languages, sizes, profiles and source-role combinations representative of the proposed service. These minimums are an initial empirical protocol, not a statistical guarantee for arbitrary books.

Prepare all run directories, then create a private JSON suite with these exact fields:

- `schema_version`: `1`.
- `workflow_signature`: `workflow_signature(manifest)` from `reading_pack_producer.pipeline_qualification`.
- `repetitions`, `minimum_distinct_books`, `minimum_defect_classes`, `minimum_completion_rate`.
- `cases`: objects with `id`, `book_id`, `input_identity` (from the module's helper), `expectation` (`complete` or `detect-defect`), `defect_class` and `record_ids`. Clean cases use an empty class and list. Defect controls name the expected finding criterion/category and affected canonical IDs.

Register each case and repetition before execution. Use separate provider audit directories for fresh trials; exact cached responses are not new measurements. Sum all planned run allowances and deadlines before authorizing the study. Do not add trials, change models, move thresholds or extend budgets to rescue a failed study.

```sh
reading-pack pipeline qualification-register --run private/trial-1 \
  --suite private/suite.json --case body-only --repetition 0
reading-pack pipeline resume --run private/trial-1
# Repeat for every preregistered case and repetition, including failures.
reading-pack pipeline qualification-report --suite private/suite.json \
  --run private/trial-1 --run private/trial-2 --output private/qualification.json
```

The reporting command makes no model calls. Supply the entire study, not just passing runs. It checks immutable inputs, recipe/engine/model/adapter identities, fresh usage receipts, elapsed time, actual reported cost and final candidate integrity. A clean case passes only after its held-out test and author handoff. A negative control passes only if the specified defect and records were detected; provider failure does not count as detection. Missing, duplicate, reused, changed and unknown-cost trials cannot qualify a workflow.

The report includes observed completion and defect-detection rates, elapsed-time percentiles and maximum reported cost. Its optional Wilson value assumes independent trials; repetitions on the same books are correlated, so that value is descriptive and is never an admission condition. Preserve qualification runs as frozen evidence; author changes and release work belong in separate production runs. Synthetic test success is not a real-model qualification certificate.

## Use the measured recipe

For production, change only `operating_envelope.mode` from `qualification` to `production` and the per-run `--audit-dir` values. Attach the report with `pipeline start ... --qualification private/qualification.json`. Engine, model, effort, other adapter arguments, quality thresholds and resource settings must match. Inputs beyond measured format, language, supplement roles, seed status or maximum source size stop before generation. An automatically selected profile must also have been measured for that input mode. Full source complexity is not characterized by these checks; success rates apply to the declared study, not every possible book.

No measured certificate ships with the alpha. Production mode therefore stays closed until a real study qualifies. The old research workflow remains available only through explicit `--experimental` on `recipe`, `start`, `resume` and `restart`; its successful control tests must not be described as predictable real-book production.
