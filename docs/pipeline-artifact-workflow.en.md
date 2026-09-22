# Direct artifact production and reassessment

For new generation with evaluation delivery, use [Pack delivery and quality reporting](pipeline-delivery.en.md). Scores do not decide adoption or trigger repairs. The older acceptance/approval workflows on this page remain separate.

`artifact-acceptance-1` now runs generation, direct inspection, at most one repair round, full reinspection and a local author packet. The default contract remains `legacy-reader-evaluation-1`. Existing runs keep their original contract, source files, decisions and budgets. This implementation is v0.8.0 alpha.

| Milestone | Implemented boundary |
|---|---|
| M6 | Common instruction version, book differences, actual rendered instructions, prospective draft policies and selected local delivery artifacts |
| M7 | Frozen stage reservations, one repair round and full reinspection within the reserved final phase |
| M8 | Separate reassessment of a saved candidate with immutable origin evidence and cumulative resources |
| M9 | M1 records, author questions and decisions, current-state verification and independent workflow observations |
| M10 | Synthetic failure-path integration, legacy regression, bilingual release checks and package verification |

M10 is software validation. It does not claim live-book acceptance, author adoption, publication or measured model performance.

## New production

Select the contract with `pipeline recipe --contract-version artifact-acceptance-1`. The command supplies a fixed `general-navigation` profile, one repair and one transport attempt; select another supported fixed profile in the recipe when appropriate. `auto` is currently rejected before dispatch. A handwritten recipe must set `max_rounds` to 1 or 2 and `max_attempts` to 1. Existing Pack length, byte, quotation and candidate limits remain unchanged.

```sh
reading-pack pipeline start book.md --run new-run --recipe recipe.json --prepare-only
reading-pack pipeline plan --run new-run
reading-pack pipeline resume --run new-run
reading-pack pipeline status --run new-run
```

Use `--experimental` on start/resume/inspection only when the recipe has no operating envelope. An envelope is mandatory for a declared end-to-end USD/deadline allocation. Never remove an existing envelope to bypass a stop. Supplemental sources use the existing `--supplement ROLE PATH` option and registered roles, such as `author-data`, `author-qa` or `errata`.

The five existing allocation names remain `preparation`, `generation`, `development`, `repair`, `final`. Here, `development` means initial direct inspection and `final` means reinspection. Reader questions, answers and grades have zero standard calls. Generation and repair reserve worst-case candidate review splitting. `artifact_inspection_batch_limit` (default 128) fixes the maximum initial semantic batches and the separate final capacity. `artifact_adjudication_limit` (default 1) bounds additional judgments of concrete unresolved findings per inspection. `instruction_context_characters` (default 200000) bounds the combined instruction/artifact context. These are inspection resource limits, not additional Pack length requirements.

A candidate exceeding the batch or context capacity is incomplete. A second repair never starts. Initial inspection must finish before confirmed defects are collected for repair; preferences and unresolved suspicions cannot trigger repairs. Changes are rechecked across the entire candidate because this implementation does not assume complete dependency analysis. If no editable content defect exists, the workflow produces the final questions without asking the author during production. Instruction/template defects and protected author material remain decisions for the author or developer.

Initial and repaired snapshots and their inventories remain under `acceptance/production/`. Repair plans and before/after canonical changes are separate evidence. Resume reuses completed exchanges and checks their hashes; unknown or failed calls keep reservations. Phase exhaustion preserves capacity belonging to other phases, and the final phase's time is reserved before earlier calls. Provider-reported USD, reservation allowances and invoices remain distinct; missing receipts remain unknown cost.

## Instructions and delivery

Common rendering rules and the default language template have a version and content hashes. The independent instruction inspector receives the actual candidate template, actual rendered file, prospective rendering of draft policies, book-specific policies and registered source roles. It checks the six fixed instruction criteria, including source attribution, material conditions, answer boundaries and retrieval/fallback behavior. A template hash alone cannot complete these checks.

Local distribution is built separately and compared byte-for-byte with the frozen candidate. Missing SYS instructions, missing Pack boundaries, oversized files and damaged or changed outputs are detected. A configured `public_base_url` adds the existing local Web delivery build/reference checks. `route_results` reports those local checks and `live_retrieval` stays `not_run`: local artifact integrity does not prove a deployed server or a specific chat service's one-click retrieval. No deployment is performed.

The normal renderer continues activating only approved policies. The prospective rendering is inspection evidence, not an approved or public artifact. No status is silently promoted to activate draft instructions.

## Existing candidates

```sh
reading-pack pipeline reassess-artifact --from-run old-run --run reassessment \
  --project old-run/working --recipe artifact-recipe.json --prepare-only
reading-pack pipeline resume --run reassessment
```

The project must be a saved candidate inside the origin run. A new run retains a complete origin evidence snapshot and starts at `not_run`. Source identity and saved outlines may be reused; old semantic judgments, question scores and approvals cannot establish new acceptance. The old candidate remains unchanged. `restart` still cannot change contracts.

The operating envelope must match the origin exactly, the cumulative call limit cannot increase, and the original deadline survives. Unknown prior monetary usage cannot be initialized as zero. A single successor marker prevents branching or executing further calls from the predecessor. This conservative entry does not extend expired deadlines. New spending or timing terms require a separately authorized plan, not editing the old ledger. Candidate reassessment is distinct from measuring fresh production from a manuscript.

## Reports and author decisions

The M1 [state contract](reading-pack-artifact-acceptance-contract.ja.md) is unchanged. Its runtime schema is a byte-identical packaged copy, `schema/artifact-acceptance-report.schema.json`. Final append-only records live under `acceptance/records/`; source, candidate, template, criteria, delivery, resource and author evidence live at safe, hash-bound run-relative paths. Source excerpts stay in private run evidence.

`acceptance.status` is `pass`, `fail`, `inconclusive` or `not_run`. Execution, stop reasons, model diagnostics and author decisions remain independent. A stopped inspection can retain confirmed defects. Read-only status checks current evidence; tampering cannot leave a stale `pass` displayed as current. Prior records themselves remain unchanged.

The author packet is a private JSON document linked from `author_approval.review`; it includes the exact candidate manifest, inspection results, unresolved checks, repair changes and a decision format. A passing candidate is `pending`; a failing or inconclusive candidate is `needs_decision`. To record an actual decision, supply a JSON file containing `reviewer`, timezone-qualified `decided_at`, `decision` (`approved`, `rejected` or `changes_requested`), `scope` and the packet's exact `candidate_manifest_sha256`:

```sh
reading-pack pipeline finalize --run new-run --review author-decision.json
```

This imports an explicit decision and appends evidence. Approval does not change a failed quality result, activate draft policies, build an approved release or authorize publication. Changes requested do not open another automatic repair loop. The existing author-review/release tools retain their separate approval requirements.

## Finite workflow observations

The optional qualification commands accept an artifact-specific study with `schema_version: 2`, `contract_version: artifact-acceptance-1`, a fixed `workflow_signature`, positive `repetitions`, and finite `cases`. Cases use the existing identity fields: `id`, `book_id`, `input_identity`, `expectation`, `defect_class`, `record_ids`. Register each trial before its first call. There is no minimum number of books or defect classes for an individual Pack to pass.

The study report includes every registered slot, including missing trials in the completion denominator. A completed candidate requires direct acceptance and an author packet, independently of reader scores. Reassessments cannot be registered as fresh production. Verified provider receipts are required to describe observations as live; synthetic control tests never qualify. The report is an observation, not a production admission certificate, and reports `pack_acceptance_gate: false` and `qualified: false`. No live study is supplied by this implementation.

## Minimal command sequence

This example runs a small manuscript once through configured JSON adapters. Replace paths and model IDs with actual settings. The artifact contract permits omission of `--reader`; the unused internal slot copies the judge configuration. The legacy contract still requires a reader.

```sh
reading-pack pipeline recipe --output artifact-recipe.json \
  --contract-version artifact-acceptance-1 --experimental --repair-rounds 0 \
  --generator /path/to/generator --generator-model exact-generator-id \
  --judge /path/to/judge --judge-model exact-judge-id
reading-pack pipeline start book.md --run private/new-run --recipe artifact-recipe.json --prepare-only --experimental
reading-pack pipeline plan --run private/new-run
reading-pack pipeline resume --run private/new-run --experimental
reading-pack pipeline status --run private/new-run
```

This experimental example has no end-to-end monetary or deadline guarantee. For bounded operation, replace `--experimental` with `--operating-mode qualification` or `production` and supply `--max-cost-usd`, `--call-allowance-usd`, `--max-wall-seconds` and all `--phase-calls` allocations. Reserve the worst-case calls and time required by `plan`. Comparisons use qualification mode.

Author decision JSON has the following shape. Replace illustrative values with the actual author's decision, timestamp and candidate hash from the review packet. An agent must not invent approval.

```json
{
  "reviewer": "Actual author making the decision",
  "decided_at": "2026-09-13T12:00:00+09:00",
  "decision": "approved",
  "scope": "Contents of the candidate identified by the review packet",
  "candidate_manifest_sha256": "The 64-character SHA-256 from the review packet"
}
```

Exit code zero and `artifact_completed` from `pipeline finalize --review` mean the decision was recorded successfully. `needs_author_decision` is also a normal decision-waiting state. Read `acceptance.status` separately for quality; approval does not imply publication or approved artifact generation.

## Model comparison

[Model comparison](pipeline-model-comparison.en.md) optionally varies generator models with fixed inputs and workflow. It adds no trial-count requirement to routine production.

## Manuscript coverage

Freeze the scope with `pipeline recipe --scope "Chapter 3 only; notes and appendices excluded"`. The value passes unchanged into the candidate quality plan and rendered META. When omitted for the artifact contract, the default is "Supplied manuscript only; completeness against the published edition is unverified". Do not infer a complete published edition from the supplied file; explicitly declare verified full-edition scope when appropriate. Existing seeds retain their scope, and a conflicting recipe scope is rejected. The legacy omitted-scope behavior and saved candidates and judgments remain unchanged.
