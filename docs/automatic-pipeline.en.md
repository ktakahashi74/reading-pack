# Automatic production pipeline

For new generation with evaluation delivery, use [Pack delivery and quality reporting](pipeline-delivery.en.md). Scores do not decide adoption or trigger repairs. The older acceptance/approval workflows on this page remain separate.

`artifact-acceptance-1` runs direct content, instruction and local delivery checks, fixed resource limits, at most one repair, separate candidate reassessment and a hash-bound author packet. Standard question generation, reader answers and grading make zero calls. Omission preserves `legacy-reader-evaluation-1`. See [Direct artifact production](pipeline-artifact-workflow.en.md) and [Model comparison](pipeline-model-comparison.en.md). Comparison is optional and is not an individual Pack acceptance gate.

Automatic production using questions and holdout below describes the legacy contract. Individual production and review commands remain available.

This guide describes `legacy-reader-evaluation-1`. For the current direct-inspection contract, see [artifact production](pipeline-artifact-workflow.en.md); its acceptance does not require reader-answer success or a workflow certificate.

Fresh runs freeze a reader-utility contract before questions or answers. Generated questions no longer create mandatory Pack content. Central meanings, material qualifications and correct attribution remain essential; incidental numbers may be handled by an honest limitation and a specific relevant location already present in the Pack. Generic deflection and false statements still fail. Existing studies retain their original scope and results on restart; changing scope requires a separately identified reassessment, without implying that the candidate must be regenerated.

The automatic workflow is an unqualified alpha. Legacy-contract production mode requires a measured workflow certificate and an end-to-end time/cost envelope; legacy research commands require explicit `--experimental`. Control tests do not establish real-book completion. See [predictable production and qualification](predictable-production.en.md) for planning, measurement and current limits.

`reading-pack pipeline` runs a fixed workflow from a manuscript to an author-review form. Additional author materials are optional. The controller owns the order of operations, acceptance rules and stop conditions; configured AI workers supply bounded JSON results. A person need not schedule each generation, grade or repair step.

The normal human gate is the final author review, which includes the applicable rights, publisher and publication decisions. Passing machine evaluation is not author approval. This implementation builds approved artifacts locally; it does not upload or deploy them to a remote service.

## Legacy research setup (unqualified)

The producer remains provider-independent. Configure three trusted local executables implementing the JSON protocol below: generator, judge and reader. Each executable must use its configured model, report the actual model identity, and enforce its provider's output/token settings. Separate roles use separate requests and contexts; they may use the same underlying model. An optional Claude CLI adapter is included; configure its binary, model, authentication and send/cost limits explicitly. The CLI itself and credentials are not bundled.

```sh
reading-pack pipeline recipe --experimental --output recipe.json \
  --generator /path/to/generator --generator-model generator-model-id \
  --judge /path/to/judge --judge-model judge-model-id \
  --reader /path/to/reader --reader-model reader-model-id

reading-pack pipeline start --experimental book.md --run private/book-run --recipe recipe.json

# Optional source roles include author-canon, author-data, author-qa, errata,
# bibliography, publisher-metadata and translation.
reading-pack pipeline start --experimental book.md --run private/book-with-extras --recipe recipe.json \
  --supplement author-canon canon.json --supplement author-data appendix.md

reading-pack pipeline status --run private/book-run
reading-pack pipeline resume --experimental --run private/book-run
```

`--prepare-only` freezes inputs without invoking adapters. `--title`, `--author` and `--format` are optional. Without explicit metadata, title defaults to the filename stem and author to `Unspecified`; these are not inferred authorship claims. Automatic language selection recognizes Japanese kana, otherwise selects English. Set `language` in the recipe when that heuristic is unsuitable.

`--project existing-pack` seeds a private candidate from existing canonical data and source registrations, including previously supplied Author Input Package data. Initial evaluation retains unchanged prose and the original approval history; automatically revised content returns to draft status. The manuscript must match the seed's source hash. A bilingual seed is processed as a private primary-language projection; other language files remain in the input snapshot and are not released by this run. A coordinated bilingual generation/translation transaction is not implemented.

The default profile is `auto`: a separate classification request selects a built-in profile from bounded source samples before testing begins. A seed retains its declared profile. Set an explicit profile in a production recipe to fix it across books. Legacy experimental defaults are four candidate rounds, two stagnant rounds, 1,000 adapter calls, two execution attempts per job, two answers per question, 100,000 Pack characters and 600,000 UTF-8 bytes. Every requirement must pass, and critical errors must be zero. The default proposed license is `All rights reserved`; final author review must still approve the rights and terms. Recipe changes require a new run.

## Vertical PDF structure recovery

Before any model call, local preflight checks that source structure can be extracted and that a seed matches the manuscript. If ordinary vertical-PDF extraction fails, the optional local Poppler `pdftohtml` command recovers chapter openers and section candidates from character positions, font sizes and vertical-column spacing. The pipeline then selects body headings and independently reviews omissions and false positives before creating benchmarks. TOC/body title differences are retained; an outdated TOC never dictates body wording or counts. If the pool omits a heading, review can propose its exact title, physical PDF page and a unique short body quotation. The controller binds it to a verified source position, adds a candidate, and requires a fresh selection and independent review within the same round budget. Text occurrence alone does not establish that a passage is a heading. Unresolved omissions stop before benchmark generation.

`source-preflight.json`, `source-import-plan.json`, `source-layout.json` and `structure-reviews.json` retain source bindings and decisions. Layout evidence includes body text and remains private. Physical PDF page indices are not represented as printed book pages. Ordinary `import-plan` remains body-free and does not adopt unreviewed section candidates. Candidate IDs and source order are enforced by code. These heuristics cover supported layouts, do not implement OCR, and do not guarantee recovery of every PDF. Unsupported or oversized inputs stop explicitly without silent truncation. All selection/review calls share the fixed call budget.

## Fixed execution and stopping

1. Snapshot exact input bytes, source roles, seed data, recipe and implementation identity. Normalize supported manuscript formats locally. Split sources into bounded overlapping chunks; original bytes remain authoritative.
2. Run local source-structure preflight before model calls. Select the profile, bootstrap the project, and independently select/review recovered PDF body headings where needed. Then generate development and held-out questions from each source chunk, independently review the questions, and freeze the suite. The controller retrieves exact evidence by source span ID before semantic review. Rejected questions are revised with the original proposal and explicit defects, then independently reviewed again, for at most `max_rounds` attempts per chunk. Exhaustion ends with `failed_quality`; all pairs must pass before suite freezing.
3. Generate candidates, verify their record schemas and source snippets using the existing candidate pipeline, independently review them, and apply accepted candidates only as drafts in an isolated project.
4. Validate canonical data, profile-required content, character/byte limits and source-derived structure counts. Independently audit source fidelity. Answer fixed reader questions only after these checks pass; a deferred reader test has no scores and cannot count as a pass. Grades must cover every requirement, cite real answer text and provide verified source evidence for findings. An uncertain grade gets one independent adjudication; unresolved uncertainty fails.
5. Classify failures and generate source-bound repairs. Replacements are restricted to the reported record IDs. Candidates that regress on any question/repetition or increase critical errors do not replace the best candidate. Retain all rounds, including failures; stop at the round or stagnation limit.
6. Once development gates pass, run the held-out final test once. Its answers are never supplied to repair workers. Failure stops the run; the same final test is not repeatedly tuned against.
7. Export the author-review form and private tested preview. Draft policies are activated only in an in-memory evaluation projection so their proposed behavior is actually tested. This does not change canonical approval states.

Outcomes distinguish `awaiting_author_approval`, `failed_quality`, `budget_exhausted`, `blocked_execution`, `needs_author_input` and `blocked_source_evidence`. They are not interchangeable. Failed quality is a valid terminal result, not an instruction to lower the criteria. The source-grounding and semantic judges can still be wrong: automated execution is established by synthetic tests, not a guarantee that any particular real book will pass or that AI grading is infallible.

Full Pack size is separate from delivery size. Setting `public_base_url` in the recipe additionally builds and byte-checks the existing web delivery artifacts locally, before author review and again after approval. The book slug is appended to that base URL. Current web profiles require their canonical sections; a minimal book that cannot support them fails delivery preflight rather than receiving fabricated people, glossary or misreading records. Leave this setting empty for the portable Markdown route. These checks do not measure a remote chat product's retrieval compatibility.

## Fixed coverage and source adjudication

Before generation, `audit-contract.json` freezes the profile's required modules and one orientation requirement per chapter, each with a stable ID; reviewed development questions are bound by hash without becoming coverage quotas. Generation, repair and audit use the same contract. A coverage violation must cite an existing requirement ID; the judge cannot add a quota or a new obligation during a repair round. Fidelity, attribution, material qualifications, consistency, actual structure errors and spoiler rules apply throughout the Pack, including statements outside the development questions. Held-out requirements remain hidden from generation, repair and source adjudication.

A concise chapter summary need not cover every paragraph or example. A footnote citation alone does not require a person record, and bibliographic coverage has no per-citation quota. Judges consider qualifications elsewhere in the complete Pack and preserve the roles of author-provided material. Optional detail belongs in `advisories`, which is retained for review without blocking reader tests or initiating repairs. Reader graders also separate optional advice from actual requirement failures and critical errors.

Each source-audit suspicion, including aggregate attribution or invented-record reports, goes to a separate `audit_adjudicate` judge request in batches of at most four. It receives the complete canonical Pack and exact passages retrieved from the frozen source copies using the finding's evidence, normalized-text locators, registered `file#anchor` locators and record anchors. Attribution disputes also retrieve exact entity labels from the frozen manuscript. Literal search counts do not prove semantic absence. Verified adjudication evidence determines repair routing when it differs from an earlier suspicion's source interval. Source roles, hashes and positions are preserved. A group that exceeds the context cap is split locally before any judge call, down to single findings; the cap is never raised. The recipe's `audit_context_characters` defaults to 32,000 and permits at most 64,000 characters per batch; the overall adapter input limit remains 1 MiB. Additional URLs or arbitrary local files are not fetched.

The judge classifies each suspicion as `blocking`, `advisory`, `dismissed` or `unresolved`. A blocking decision needs a named criterion, concrete reader impact, verified evidence and repair scope. Dismissal requires provided source evidence. Unknown evidence IDs and incomplete decision coverage fail closed; omitted retrieval context turns an advisory or dismissal into unresolved evidence. This is independent AI judgment, not a guarantee of semantic correctness. All original findings, decisions, advice and model-estimated structure counts remain in the jobs and `audit-report-N.json`.

Structure totals use the source-bound parsed or independently reviewed heading inventory. The controller compares chapter kind, title, page locator, section titles and order with canonical data. Explicit source-bound title aliases from outline reconciliation remain valid, including in older checkpoints. A model's numeric estimate alone cannot override that inventory. A concrete source-supported missing or incorrect heading remains an audit finding and must be resolved.

Unsettled source evidence gets a bounded requested lookup and rejudgment. Confirmed repairs and positively sourced clarifications of editable records may proceed within the existing round limit. If uncertainty persists when no permitted repair remains or the round limit is reached, the run stops as `blocked_source_evidence` with `unresolved-source-evidence.json`. Unsupported speculation is never a repair. A confirmed defect requiring direct changes to a still-protected author record stops as `needs_author_input` with `author-input-conflicts.json`. These are unsuccessful outcomes; they do not create author approval or expand draft permissions. A related editable clarification can use ordinary bounded repair when it suffices. Existing explicit record permissions continue to apply.

The changed policy and worker protocol require an explicit new run or checkpoint restart. Old findings are not retroactively relabeled, and old failed runs do not acquire a quality pass. Frozen questions, previous costs, source protections and the prohibition on restarting after held-out answers remain in force.

## Author decision and release build

The form is `RUN/candidate/.reading-pack/reviews/pipeline-author.review.md`. The rest of the candidate is immutable. The form includes content, policy questions, rights, publisher review, final signature and publication decision. Do not have a worker fill approval boxes on its own.

```sh
# Run after the author has submitted the edited form.
reading-pack pipeline finalize --run private/book-run
# A separately saved edited copy is also accepted.
reading-pack pipeline finalize --run private/book-run --review signed-review.md
```

A submitted content correction starts a child run under `RUN/revisions/`, with fresh questions, generation/audit jobs and answers, and another author-review form after passing. The old signed form, draft, results and source bytes remain intact. Old quality metrics are never rebound to changed content. Each explicitly submitted revision starts a new recipe budget.

An unchanged, fully signed approval is applied in a separate project. Existing release gates run before generating `RUN/release/dist/`. The state becomes `release_ready`, meaning approved local artifacts, not a deployed site. Subsequent finalization verifies and reuses that release. Human approval cannot compensate for failed machine checks.

## Adapter protocol and recovery

Each adapter receives one JSON request on stdin and returns one JSON response on stdout. The request carries `schema_version`, `stage`, `job`, `recipe_sha256`, `model`, fixed `prompt`, bounded `payload`, `response_schema` and `request_id`. The schema includes the exact stage's result definition. The response is:

```json
{"schema_version":1,"request_id":"<exact request hash>","model":"<actual model id>","result":{}}
```

Fill `result` according to the supplied schema. Stages are `profile`, `structure_select`, `structure_review`, `benchmark`, `benchmark_review`, `generate`, `review`, `audit`, `audit_adjudicate`, `answer`, `grade` and `repair`. Generation requests expose candidate-specific record schemas derived from the admission rules, including ID prefixes, field limits and draft status. Existing author IDs can be retained for a permitted replacement bound to the exact base record; they do not authorize new IDs with the same prefix. Invalid records, out-of-scope source span IDs and unrelated replacement proposals are quarantined individually, with the raw response retained, while valid sibling candidates proceed to independent review. Development and holdout generation each return exactly one question per source chunk, enforced by the response schema. Candidate chapter summaries are additionally limited to 500 characters by admission, even when the recipe permits a larger summary. Reader requests contain only the Pack and question. Generation/repair payloads do not contain held-out questions, requirements or answers. Requests are bounded to the existing adapter input limit of 1 MiB; responses use the recipe's output cap. An adapter is trusted local code, not a sandbox, and can access files or networks under its user's permissions. Context separation is a protocol boundary, not protection against a malicious adapter.

The run directory is private (`0700`). Source bytes, questions, raw responses, evidence and author forms may contain confidential material. Publish only approved distribution artifacts, not the run directory. Executables are invoked directly without a shell and with the existing restricted environment. Configure credentials inside the adapter's existing secure mechanism; inherited arbitrary environment variables are not forwarded.

Calls are reserved durably before dispatch. Completed raw results are saved before schema/model checks and replayed without re-sending. Invalid results are retained and fail closed. Execution errors get bounded retries; an interrupted call with an unknown result requires explicit `resume --retry-inflight`, which spends another reserved call within the same cap. This avoids silently paying for an unknown operation twice. The controller bounds calls, attempts, time and I/O size; it does not measure currency cost or validate a provider's billing report.

Input, toolkit implementation, or configured executable/script-file changes invalidate resumption. Adapter dependency environments remain the adapter operator's responsibility. Source copies, caches and candidate inventories are hash-checked; one process holds the run lock. Candidate snapshots are staged before rename. Checksums detect accidental/cooperative modification, not forged signatures by a hostile local writer. Replaying saved results is reproducible; asking a stochastic model again does not promise identical prose.

Copyright 2026 Koichi Takahashi / 高橋恒一. Licensed under CC BY 4.0.

## Source spans and bounded question repair

Workers return `span_id` from the supplied `evidence_spans`. Each span contains at most 500 characters, the original file hash, document ID, offsets in extracted text and exact text. Source payloads replace `source.text` with the span inventory, sending each passage once. General evidence uses `source_id` plus `span_id`; candidate evidence uses `span_id`. The controller retrieves quotations, avoiding retyping differences in width, spacing or punctuation. PDF heading `evidence_quote` and grader `answer_quotes` remain separate fields with their existing source/answer checks.

Every selected chunk produces one development and one held-out question; legacy recipes select all chunks. Each question has two to four atomic requirements. Numeric instructions are rendered from the actual schema. `max_rounds` bounds each chapter's structure loop, each chunk's benchmark preparation loop, and the Pack candidate loop separately. All calls also consume the shared `max_calls` limit. Original proposals and reviews remain in jobs and `benchmark-reviews.json`. Invalid requirements, unknown spans, duplicates and semantic defects can be revised before freezing; envelope and model-identity errors stop execution. Questions and requirements cannot be changed after seeing reader answers.

## Optional Claude CLI adapter

An installed `reading-pack-claude-adapter`, or `python -m reading_pack_producer.claude_adapter`, can be configured as a worker command. Explicitly provide `--cli`, `--cli-sha256`, `--model`, `--effort`, `--audit-dir`, `--max-calls` and `--budget-usd`. Pin an absolute CLI binary path and SHA256, and configure authentication through the CLI's existing secure mechanism. To share one budget across worker roles, use the same private audit directory. `--call-budget-usd` defaults to 5 and `--timeout-seconds` to 550; the controller timeout must be longer. The CLI itself and credentials are not bundled. Choose model and budget for your production environment:

```text
reading-pack-claude-adapter --cli /absolute/path/to/pinned/claude --cli-sha256 SHA256 --model EXACT_MODEL_ID --effort medium --audit-dir /absolute/private/run/cli-audit --max-calls 100 --budget-usd 40
```

Only the return-only `StructuredOutput` channel is allowed; operational tools and subagents are prohibited. The CLI-facing schema omits the unsupported Draft declaration and translates `dependentRequired` into equivalent `if`/`then` required-field constraints; the adapter and controller independently enforce the original constraints. Up to three native outputs are accepted only when every earlier output was schema-invalid and received its matching CLI error before the next output. A schema-valid judgment cannot be replaced. Semantic benchmark repair belongs to the controller loop, never this transport recovery.

Private audit files retain requests, raw streams, actual models, costs and decisions. An identical valid response is replayed without sending; failed responses are saved and not automatically resampled. Unknown interrupted calls reserve the full per-call allowance, blocking dispatch when the remaining budget is insufficient. Limits use CLI-reported costs and call counts, not a billing guarantee. Changed adapter settings require a new audit directory and a new pipeline run.

## Generation and recovery

Generation uses separate source units of at most 12,000 characters and at most 24 candidates per response by default; review uses batches of eight. Targeted repairs use the original evaluation source units by default; `repair_chunk_characters` can set a separate limit up to 50,000 characters. Candidate and review bounds remain unchanged. Author Input Package modes are enforced before review/application. A populated seed is evaluated first, then repaired only where needed. Candidate record schemas are supplied separately from the transport envelope, so an invalid record is quarantined without discarding valid siblings. Rejected, unapplied proposals remain in the audit report; acceptance depends on the independently checked resulting Pack, not on the number of rejected proposals.

The optional recipe fields `generation_chunk_characters`, `max_generation_candidates`, and `review_batch_size` freeze these bounds. Use `pipeline start --reuse-preparation-from PREVIOUS_RUN` after an engine update to import only identical profile, structure, and benchmark exchanges. Sources, seed, language and chunking must match; each current prompt, payload, model and response schema must match too. Changed exchanges run anew. Reuse is refused once held-out answering has begun. Original requests, costs and job identities remain in the previous run; the new receipt records their origin and prior reserved calls. Reuse never imports generated candidates, reader answers, grades, or author approval.

A worker is asked to stay below half the hard candidate cap, leaving room for ordinary count mistakes without discarding records. Omitted immutable chapter metadata, including an empty page field, is restored from the canonical chapter with an audit record; changed values remain rejected. Audit receives canonical content once rather than also receiving a duplicate rendering. Repair sees only failures supported by its current source unit, plus unresolved global checks.

Set `evaluation_batch_size` to 2–4 to share Pack input across independent per-case grades (default: 1). Every reader answer still has its own request and receives exactly one question plus the Pack. Questions, source evidence and other answers are never shared between reader requests. The judge receives a bounded group of answers, but each grade must use that case’s own evidence IDs; duplicate/missing IDs and borrowed evidence stop the run. Development and held-out cases are never mixed, and repetitions use separate contexts.

Accepted candidates are closed over their references before application: rejecting a proposed certainty record also excludes claims and reader notes that depend on it, while preserving existing valid targets. `pipeline restart --from-run OLD --run NEW --recipe recipe.json` rebuilds after a controller or transport interruption using exact saved exchanges, including candidate generation and independent review. It preserves original candidate creation identity, records restart time separately, and re-runs current source, author-input, dependency and project checks. Changed prompts, schemas, model identities or payloads require new exchanges. Restart is refused once any held-out answering has begun. Previous runs and their costs remain in the audit chain; configure the new adapter with the remaining overall allowance.


## Source outlines and pending revisions

Existing Markdown and Org projects are reconciled against the complete source outline, including nested headings and back matter, while preserving supplied record IDs and prose. Automatic changes to previously reviewed records are recorded as unapproved draft revisions; the original human decisions remain intact. Source and structural checks must pass before reader questions are answered. Repair workers receive source-bound development guidance, while the held-out suite remains unavailable to repairs.

The private `source-text-outline.json` records source offsets and explicit title aliases. `seed-reconciled-import-plan.json` records the resulting import plan. Author-provided structural sets are protected; disagreements remain visible to the source audit rather than being silently removed from expected counts. Empty-summary findings are bound to the corresponding source region when its heading is available.

Optional `draft_revisions` in `author-review-state.json` bind a changed draft to its last applicable human-reviewed content hash and Author Input Package module state. They do not create author decisions or approve content. Unregistered edits, stale origins, and fabricated approved statuses still fail consistency checks. A later real author decision must explicitly review the draft; previous decisions remain unchanged.

Generation and repair inputs omit the prose of protected author records, retaining their IDs and content hashes with an explicit projection notice. This reduces repeated input without changing canonical data. Independent source audits, reader requests, grading, and author review retain the complete Pack content. Repair guidance uses development cases only; deferred tests and failed source checks never satisfy acceptance.

The Claude adapter retains the original request bytes and the exact CLI input separately. Stable Pack context precedes changing question/job identifiers in CLI input, preserving all JSON values and all schema constraints. This permits a common input prefix across reader calls; actual provider cache use and cost are measured in raw audits, not assumed.


## Repair scope

Aggregate source-error summaries do not trigger repairs on unrelated text. Detailed errors retain their source scope, and findings about protected author records can target related editable summaries or glossary/name context. Original author-record protection and acceptance rules remain enforced.

An audit that lists attribution or invented-record errors retains those exact details and its source interval in repair guidance. A source interval is routing metadata, not fabricated quotation evidence. The aggregate suspicion receives independent source adjudication; a confirmed violation blocks acceptance, but does not by itself cause generation on every source chunk. If a finding concerns an immutable author record, the worker receives that affected record as read-only context plus related editable IDs derived from chapter/claim links. Candidate admission still enforces all record and field protections; a contradiction that remains unresolved cannot pass.

### Retaining a stopped draft across an engine update

The default `pipeline restart` rebuilds from the original seed and exact saved exchanges. To retain already generated records instead, select one explicit checkpoint:

```sh
reading-pack pipeline restart --experimental --from-run OLD --run NEW --recipe recipe.json --checkpoint-round 0
# Or retain the current working draft of a stopped run:
reading-pack pipeline restart --experimental --from-run OLD --run NEW --recipe recipe.json --working-checkpoint
```

`--checkpoint-round N` verifies the saved round inventory and rendered Pack hash. `--working-checkpoint` accepts only a run stopped as `blocked_execution`, `failed_quality`, `needs_author_input` or `blocked_source_evidence`; its receipt identifies an unverified working draft. Both options validate the project and source provenance, preserve the original inputs and frozen questions, and record the selected content hash. Initial generation is skipped for this explicit checkpoint; missing summaries or required modules are evaluated and passed to bounded repair. No old quality pass or author approval is assigned to the retained content. Exact matching exchanges may still be reused; changed requests run again. Restart remains forbidden after any held-out answering has begun. The checkpoint retains its round number, so completed matching audits can be replayed and the round limit is not reset. Previous costs remain spent, and the new adapter must use the remaining overall allowance.

When reviewing chapter candidates, the controller identifies structural fields that exactly match the existing chapter: ID, kind, title, pages and sections. These retained values need not all appear in the current generation excerpt. Every other new or changed content field, including summary, terms and spoiler scope, still requires independent source review; an unsupported new statement remains a rejection. A changed structural value receives no such attestation. The complete resulting structure is still checked by the separate source audit.

### Explicit permission to draft a supplied record

A supplied record remains protected by default. When the controlling user explicitly authorizes revisions to identified records, restart from a checkpoint with repeated `--permit-draft-record ID` and `--authorization-file permission.txt`. The file records the actual user authorization; producing this file cannot substitute for receiving that authorization. Use `--prepare-only` to inspect the result before dispatch.

The restart retains the original records and Author Input Package state, records the authorization hash, and grants only unapproved replacements of the selected IDs. It adds no IDs to a `provided` module and changes no human review history. Candidate evidence and independent review remain required. A draft receipt binds each changed record to its prior content and module hash; unregistered changes, stale permissions and manufactured approved statuses fail validation. Supply provenance is retained separately from the new candidate's source locations. A subsequent author decision must explicitly adopt the revised content; the old permission does not authorize further changes to that newly adopted text.

### References in a manuscript without web links

A bibliographic reference needs an ID, a source-supported `label`, and a review status. The `url` field is optional. Put only the author, title, year or other bibliographic details actually supplied by the source in the label; do not invent missing fields or web addresses. A citation records which work the book mentions, not that the work's content has been retrieved. Candidate evidence and independent review are still required.

When a URL is supplied, it must remain an absolute HTTP(S) address. An `official_companion` reference always needs a URL and the existing scope/retrieval declarations. Existing linked-reference output is unchanged. The separate URL/DOI catalog extractor continues to discover links; automatic generation and Author Input Packages can also supply bibliographic references without links.

## Chapter review context

For a candidate that changes chapter content, the reviewer receives neighboring text from the same frozen source document. `chapter_review_context_characters` sets the expansion bound (default 120,000; maximum 150,000 characters). An already larger original chunk is preserved without expansion. The controller checks source hashes, continuity and exact positions before sending spans. This can bridge a chapter across a generation or repair window; it does not guarantee that an arbitrarily long chapter fits. Every new statement still requires independent source review. This neighboring-text expansion stays within one document; linked source passages are retrieved separately as described below. Reader tests still receive only the Pack.

New question sets are reviewed for relevance to a compact reading aid before their requirements are frozen. Existing frozen sets survive restart unchanged. Unresolved audit questions receive one bounded source lookup and rejudgment per round; supported clarifications of editable records can then be reviewed and re-audited alongside confirmed repairs. Uncertainty continues to block acceptance, and the existing round and budget limits still apply. Duplicate observations are grouped without losing their reasons or evidence.

The `audit_lookup` worker selects up to six literal queries against registered frozen source IDs. Retrieved passages share the existing audit context cap; no files or URLs are fetched. A clarification request preserves the suspicion as unresolved until independent review and a complete audit settle the changed record. `resolution_attempts` retains the lookup and judgment; grouped findings retain every original observation. These steps do not grant permission to edit protected records or to approve a Pack.


Candidate review also receives bounded passages from registered sources referenced by the candidate, the affected records and the original findings. The controller preserves source roles and exact spans, including manuscript and supplement passages needed to verify an explicit correction, and supplies explicitly linked Pack records as context. The existing audit context cap applies to retrieval; unrelated files and reader questions are excluded. Only supplied spans may be cited, and unresolved evidence still prevents acceptance.

Grader quotations may omit balanced `**` emphasis markers. The controller restores the exact answer substring and records both versions and positions; wording, punctuation, whitespace, numbers and conditions must still match. This does not alter scores or forgive a missing answer requirement. Other quotation mismatches remain execution errors.


Coverage repair destinations are selected independently of other findings in the same source batch. Existing record IDs explicitly named in a required-coverage finding remain eligible when the author contract permits revision, even when other findings already name different targets. Without a named destination, the existing fallback offers editable records; protected records remain excluded. Candidate rejection reasons from the preceding round are supplied as repair feedback without creating new acceptance requirements or extra rounds. A completed checkpoint retains that feedback under its report hash, so a restart does not repeat a rejected verbatim-copy proposal without its rejection reason.

The adjudicator is explicitly told that `development:<case>:<index>` names a frozen question requirement, not a Pack module or record. The complete canonical records are supplied; coverage is a meaning check across those records. This clarification does not change any frozen requirement.

A previously confirmed or unresolved source finding is not dropped merely because the next broad audit omits it. If the current audit gives no explicit verdict on the same record issue or frozen requirement, the controller requests a focused adjudication against the current complete Pack and frozen evidence. The resulting verdict is retained in `known_findings_rechecked`. Checkpoint restarts carry forward the known findings with their report hashes. This consumes the existing call, context and round budgets and cannot itself grant acceptance.

When a candidate review batch exceeds the existing source-context cap, the controller splits it before dispatch, down to individual candidates. This prevents one candidate from losing its manuscript evidence because another candidate consumes the retrieval budget. The cap is not raised. Missing context for an individual candidate remains explicit and must not be assumed verified.

Coverage repair routing retains the original frozen development requirement evidence as well as the adjudicator's evidence. The passage establishing a defect may differ from a distant footnote or supplement containing the omitted fact; both can route the repair to the necessary source interval. Only explicitly bound development requirement IDs are used, never prose guesses or held-out cases. Source admission, independent candidate review, whole-Pack evaluation, round limits and budgets still apply.
