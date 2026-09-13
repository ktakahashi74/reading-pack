# Direct inspection of candidate content

Compare the content included in a Reading Pack with its frozen sources. M4 implements the inventory of required checks and their completion ledger. M5 connects direct content inspection. The candidate stays unchanged; the run stores the inventory, source scope, results and evidence.

This entry is specific to `artifact-acceptance-1`. The [full workflow](pipeline-artifact-workflow.en.md) connects content, instruction and delivery inspection, repair, final records and author packets. Old verdicts are preserved; reader answers do not determine artifact acceptance.

## 1. Preparation and execution

Create an artifact-contract run with `pipeline start --prepare-only`. See [contract selection](pipeline-contract-selection.en.md) and [automatic production](automatic-pipeline.en.md) for recipes, source inputs and candidate projects. The candidate profile must match the frozen recipe. `auto` is unsupported here.

Save the check inventory without calling a model:

```sh
reading-pack pipeline inspect-artifact --run private/run --project draft-project --prepare-only
```

Semantic inspection uses the configured judge adapter. For a recipe without an operating envelope, explicitly select experimental execution:

```sh
reading-pack pipeline inspect-artifact --run private/run --project draft-project --experimental
```

Execution retains the frozen operating envelope, phase allocations and cumulative deadline. Recipes without an envelope require explicit `--experimental`. See the [resource and production contract](pipeline-artifact-workflow.en.md).

Exit code 0 means preparation or scheduled inspection finished, not that the Pack passed. Read `acceptance.status` separately from `execution.status` in the returned JSON.

## 2. Inventory and evidence

Fixed IDs cover all included candidate items, source-derived chapter-orientation requirements and consistency between items. Structure, metadata, required modules, source locations and length have mechanical checks. Common instructions, book differences and selected local delivery artifacts are also fixed inspection targets.

Semantic checks cover source fidelity, including numbers, comparisons, attribution and quotations; material conditions, exceptions and uncertainty; orientation to each chapter's main arguments; and global consistency. They receive source context rather than only a short quotation selected by the generator. Manuscript and author-supplement roles remain distinct. A supplement is neither an automatic manuscript correction nor author approval.

Responses explicitly identify each `check_id`. Omitted IDs remain pending. Evidence selects span IDs from the particular request; the controller checks source hashes, positions and exact quotations. Unknown IDs, out-of-scope quotations and duplicate results are rejected. A defect needs a target or frozen requirement, a criterion, source or structural evidence and a reader impact.

Chapter structure comes from a saved source outline or deterministic Markdown/Org heading extraction. Other formats without a trustworthy outline leave chapter orientation incomplete. Global consistency conservatively reviews all included content and all registered sources together; large sources can therefore leave this check incomplete at the context limit.

If required source context cannot fit the fixed bound, the check remains incomplete instead of silently truncating the source. Unverified locations and insufficient structure information also remain unresolved. Absence from an excerpt does not establish fabrication. Exhaustive incidental detail and perfect answers to generated questions do not become new inclusion obligations.

Semantic model judgments can miss errors. Successful hash, position and quotation checks do not prove sound inference or the quality of an entire real book.

## 3. Persistence, resumption and four states

Artifacts live under `acceptance/inspections/<inspection_id>/` inside the run:

- `candidate/`: an unchanged candidate snapshot.
- `inventory.json` and `plan.json`: targets, check IDs, source ranges and hash bindings to the candidate, sources and settings.
- `evidence/`: per-inspection results, with semantic results also bound to saved worker requests and responses.
- `reports/`: append-only ledger snapshots and inspection reports referring to previous reports and evidence.

Repeating the same candidate and settings reuses verified results and saved worker responses. Completed checks are not called again, and omitted results are not invented. A changed candidate creates a different `inspection_id`; old candidates, reports and author decisions stay intact. Removed or modified evidence prevents presenting an old result as current acceptance.

Acceptance precedence is: confirmed defects produce `fail`; untouched inspection is `not_run`; unfinished checks or unresolved suspicions produce `inconclusive`; all required checks complete without defects or suspicions produce `pass`. Defects and unfinished scope remain visible together. Instruction and delivery checks must also complete before an overall `pass`.

Detailed inspection reports remain separate from final M1 records. The [production workflow](pipeline-artifact-workflow.en.md) connects delivery bindings, repair histories and author forms. Standalone inspection leaves model diagnostics `not_run` and author approval `not_requested`.
