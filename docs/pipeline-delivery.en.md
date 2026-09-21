# Deliver a Pack with quantitative evaluation

The recommended entry for new production is `pipeline deliver`. The `generation-report-1` workflow delivers a generated Pack and quantitative evaluation within fixed resources. The user decides adoption. Low scores or incomplete evaluation never trigger automatic repair, regeneration or rejection of a generated Pack.

## Fresh generation by default

New runs regenerate every content module from the current source: chapter summaries and terms, section overviews, certainty distinctions, propositions, misreadings, book/Pack policies, names, glossary and references. There are no inherited records by default. Empty modules require specific absence reasons; item limits are ceilings, not quotas. All generated records and module absence decisions are evaluated against the chapter. IDs and source locators are assigned locally.

Old data requires both `--seed PROJECT` and `--inherit-seed`; `--seed` alone is rejected before any call. In that explicit inheritance mode, auxiliary modules remain unchanged and are not re-evaluated against the new source. `--seed-policy regenerate` in inheritance mode replaces chapter content only.

A fresh run is `delivered` only after all generation and evaluation jobs complete and required content is present. Incomplete evaluation or content results in `delivered_partial`, retaining the Pack. Ordinal scores never decide acceptance or trigger automatic repair.

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

Direct input is UTF-8 Markdown, Org or text. Prepare verified text from other formats using existing import facilities. Author-provided modules are never imported implicitly: only explicit `--seed PROJECT --inherit-seed` carries them, unchanged, while chapter content is generated (next section).

The controller freezes chapter/section order and source ranges. A single top-level heading with lower headings is ambiguous between a book title and an excerpt chapter: specify `--chapter-level`. Use 1 for a chapter headed by `#` with `##` sections, or 2 when `#` is a book title and `##` denotes chapters. Multiple parent containers are rejected instead of silently dropping parts or back matter. Fenced code headings are ignored. Plain text is treated as a single chapter.

For N chapters, reserve **2N+1** calls: one generation and one source-bound evaluation per chapter, then one whole-Pack consistency/instruction evaluation. Repairs and retries are zero. The whole-Pack call reads the complete Pack and is larger than a chapter call; `global_call_allowance_usd`, `evaluator_timeout_seconds` and `global_timeout_seconds` in the recipe reserve it separately, defaulting to `call_allowance_usd` and `timeout_seconds`. Reservations sum the per-call allowances and timeouts plus the local reserve; keep adapter timeouts below the matching controller timeout. Failed generation leaves its chapter and expected records in report denominators. Other generated chapters are retained as a partial delivery.

Admission derives reservations from the actual outline, per-call timeouts and cost allowance. All planned work must fit before any sending. Carry prior costs with `--prior-cost-usd` and `--cumulative-cost-limit-usd`; the default prior cost is zero, so existing projects must supply their actual prior cost. If a provider exceeds its call allowance, stop additional sends and report the actual excess. Reservations are not invoice guarantees.

Defaults allow 50000 characters per chapter, 128 chapters and 64 sections per chapter. Oversized chapters are rejected before sending, not automatically split into more model calls. Requests exceeding 1 MiB or calls that cannot fit the remaining time are recorded as unperformed. Started, failed and unknown calls are never automatically retried. Deadlines and reservations persist across resumes.

## Seed projects

Fresh runs generate these modules from the new source and report justified absences. Explicit inheritance uses `--seed PROJECT --inherit-seed` and preserves the supplied modules.

```sh
reading-pack pipeline deliver body.md --run private/run --recipe delivery.json \
  --chapter-level 1 --seed path/to/canonical-project --inherit-seed \
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

## Claude structured output transport

The full frozen schema is included in the request body. The native output schema constrains the envelope and requires `result` to be an object, avoiding a second copy of large dynamic constraints. Every response is then checked against the original full schema locally. Only undeclared extra keys can be removed, with their paths recorded; declared values, required fields, types, conditions and identity checks remain strict.

A failed Claude call with unknown cost stops later dispatch. A successor is also refused while a non-completed call has an unresolved outcome or unknown failed-call cost. Recovery of saved outputs and provisional cost reserves require explicit evidence; unknown calls are not automatically resent.

## Deterministic corrections and evaluation replay

New deliveries accept `--mechanical-inputs inputs.json`. The input must bind the SHA-256 of the **normalized `source.txt`**, not the PDF or raw Markdown bytes:

```json
{
  "source_sha256": "<64 lowercase hex characters>",
  "section_pages": [{"section_id": "S01-01", "printed_page": 10}],
  "names": [{"entity_id": "PERSON-1", "canonical": "Confirmed name", "aliases": ["Confirmed variant"]}]
}
```

Use the same normalization as `reading_pack_producer.candidates._source_text_snapshot` when preparing this input. Both arrays are optional. Supply a reviewed page map and confirmed identity aliases; spelling similarity is never identity evidence. The controller rejects a different source hash, unknown/duplicate sections, absent canonical names and conflicting aliases before dispatch. Mechanical inputs are frozen in the plan and cannot be substituted on resume or a successor. They are supported for fresh generation, not inherited seed records.

Section start pages are embedded in the Pack, so the whole-Pack evaluator sees the same navigation data as a reader. A start page is not an individual record's exact page. When pages are absent, R7 asks for chapter/section navigation and forbids inferring pages. Name display values are normalized by explicit aliases **before chapter evaluation**; original responses stay untouched, original spellings remain aliases, and every change is reported. Similar names without a confirmed mapping are reported as unconfirmed candidates only.

Evidence diagnostics preserve the model's original quote and exact-match result. A unique whitespace-equivalent match **inside the declared source range** yields an `effective_quote` copied from the original and exact start/end offsets. Ambiguous matches, matches elsewhere and other differences are reported without replacement. This changes neither a content judgment nor the original exchange.

When a CLI budget stop or timeout has already recorded exactly one complete, identity-bound, schema-valid StructuredOutput, the adapter can recover that result without changing values or making another call. The audit and receipt distinguish recovered output from native success. Missing, ambiguous, rejected or malformed output is not repaired automatically. Unknown CLI cost remains unknown and stops subsequent dispatch; a per-call cost overrun also stops subsequent dispatch. The provider's CLI allowance is not a guaranteed hard billing ceiling.

An evaluation-only successor copies and hash-binds the predecessor's Pack and project; it does not render a new date or silently apply new templates. Failed generation successors still rebuild the artifact and reevaluate the global instructions.

`resources.cost_accounting` reports the entire successor chain: known reported cost, unknown calls, provisional reserve, call count, the declared cost before the chain, and any extra budget carry-in. Completed exchanges carried between runs are not counted twice; failed calls are counted. `delivery-recipe --unknown-call-reserve-usd N` sets an explicit provisional reserve per unknown call; the default is that call's allowance. Neither reserve is a bill or a proven bound on an unknown charge. Successor preparation raises an understated `prior_cost_usd` to cover the chain and preserves any larger caller-provided budget carry-in; it rejects an insufficient cumulative cap before sending. The original pre-chain amount remains a declared input, not independently verified billing data.

These mechanisms do not approve draft policies, promote review states, repair semantic content, decide acceptance or trigger publication. Existing frozen runs and past deliveries are not rewritten by a toolkit update.

## Optional single repair round

`pipeline delivery-recipe --repair-rounds 1` explicitly enables a bounded second pass for **fresh** deliveries. The default remains `0`, which only produces the Pack and evaluation reports. The generator also acts as repairer in separate calls; the evaluator reassesses the result. `--repair-call-allowance-usd` optionally sets a repair-call allowance. For N chapters, preparation reserves at most **4N+2 calls**: N generation, N initial chapter evaluations, one initial global evaluation, up to N chapter repairs, up to N chapter reevaluations, and one final global evaluation. Skipped work is not sent. There is no second repair round or automatic quality gate.

Initial generation and all initial evaluations must complete before repair starts. The repairer receives the entire chapter source, initial generated records, the allowed edit schemas, and identified findings from chapter/global evaluation and quote checks. Each finding gets a changed/no-change/out-of-scope disposition. Findings are fallible: the prompt explicitly permits rejecting an incorrect criticism and prohibits treating an unapproved draft as a content defect.

Changes are addressed to a chapter summary, terms, section overview, or auxiliary item. A reported omission or classification can also justify adding/removing an auxiliary item. Each change must cite a linked finding, an allowed target, and an exact source quotation in a declared chapter section. The controller applies a chapter's changes atomically, then validates the complete generation schema. A bad quote, out-of-scope edit, inconsistent disposition, or invalid replacement rejects that chapter's patch; it is reported, never silently applied or retried. This structural/source-anchor validation does not prove the revised content is semantically correct.

SYS instructions, rights, approval states and release metadata are outside the editable targets. Global criticisms about those fields remain visible as out-of-scope findings; this round does not rewrite tool rules or grant approval. Changed chapters receive new source evaluations; if the rendered Pack changes, the whole-Pack evaluation is repeated. Missing final evaluations stay missing, never replaced by the first-pass scores. Unchanged content retains its hash-bound original evaluation. Negative final scores do not trigger another repair, acceptance or publication.

Exports include `first-pass-reading-pack.<lang>.md`, `first-pass-pack.<lang>.json`, `first-pass-quality-report.json`, and `repair-report.json`, in addition to the final Pack/reports. Repair changes and dispositions are retained alongside the initial and final evaluations. The internal `repaired-generation.json` stays in the private run. An explicit successor retains the repair-round policy, copies the initial snapshot and completed exchanges, and retries only incomplete/rejected work under a new frozen cost reservation. It never reopens a finished repair round just because the final score is low.

Explicit partial repair may set `repair_available_chapters: true` in the recipe. Only chapters with completed generation and initial source evaluation are repaired. Missing chapters stay in the original scope and denominators; delivery and overall repair remain incomplete. The default is false, requiring all initial chapters before repair.

An array wrapped only as `{"items": [...]}` may be recovered without changing any element, order, or judgment. Extra wrapper keys, invalid elements, identity errors, or failed executions prevent recovery. Full-schema validation is mandatory; normalized paths and the original schema failure remain in the audit.

A global chapter finding may target its auxiliary items; a record finding remains record-scoped. Partial reassessment freezes the initial artifact and ungenerated chapters rather than retrying them. Partial Packs state generated coverage and missing chapters.

Array recovery also permits objects containing only contiguous zero-based numeric keys. Numeric order is preserved; gaps, extra keys, or invalid elements are rejected.
