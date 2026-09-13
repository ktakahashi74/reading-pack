# Deliver a Pack with quantitative evaluation

The recommended entry for new production is `pipeline deliver`. The `generation-report-1` workflow delivers a generated Pack and quantitative evaluation within fixed resources. The user decides adoption. Low scores or incomplete evaluation never trigger automatic repair, regeneration or rejection of a generated Pack.

## Commands

```sh
reading-pack pipeline delivery-recipe --output delivery.json \
  --generator /absolute/path/to/generator-adapter --generator-model exact-generator-id \
  --evaluator /absolute/path/to/evaluator-adapter --evaluator-model exact-evaluator-id \
  --scope "Chapter 1 only; notes and appendices excluded" --language en \
  --max-cost-usd 3 --call-allowance-usd 1 \
  --max-wall-seconds 1200 --timeout-seconds 300
reading-pack pipeline deliver chapter.md --run private/run --recipe delivery.json \
  --chapter-level 1 --title "Book, chapter 1" --author "Author" \
  --output deliveries/book-ch1 --prepare-only
reading-pack pipeline plan --run private/run
reading-pack pipeline resume --run private/run
reading-pack pipeline status --run private/run
reading-pack pipeline export --run private/run --output deliveries/book-ch1
```

These numbers illustrate reservation for one chapter, not measured model costs or quality guarantees. Configure local JSON adapters, including model identity. Argument arrays are supported in `workers.*.command`. A Claude adapter wrapper or array must specify the CLI hash, model, audit directory, call/cost limits and timeout; allow enough adapter calls for the chapter count and use an adapter timeout shorter than the controller timeout.

Omit `--prepare-only` to execute immediately after freezing inputs. `pipeline deliver --run private/run` also resumes a prepared delivery. Completed runs only return verified status and make no new calls. Frozen inputs, settings, start time and reservations cannot be replaced or reset during resume.

## Outline and bounded work

Direct input is UTF-8 Markdown, Org or text. Prepare verified text from other formats using existing import facilities. Author-provided modules are never imported implicitly: only an explicit `--seed` project carries them, unchanged, while chapter content is generated (next section).

The controller freezes chapter/section order and source ranges. A single top-level heading with lower headings is ambiguous between a book title and an excerpt chapter: specify `--chapter-level`. Use 1 for a chapter headed by `#` with `##` sections, or 2 when `#` is a book title and `##` denotes chapters. Multiple parent containers are rejected instead of silently dropping parts or back matter. Fenced code headings are ignored. Plain text is treated as a single chapter.

For N chapters, reserve **2N+1** calls: one generation and one source-bound evaluation per chapter, then one whole-Pack consistency/instruction evaluation. Repairs and retries are zero. The whole-Pack call reads the complete Pack and is larger than a chapter call; `global_call_allowance_usd`, `evaluator_timeout_seconds` and `global_timeout_seconds` in the recipe reserve it separately, defaulting to `call_allowance_usd` and `timeout_seconds`. Reservations sum the per-call allowances and timeouts plus the local reserve; keep adapter timeouts below the matching controller timeout. Failed generation leaves its chapter and expected records in report denominators. Other generated chapters are retained as a partial delivery.

Admission derives reservations from the actual outline, per-call timeouts and cost allowance. All planned work must fit before any sending. Carry prior costs with `--prior-cost-usd` and `--cumulative-cost-limit-usd`; the default prior cost is zero, so existing projects must supply their actual prior cost. If a provider exceeds its call allowance, stop additional sends and report the actual excess. Reservations are not invoice guarantees.

Defaults allow 50000 characters per chapter, 128 chapters and 64 sections per chapter. Oversized chapters are rejected before sending, not automatically split into more model calls. Requests exceeding 1 MiB or calls that cannot fit the remaining time are recorded as unperformed. Started, failed and unknown calls are never automatically retried. Deadlines and reservations persist across resumes.

## Seed projects

`--seed PROJECT` names an existing Reading Pack project, typically the author-reviewed canonical one. Its certainty scale, canonical claims, misreadings and objections, policies, names, glossary and references are carried into the delivery **unchanged**; only chapter summaries, chapter terms and section overviews are generated. Without a seed those modules are empty, and the quality report's completeness section says so explicitly.

```sh
reading-pack pipeline deliver body.md --run private/run --recipe delivery.json \
  --chapter-level 1 --seed path/to/canonical-project \
  --chapter-map chapter-map.json --prepare-only
```

- Seed chapter records and manuscript chapter headings are bound one-to-one, in order, by exact match ignoring whitespace and compatibility forms. Unmatched chapters are rejected before any send instead of guessed. Differently worded headings are declared with `--chapter-map` (`{"CH-AFTERWORD": "Afterword"}`, seed chapter id to manuscript heading). A different chapter count, or seed section titles that differ from the manuscript's immediate subheadings, is also rejected.
- Sections follow the seed's immediate subheadings; deeper headings are merged into their parent section and the merged count is reported. Chapter and section ids use the seed's numbering, so generated `CH-nn` ids never diverge from the canonical Pack.
- `--seed-policy preserve` (default) keeps seed summaries and terms where present and generates only empty ones; author-reviewed records are left byte-identical, without added locators. `regenerate` replaces summaries and terms and returns those chapter records to `draft`. Section overviews are always added as new `draft` claims; an id clash with a seed claim is rejected.
- The run's `project/` is copied from the seed with `status` reset to `draft`, every `[workflow]` approval reset to `pending`, a `-draft` version suffix and only the delivery language. Seed approvals and publication decisions never transfer to new content. The seed copy under `seed/` is frozen and tamper-checked.
- The completeness section of the quality report lists seed count, output count, lost ids and added ids per module; any loss is reported as a regression. Conflicts between the seed's author-input declarations (for example `claims: provided`) and generated additions are reported as items needing an author decision; declarations are never rewritten. A seed whose recorded source differs from the delivered manuscript is shown as such.
- LLM source evaluation covers only records generated by this delivery; seed records are counted, not re-scored. The whole-Pack evaluation reads the complete rendered Pack.

## Successor runs for incomplete jobs

A frozen run never resends failed, unknown or unperformed jobs. To fill a failed generation or a timed-out evaluation, create a successor run.

```sh
reading-pack pipeline deliver --run private/run-2 --predecessor private/run --recipe delivery-2.json --prepare-only
reading-pack pipeline resume --run private/run-2
```

- The predecessor must be finished (`delivered`, `delivered_partial` or `generation_failed`). Manuscript, outline, seed and title are inherited; the recipe must keep the language, scope and both models. Compare models in a new delivery instead.
- Completed exchanges are copied into `jobs/`, hash-bound and replayed without sending. Only incomplete jobs are executed; if any chapter is regenerated the whole-Pack evaluation is redone because the Pack changes. Reservations cover the redone work only.
- Reported calls and costs are the successor's own; carried jobs and the predecessor path are listed alongside. Carry predecessor costs through `--prior-cost-usd`. The predecessor's artifacts and report are never modified.
- The Claude adapter accepts a final attempt that was rejected only for undeclared keys by pruning those keys; pruned paths are recorded in the audit and the report. Declared values are never changed, and invalid values are never salvaged.

## Deliverables and interpretation

The run directory is a private workspace where the manuscript, exchanges, seed copy and outputs live together. What you hand over is the delivery folder named by `--output` (default: a sibling of `--run` called `<run>-delivery/`), written automatically on completion; `pipeline export --run DIR --output DIR` writes it later from any finished run. The delivery folder holds only the four files below plus `delivery-manifest.json` (per-file hashes, run, models, seed, predecessor, generation and evaluation counts, cost, adoption not decided). Manuscript, `jobs/`, `seed/` and the `project/` tree are withheld. Re-exporting identical content is a no-op; a non-empty folder with different content is refused.

- `reading-pack.<lang>.md`: chapter summaries, chapter/section map, section overviews and reader instructions.
- `quality-report.<lang>.md`: scores, denominators, findings, evidence, missing evaluation, time and cost, and module completeness against the seed (counts, lost/added ids, chapter map).
- `quality-report.json`: separate mechanical results, model judgments, identities and execution evidence.
- `pack.<lang>.json`: the canonical data behind the rendering, including seed modules and generated records, for import into the book project.
- Kept only inside the run: `project/` (a reproducible toolkit project), `source.bin`, `source.txt`, `jobs/`, `seed/` (private original bytes, normalized text, request/response evidence, seed copy).

The controller creates locators as `source.txt#normalized-text:start-end`. A valid range does not establish semantic support. Mechanical results cover outline structure, empty content fields, source ranges, exact quotation matches, format validation, length and delivery bytes. LLM evaluation separately assesses source support and explains its evidence.

Record judgments are `supported / partially_supported / unsupported / unclear`; section coverage is `covered / partial / missing`. Each chapter receives 0–4 scores for source fidelity, coverage, and qualifications/attribution. The whole Pack receives internal-consistency and instruction scores; that task sees only the Pack, while chapter tasks see the corresponding complete source chapter. Scientific truth of the manuscript is not independently reviewed.

The frozen ordinal scale is: 0 largely unmet; 1 multiple major issues; 2 useful core with important issues; 3 broadly met with minor issues; 4 no concrete issue found within the inspected scope. Scores are not correctness probabilities, are not averaged into an overall score or pass, and missing evaluation is never converted to zero.

Invalid or failed evaluator responses retain the Pack. Prepared, delivered and partially delivered runs exit 0; failure to generate any chapter exits 1 as `generation_failed`. Read generation counts and evaluation coverage separately. Status checks detect changed delivered artifacts rather than attributing old scores to new content.

## Existing contracts

`legacy-reader-evaluation-1` and `artifact-acceptance-1` remain available for explicitly frozen workflows. Their results and approvals are preserved, never relabeled as this new contract. Existing production-standard/release conformance requirements remain separate from delivery completion. Delivery does not imply author adoption or publication.
