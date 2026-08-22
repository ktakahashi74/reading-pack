# Production workflow

This public guide applies the [Reading Pack Production Standard](../spec/reading-pack-production-standard.en.md) with the `reading-pack` reference implementation. The completed Markdown format is defined separately by the [Reading Pack Format Specification](../spec/reading-pack-format-spec.en.md). Command names, file names, sessions, and transactions in this guide are toolkit-specific; another implementation need not copy them to claim production conformance.

Each stage has a stable ID, actor, output, and gate. D means deterministic, L means optional model assistance followed by inspection, H means a human decision that the software must not make, and R means candidate triage by a named human or auditable AI.

## W0 — Set design constraints (H)

Select a built-in use profile in `quality-plan.json`; record the scope, human authority, spoiler policy, book type, intended readers, available rights, languages, public derivatives, size budget, confidentiality boundary, measured acceptance thresholds, and non-reconstruction rubric. When replacing an existing pack, hash-bind the comparison artifact and declare module metrics that must not regress under `content_floor`. Profile conformance is an AND of critical gates, not a quality score. Set `design_constraints = approved` only after each constraint has a test or review procedure. Authority approval is later bound to both the current substantive contract hash and current canonical-data hash.

Inventory author-, editor-, publisher-, and rights-holder-supplied material before generation. When structured chapters, summaries, claims, Q&A, book-specific policy, people, terms, or references are available, declare every module in an Author Input Package as `provided`, `augment`, `generate`, or `omit`; review its body-free plan and apply it as draft. Record-level `source_locations` preserve the supplied or verified locator in addition to the module source hash. A raw appendix registered only as an attachment remains a source, not an automatically inferred canonical module. See [Author Input Package](author-input.en.md).

## W1 — Bibliography (D → H)

Enter title, author, publisher, publication date, ISBN, official URL, and rights holder in `reading-pack.toml` and language data. Compare them with authoritative publication information.

## W2 — Certainty categories (L → H, optional)

Define categories by kind of evidence. Preserve the author's definitions without lossy paraphrase. Do not convert categories to confidence scores. Omit this module when the book has no such framework.

## W3 — Chapter map

- W3a (D): `import-plan` extracts a body-free hierarchy, locators, confidence, provenance, diagnostics, and outcome from Markdown, Org, EPUB3, PDF, or text without changing canonical data. PDF uses optional local Poppler and is always marked for review.
- W3b (D → H): review the plan, reconcile conflicts, and run `import-apply`. Under a cooperative project lock, the command rechecks the exact source and planned canonical state and writes only draft structure. An ambiguous or stale re-import stops before replacing canonical data.
- W3c (D → H): add page ranges only when checked against the published edition; heading navigation is sufficient for electronic-only books.
- W3d (L → D → R): for an unstructured book, a resumable `work plan/next/ingest/close/status/retry/finalize` session may create bounded chapter and module responses before the ordinary candidate run. It records explicit zero outcomes and reuses the same source-evidence verifier, work ledger, candidate review, and draft-only application path. R is either the default named-human review or an explicitly selected audited AI review. AI review requires a decision artifact bound to the exact run integrity, candidate record hash, evidence artifact hash, model name, method, and timestamp. The reviewer explicitly accepts content-hash-bound candidate IDs; only those IDs can be applied, and only as drafts. Candidate acceptance is not author approval. Do not include argument sequence, examples, metaphors, prose, or paragraph coverage. A glossary meaning is an abstractive, book-specific summary of at most 500 characters, never a quotation or lightly edited source definition. Ingest rechecks both the bound and source-copy limits and leaves a violating work item open for regeneration. PDF and EPUB representations are derived internally from the exact source, not supplied as arbitrary extracted-text sidecars.
- W3e (L → D → R, optional): after initial draft application, `work plan --purpose coverage` audits every selected chapter/module scope against the fixed `whole_book_gap_audit_v1` rubric and the hash-bound current canonical baseline. Each scope produces a supported improvement candidate or an explicit zero outcome. This checks omissions and materially weak summaries, terms, claims, and index context without accepting arbitrary project prompt text or imposing record-count quotas. High-recall people and term discovery remains W5/W6's source-verified catalog path.
- W3f (D, optional): when a reviewed explicit normalized-text chapter map is available, pass it to `work plan --chapter-map`. The map becomes part of the session identity, and ingest rejects any chapter-scoped evidence occurrence outside its bound span instead of waiting for candidate finalization.

## W4 — Canonical claims (D → H, optional)

Maintain stable claim IDs, descriptive/normative layer, kind, chapter references, certainty category, and falsification or reconsideration conditions. Build output mechanically from JSON. Human authorship is required for claim wording and epistemic metadata.

## W5 — People index (L → D → H, optional)

Run `catalog extract` against the exact registered source to create a private people inventory. The built-in recognizer produces conservative seed candidates, not high-recall named-entity truth: it can miss people and include non-people. A person can become a candidate only when its normalized occurrence resolves inside one exact, hash-checked chapter span. An inferred title-sequence chapter map remains `review_required` and cannot create people or term candidates until it is checked and resupplied as an explicit map. A private model/NER result may supplement recall through `catalog candidates --responses`, but its named value must occur in exact evidence inside its declared mapped chapter. The ordinary candidate verifier rechecks source-bound evidence, and the configured human or AI reviewer decides inclusion. A zero-candidate scope is not treated as absence; it remains an omission-review failure in the ledger. A plausible invented person is a validation failure even if the name is linguistically well formed.

After inclusion, use `catalog context-plan` to enumerate every person still missing an explanation and `catalog context-candidates` to propose `book_context` updates. Use `--refresh-existing` to return all existing descriptions to the plan for a quality re-audit. One sentence states who the person is as introduced in this book and which view, work, quotation, or evaluation the book connects to them. Exact evidence inside the target chapter is required; neither a generic biography nor a “mentioned in this chapter” placeholder is a substitute.

## W6 — Glossary navigation (D → H, optional)

Use the same private catalog inventory to propose subject terms and their first substantive explanation locations. An explicit definition or naming context takes precedence over a mere earlier mention in front matter, a contents list, or a chapter preview; absent that signal, routing falls back to the first mapped occurrence. Quoted definitions and acronym shapes are discovery signals, not proof that a term belongs in the glossary. A human checks false positives, omissions, preferred spelling, and explanation-location suitability. After inclusion, the same context plan adds `book_meaning` to every term: an abstractive summary of its meaning or role in this book, not a general dictionary definition, quotation, or lightly edited source sentence. It is limited to 500 characters. Every target is processed separately and bound to evidence inside its declared chapter. In a coverage pass, an existing source-copy risk must be replaced under the same ID and cannot be closed with a zero result, including when the existing record came from a `provided` AIP module.

## W7 — Misreadings and corrections (L → H, optional)

Record a concise misreading and a self-contained correction. The correction should be useful offline and may also point to an official page. It must not disclose confidential evaluation prompts or copy long passages.

Prefer an independently maintained author-Q&A source over inferring corrections from the book alone. Register it with `sources plan` and `sources apply`, then run `qa plan`. Each item must supply criticism, impact on the book, the book's response, and remaining uncertainty under a stable source key. Structured JSON can carry an explicit kind. Appendix-style Org can use `QA_TYPE` or a separately reviewed `qa classify` mapping. Unclassified criticism remains unresolved; the workflow never calls it a misreading automatically. Concise, edited Q&A can be converted deterministically with `qa candidates`. For a long Q&A, do not copy all four facets verbatim: pass concise generated candidates through `qa candidates --responses`, covering the complete classified plan with exact evidence inside each corresponding facet of the same item. Neither path bypasses long-copy checks, configured human/AI candidate acceptance, or author approval.

## W8 — AI instructions (H)

Review the local pack template and generated SYS. It must cover source discipline, web precedence, descriptive/normative separation, non-ranking certainty, symmetric testing, no quotation or reconstruction, navigation assumptions, referenced-page prompt injection, no author impersonation, no unauthorized translation, and a fixed initial response.

When an authority-supplied reference is official companion material, declare its closed relation, exact-or-prefix scope, and proactive retrieval policy in canonical reference input. Rendering then adds fixed model-independent REF metadata and SYS behavior. `book.official_url` alone remains bibliographic metadata and does not enable proactive retrieval.

## W9 — References (D → H)

The catalog workflow may inventory explicit HTTP(S) URLs and DOI strings as source-grounded reference candidates. Use stable IDs and official HTTP(S) URLs. Core validation checks syntax and cross-language parity without requiring a network. Detection does not establish relevance, authority, safety, or current reachability; those remain human publication checks because CI is offline-capable.

Candidate extraction never infers that a URL is official companion material. That status must come from producer canonical input or an authority-supplied Author Input Package.

Create people, glossary, and reference candidates together with `catalog candidates` unless deliberately reviewing only selected modules. One combined candidate run and reconciled work ledger preserve one canonical snapshot across the catalog work; separately applying three independently created runs can make later runs stale. This combination does not create blanket acceptance: every candidate remains separately reviewable and applicable only as a draft.

## W10 — Cross-module review, assembly, and validation (D → H)

Before recording candidate decisions, use `review bundle` to combine the supplied chapter/summary, claim, certainty, people, term, reference, and author-Q&A runs in one owner-only HTML view. Supply the exact source for every run; optionally bind its reconciled ledger and semantic review. Rendering rechecks run integrity, source and evidence hashes, and the current canonical snapshot. A missing section means not generated or not supplied, not that the book has no such content. The bundle is read-only and provides no accept-all operation, so the human records accept or reject decisions separately by candidate ID.

After explicitly accepted candidates have been applied as drafts, run `validate`, `build`, and `check`. Validation covers schemas, unique IDs, broken references, bilingual parity, translation freshness, summary budgets, and record states. Build output is deterministic. Check compares current output with a fresh render byte-for-byte.

When several runs were applied sequentially, `candidates receipt` creates one compact handoff artifact in application order. New manifests persist exact CAS before/after hashes, so every adjacent run and the current canonical state can be verified. Legacy runs require an explicit partial-continuity mode and remain labeled unverified.

## W11 — Evaluate behavior (L → H)

Before model evaluation, record the model ID, settings, date, and pass/fail rubric. Run `reading-pack measure --json` to recompute module counts, index-explanation completeness, and content characters, and require the declared `content_floor`. Then measure the predeclared profile thresholds and bind the evaluation record and its hash to the current canonical-data hash. Cover navigation, certainty, counterconditions, complete listing, absent-name/term resistance, quotation requests, impersonation, normative-to-factual conversion, out-of-pack questions, rule override, first receipt, one-word prompts, and reconstruction. The public repository provides categories and a blank record, not confidential attack strings or answers.

If a public delivery adapter is advertised, evaluate each `product / surface / model / route / profile / language` target separately. Record ingestion mode, origin availability, first and final markers, middle coverage, version binding, fetch rounds and URLs, retries, fallback, latency, and whether failure came from the Pack, procedure, host, or hosting. Test a previously saved complete Pack with its publication origin unreachable. Delivery compatibility is dated and target-specific; an adapter failure does not invalidate the canonical Pack.

## W12 — Final author review (H)

Run `reading-pack review export` to create one owner-only Markdown form bound by hash to the current canonical state. Exact authority-provided content appears in evidence-bound groups; generated, author-revised, and provenance-drifted records remain individual decisions. An agent may inspect everything, explain exceptions, known gaps, rights questions, and language-edition status, then fill response regions at the author's direction. The evidence is not the conversation but the edited Markdown with human reviewer, date, and submission attestation. Group choices expand to explicit record decisions before apply.

To activate book-specific policy before the complete review, use `review export --module policy`. It creates the same Markdown consent artifact with policy records only, omits whole-pack previews and global questions, and cannot grant whole-pack `final_signoff`. Normal review plan/apply semantics remain the only approval path; only approved policy records become operational SYS rules on the next build.

When the agent has extracted a small set of owner decisions, use `--record` to limit the form to those IDs. Passing current QA-passed runs with `--candidate-run` rechecks canonical and multilingual bindings and prefills changed fields as `revise_approve` in the same unsubmitted Markdown. Only the author's submission of the exact displayed changes applies and approves them in one transaction. Records not shown remain undecided, and a focused form cannot grant whole-pack final signoff.

`reading-pack review plan` creates a body-free change plan from the edited Markdown. `reading-pack review apply` rechecks canonical state, the form, and private evidence before applying it. Protected-content edits, stale sessions, and decisions without human submission attestation are rejected. Plain `revise` returns a record to `draft`; `revise_approve` approves the exact revised value displayed in the same signed form. Only a review that approves or excludes every published record and accepts every required policy sets `workflow.author_review` to `approved`. Agent assistance never replaces the human-edited evidence file.

For the ordinary pre-publication pass after W11 measured evaluation and evidence are ready, use `review export --release-signoff`. The same Markdown enumerates rights, publisher disposition, non-reconstruction, accountable quality authority, edition identity, and publication. With no exception, one explicit human instruction records submission, final signoff, and release approval. If publisher disposition is unresolved, the same form asks once for `approved` or `not_required`. Apply rechecks canonical content, configuration, quality plan, and evaluation evidence, then updates all release gates in one transaction; any failed machine check leaves every artifact unchanged. A correction uses a focused form first, repeats W11, and exports a fresh final form.

## W13 — Publish and maintain (D + H)

After `check --release` passes, a human chooses the pack's license, creates publication infrastructure, and synchronizes version, date, official URL, distribution page, and changelog. Any canonical or quality-contract change invalidates the corresponding hash-bound review; a primary-language change also invalidates affected translations. Large changes repeat W11 and W12.

## Checkpoint record

At each checkpoint record inputs, commands, result, reviewer, date, and unresolved effect. A remaining issue must say whether it blocks publication. Never classify missing rights, missing author approval, stale translations, broken references, or reconstruction risk as non-blocking.

Copyright 2026 Koichi Takahashi / 高橋恒一. Licensed under CC BY 4.0.
