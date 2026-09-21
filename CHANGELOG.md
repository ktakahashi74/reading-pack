# Changelog

All notable changes are recorded here. The format follows Keep a Changelog; versioning follows Semantic Versioning after the draft period.

## [Unreleased]

- Add an explicitly enabled single repair round for fresh deliveries: source- and finding-bound patches, per-finding dispositions, preserved first-pass artifacts, and new evaluations for changed chapters and the final Pack. Reserve both passes before sending; never reuse initial scores for changed content or auto-adopt by score.


- Add source-bound section pages and confirmed name aliases to fresh deliveries, range-safe whitespace quote recovery, stopped-CLI output recovery, byte-fixed evaluation successors, and complete cost-chain accounting with explicit unknown-cost reserves. Preserve raw exchanges and draft approval boundaries.


- Validate full Claude responses against the frozen schema locally while using a bounded native transport schema; avoid duplicated dynamic schemas, and prune/audit only undeclared keys without changing declared values. Stop dispatch and successor preparation for unresolved failed calls. Omit empty optional uncertainty fields during canonical mapping without inventing content.

- Default report-only deliveries to fresh generation of every content module from the current source. Require `--inherit-seed` alongside `--seed` to carry old records; reject implicit inheritance before dispatch.
- Evaluate all freshly generated auxiliary records and module absence reasons, count their provenance/evidence checks, and distinguish incomplete generation/evaluation from `delivered`. Preserve explicit-inheritance and frozen predecessor semantics; low scores still do not trigger repairs or acceptance.

### Changed

- Replace the one-touch `web-core-index-v2` delivery profile with `web-core-index-v3`, which defers `PROPS` alongside `MIS`, `NAMES`, and `GLOSS`. A Pack with 95 claims pushed the English v2 core to 81,333 characters, past the 80,000-character budget; v3 brings that core to about 30,000 characters without relaxing the budget. Deferred modules are now derived from one constant, so artifact rendering, the entry prompt, manifest, verification, and `delivery measure` stay in step. Published v2 bundles are unaffected. Sites with stable retrieval URLs must add a `props.txt` route. Production adoption still requires the same on-device ChatGPT test as v2 ([design](docs/reading-pack-web-core-index-v3-design.ja.md)).

## [0.7.0] — 2026-09-13

### Fixed and added

- Separate deliverables from private run evidence: `pipeline deliver --output` (default `<run>-delivery/` beside the run) and `pipeline export` write only the Pack, quality reports and canonical data with a hash manifest; manuscript, exchanges and seed copies stay in the run.

- Add `pipeline deliver --predecessor`: successor runs that carry a finished delivery's completed exchanges and redo only incomplete generation/evaluation jobs, with reservations for the redone work only. Add per-role reservations (`global_call_allowance_usd`, `evaluator_timeout_seconds`, `global_timeout_seconds`) and adapter salvage of final attempts rejected solely for undeclared keys, recorded as pruned paths.

- Add `pipeline deliver --seed`: carry an existing project's author-provided modules (certainty, claims, misreadings, policies, names, glossary, references) unchanged while generating chapter content; bind chapters to seed ids explicitly (`--chapter-map`), keep reviewed records byte-identical under `preserve`, reset inherited approvals to draft/pending, and report per-module completeness with lost/added ids so a delivery can no longer silently drop an author-provided layer. Seedless deliveries now state that those modules are empty by construction.

- Add the recommended `pipeline delivery-recipe` / `pipeline deliver` workflow: explicit source-bound outlines, fixed generation and evaluation reservations, quantitative reports without automatic quality gates, retained low-scoring/partially evaluated Packs, cumulative costs, and no automatic retry of started calls. Preserve existing contracts and artifacts.

- Carry explicit pipeline manuscript scope into candidate metadata and rendered delivery; artifact runs no longer assume that supplied excerpts are complete published editions. Preserve seed scope and reject conflicting relabeling.

- Add optional preregistered generator comparisons with common judging, finite execution, cumulative book budgets and conservative completion-rate comparisons. No quality saturation or Pack acceptance is inferred.
- Correct artifact-contract documentation and CLI success states; allow omission of the unused reader and explicit zero/one repair selection.

- Complete the artifact-acceptance production path through M10: version-bound instruction and local delivery inspection, finite phase reservations, one repair and full reinspection, separate candidate reassessment, append-only M1 records, hash-bound author decisions and independent workflow observations. Standard reader trials remain zero; legacy contracts and cumulative resources remain intact. Synthetic validation does not claim live-book quality or publication approval.

- Align new-run acceptance with reader utility: generated questions no longer create content quotas; retain central meanings, material conditions, attribution and factual correctness, while accepting supported specific navigation for incidental details. Freeze the scope before generation and grading, bind it to workflow identity, and preserve existing study scopes on restart.

- Add optional end-to-end operating envelopes, per-phase call and USD reservations, protected final-evaluation capacity, and cumulative deadlines across resumes and single-successor restarts. Refuse infeasible plans before model dispatch. Author changes do not silently open a second budget.
- Separate unqualified research (`--experimental`) from measured production. Add preregistered workflow qualification with fresh usage receipts, retained failures, finite input scope and no unit-test-to-quality promotion. No live-book qualification certificate is supplied.
- Preserve frozen development-question evidence when routing a repair to a distant footnote or supplement. Explain the canonical Pack scope consistently to both audit and adjudication workers.

- Derive benchmark requirement bounds from the worker schema, retrieve source quotations by verified span ID, and repair rejected questions within fixed limits before freezing holdout. Add an optional pinned Claude CLI adapter with audited structured output recovery and shared call/cost guards.

### 0.7.0 (alpha) preparation

- Aligned generated record schemas with candidate admission while preserving hash-bound author IDs; added exact body-page heading supplementation followed by independent re-review, fixed benchmark counts, per-candidate quotation quarantine and retained adapter failure details.

- Added local structure preflight before model calls, bounded vertical-PDF layout recovery, and source-bound section selection with independent review before benchmark generation. TOC/body differences and failed structure trials remain explicit.

- Added the fixed `pipeline recipe/start/resume/status/finalize` workflow for manuscripts with optional author supplements, source-bound candidates, independent AI grading, bounded repairs, held-out final evaluation, resumable exchanges, author signoff and local release builds.
- Added automatic profile selection, immutable input and adapter bindings, conservative non-regression gates, explicit failure/budget outcomes and separate delivery preflight.
- Added synthetic end-to-end and failure/recovery tests and bilingual operating instructions. Live-book quality evaluation is recorded separately and is not implied by unit-test success.
- Updated toolkit and reference implementation version declarations to 0.7.0. Format Specification and Production Standard remain 1.0-draft. This entry is unreleased; no release date or remote publication is asserted.

### Completion reliability

- Freeze finite coverage requirements; independently adjudicate audit suspicions with bounded cross-source evidence and the complete Pack. Retain optional advice without blocking reader tests, preserve material defects, and stop explicitly on unresolved evidence or a required protected-record change.
- Compare canonical structure with the source-bound heading inventory instead of summed model estimates. Preserve numeric estimates and all decisions in private audit reports.

- Support source-grounded bibliographic references without invented URLs, while preserving linked output and mandatory URLs for official companions.
- Review changed chapter content against a bounded, hash-checked neighboring window from the same source, preserving exact evidence positions and independent approval checks.

- Add explicit, hash-bound permissions for selected supplied-record draft revisions during checkpoint restart; preserve original records, author-input state and human decisions, with fresh evidence/review and later author adoption still required.

- Preserve validated completed-round or stopped working-draft checkpoints and their round numbers across engine updates, retaining frozen benchmarks and source provenance while requiring fresh quality evaluation.
- Limit chapter candidate review to new or changed content when the controller verifies unchanged structural fields; summaries, terms and spoiler scope still require independent source support.
- Route aggregate source errors through concrete source-scoped findings, skip unrelated repair calls, group targeted repairs separately from initial generation, and allow source-reviewed context additions around protected author records without rewriting them.
- Preserve complete Claude requests while placing stable reading context before changing identities; retain exact original and CLI input hashes for cost/cache auditing.
- Reconcile seeded Markdown/Org outlines with nested headings and back matter while preserving supplied IDs/prose; retain human review history through explicit unapproved draft successors.
- Run reader evaluation only after source checks pass, bind empty-summary repairs to source regions, supply development-only repair guidance, and omit protected prose only from generation contexts.
- Enforce Author Input Package module modes before candidate review/application, evaluate populated seeds before generation, partition generation/review into bounded units, and isolate record admission from transport validation.
- Restore omitted immutable chapter metadata without accepting changed values; remove duplicate audit content and optionally batch grades while retaining isolated one-question reader requests and case-bound evidence.
- Apply only dependency-closed accepted candidates and add explicit pre-holdout restart with exact generation/review replay and fresh admission checks.
- Add content-bound preparation reuse across engine updates without reusing reader answers or tested holdout suites.

## [0.6.0] — 2026-08-22

### Added

- Portable-first delivery tooling with `delivery build`, `check`, `measure`, and `probes`; byte-identical Markdown/text Pack copies; versioned portable, direct-URL, Web-lazy, and one-touch `web-core-index-v2` prompts; manifest/bootstrap/module generation; record-boundary splitting; byte and character budgets; immutable URL checks; and full reconstruction validation.
- A one-touch core/shards adapter that keeps initial delivery to one URL, defers exact `MIS`, `NAMES`, and `GLOSS` sections to question-routed URLs, and reconstructs the unchanged canonical Pack byte for byte from manifest offsets.
- Draft 2020-12 schemas for delivery plans and manifests, synthetic size/chain/trust/corruption probes, and an AGI-book staging evaluation that keeps the canonical single-file Pack independent from optional Web adapters.

### Changed

- Added target-specific delivery guidance: one-touch core plus lazy modules for ChatGPT, complete-Pack direct loading for compatible Claude Chat targets, and complete-Pack attachment for Gemini.
- Recorded the logged-in ChatGPT Chat run-008 success and the rejected run-009 two-hop Entry Prompt fetch, preserving both experiments as compatibility evidence.

## [0.5.0] — 2026-08-16

### Added

- Optional deterministic Agent Skills-compatible directory and ZIP generation from already-built Reading Packs, with read-only checking, byte-identical multilingual references, transactional replacement, release-gate reuse, and bounded path, file-type, size, and archive validation.
- Japanese and English guidance that separates the Conversational Edition reader experience, the Reading Pack artifact, and the optional Agent Skill compatibility container.
- A shared artifact transaction layer for hash-bound, path-restricted, recoverable canonical writes.
- Record-scoped author-review forms, exact QA-passed candidate-run suggestions, and signed `revise_approve` decisions that can apply and approve one reviewed revision atomically while retaining bilingual parity checks.
- A guarded `work close` command for recording source-supported zero-result generation items without constructing an external response file or turning execution failure into a content judgment.
- An optional one-shot release-signoff form that records complete author review, publisher disposition, quality authority, and publication approval under one explicit human signature while retaining hash-bound, transactional validation.

### Changed

- Split the former aggregate Reading Pack specification into an artifact-only Format Specification, a tool-neutral Production Standard, and a toolkit-specific Reference Implementation Profile. Format conformance, production conformance, and generator identity are now independent claims.
- Expanded the public model-evaluation form with level mappings, safe example questions, and a third-party self-declaration block while keeping exact attack wording and raw answers private.
- Linked the optional alpha Reading Pack Bot deployment server from the English and Japanese project READMEs while keeping it separate from the core toolkit.
- Manuscript handoff is explicitly one dependency-resolved file. DOCX, RTF, and dependency-bearing Org remain upstream conversion concerns; direct import does not add a custom bundle or cook layer and rejects unresolved `#+INCLUDE` directives.
- Author review now has one public exchange format and four plain subcommands: `review export`, `status`, `plan`, and `apply`.
- The 1,787-line CLI is split into a small dispatcher and feature-scoped core, author-review, and producer command modules. The `reading_pack` generation kernel is isolated at about 6,600 lines, authority workflows live in `reading_pack_review`, and catalog extraction, candidate workflows, private candidate review, work ledgers, and Agent Skill distribution live behind the optional `reading_pack_producer` boundary. The core CLI has no direct producer dependency.
- Author Input Package and author-review application now share the same prepared-write, hash-check, validation-failure, and interrupted-run rollback implementation.
- CI now covers every declared Python version through 3.14 and verifies that built wheels carry the repository's mixed-license texts and path map.

### Fixed

- Rebuilt the synthetic Agent Skill distribution from current canonical Pack output and verified deterministic directory and ZIP reproduction.
- Corrected `doctor` so it reports the installed `jsonschema` runtime dependency instead of the obsolete standard-library-only claim.
- Included the CC BY 4.0 legal text and license map in built distributions that also carry CC BY-licensed schemas and Markdown runtime assets.

### Removed

- The browser/result-JSON and chapter-split Markdown author-review paths, their HTML asset, parser, generated packet files, and browser-result schema. The single human-edited Markdown remains the consent and correction evidence; its private sidecar is one body-free manifest.

## [0.4.0] — 2026-08-14

### Added

- Optional producer-declared official companion references with closed exact/prefix and proactive-retrieval semantics, bounded HTTPS validation, deterministic REF annotations, and model-independent conditional SYS rules.
- Author Input Packages with explicit `provided`, `augment`, `generate`, and `omit` modes for chapters, summaries, chapter terms, certainty, claims, Q&A, people, glossary terms, and references.
- Body-free, checksum-bound aggregate plan/apply workflow for one or more language packages; prospective primary-to-translation linking; support-source registration; recoverable multi-file application; and a canonical per-language provenance/history ledger.
- JSON and CSV authority-input schemas and templates, including aliases and book-specific context for people and terms.
- Lossless optional claim source locators and reader notes, plus stable official-page anchors for classified Q&A records.
- Optional localized bibliography metadata (`publisher`, publication date, ISBN, official URL, and `contents_note`) for translated or region-specific pack editions.
- Optional localized `display_author` while retaining one canonical author identity across languages.
- Validation of supplied-set completeness, intentional omission, source provenance, and semantic drift after application.
- Owner-only, chapter-grouped Markdown author review with protected display text, explicit per-record decisions and edit zones, body-free plan/apply, translation-pair checks, recoverable writes, final signoff, and hash-chained AIP provenance overlays.
- Self-contained offline browser review for occasional non-technical authors, with authority-bound group decisions, exception-only individual forms, policy questions, rendered pack previews, browser-local progress, downloaded result JSON, and expansion back to per-record audit actions before plan/apply.
- Human-owned, agent-assisted single-file Markdown review as the default interface. The edited form is the consent and correction evidence; it includes evidence-group choices, individual exceptions, policy comments, structured overrides, submission attestation, protected static content, and shared stale-checked plan/apply semantics. The human-facing file carries only a short review/session hash reference; the complete session is reconstructed from private evidence and current canonical state rather than embedded as Base64. The agent assists inspection and filling, while the browser remains a fallback.

### Changed

- People and term indexes render supplied aliases.
- The independent-Q&A boundary now explicitly distinguishes authority-classified canonical input from source-grounded candidate classification.

## [0.3.0] — 2026-08-13

### Added

- Optional audited AI candidate review using excerpt-free decision artifacts bound to the exact run, candidate records, evidence artifacts, model identity, method, and timestamp; it permits draft application but never author or release approval.
- Explicit `pdf-vertical` source format for bounded reconstruction of Japanese
  one-glyph-per-line text layers, reused consistently by evidence and catalog
  workflows while preserving the original PDF as source authority.
- Body-free, hash-bound registry for typed primary and support sources.
- Four-facet author-Q&A plans with explicit issue classification; unclassified criticism never becomes a misreading automatically.
- Complete-plan generated Q&A ingestion with per-field evidence bound to the corresponding source item, plus support-source provenance in canonical drafts.
- Private people, subject-term, and reference inventories with exact source spans, chapter-bound people and terms, unresolved-count reporting, source-checked model/NER recall additions, and one combined catalog candidate run.
- Deterministic module/scope work ledgers that distinguish generated, unsupported, failed, and skipped work, including book-wide claims and reading issues.
- Run-bound semantic assessment inventories, excerpt-free findings, named-human adjudication, and owner-only source-rehydrated HTML reviews.
- One-stop, read-only private review bundles for chapter, summary, claim, certainty, people, term, reference, and author-Q&A candidate runs.
- Complete, body-free catalog-context plans plus source- and chapter-bound update candidates for `book_context` on every retained person and `book_meaning` on every retained term.
- Reproducible `reading-pack measure` coverage counts and an optional SHA-256-bound `content_floor` release gate, so a replacement pack cannot silently regress below a reviewed prior pack in summaries, claims, qualifications, reading issues, contextual indexes, references, or canonical information volume.

### Changed

- Generated prompts now treat original-book access as unavailable unless text
  was actually retrieved or supplied in the conversation and forbid promises
  to search or quote from unprovided text. People and term indexes render their
  source-grounded book-specific context when present and explicitly identify
  any legacy entries that remain locators only.
- Release validation now requires retained people and terms to include concise
  book-specific context (`RP303`), rejects formulaic placeholder context
  (`RP304`), and lets `catalog context-plan --refresh-existing` plan a complete
  source-grounded replacement pass.
- Default Japanese and English openings now explain the upload workflow and
  useful question patterns; the no-question welcome names only the navigation,
  summary, claim, and index material actually present in that pack.
- Catalog heuristic v3 records a dedicated conservative `pdf-vertical` mode
  that suppresses unmarked Japanese name shapes and sentence-shaped definition
  phrases after per-glyph reconstruction; verified model/NER additions remain
  the recall path.
- Glossary routing now prefers the first explicit definition or naming context
  over an earlier contents, front-matter, or chapter-preview mention, with
  source order retained as the conservative fallback.
- Catalog heuristic v2 tightens person-context and acronym filters so generic noun phrases, Roman numeral headings, initials, and long all-caps headers are less likely to enter the review queue; language-aware recall remains a separately verified model/NER pass.
- Reading issues now distinguish misreadings, clarifications, open objections, and author updates, and can preserve impact and remaining uncertainty.
- Generated system guidance refers only to challenge material that is actually present.
- Registered JSON sources use one decoded, pointer-addressable representation for evidence and copy-risk checks, including escaped Unicode and repeated values; registered format rather than filename suffix selects the reader.

### Security

- Bound JSON evidence expansion, nesting, pointers, occurrences, and repeated-value indexing; reject unsafe surrogate text and prevent escaped JSON from bypassing long-copy quarantine.
- Owner-only catalog inventories and review bundles with no overwrite, external resources, mutation, or accept-all path; all included sources, evidence spans, ledgers, semantic reviews, and canonical snapshots are rechecked before rendering.

## [0.2.0] — 2026-08-13

### Added

- Optional offline, structure-only PDF import using bounded local Poppler tools, with encrypted-file rejection and conservative table-of-contents extraction.
- Seven gate-based book/use quality profiles with explicit scope, content authority, spoiler policy, mandatory modules, critical policies, and release conformance.
- A body-free import-plan and explicit apply boundary with hierarchical units, locators, extraction confidence, provenance, diagnostics, stable IDs, freshness checks, cooperative locking, and stale-write rejection.
- Private candidate runs with internally derived source text, transient evidence snippets, hashed evidence spans, candidate and base-record hash binding, explicit named-human acceptance, duplicate/reference/copy-risk quarantine, and draft-only application with detectable recovery state.
- Measured profile acceptance results and human authority decisions bound to current canonical data and the substantive quality contract.
- Bounded shell-free local JSON adapters with an explicit trusted-executable boundary; the adapter is not a sandbox.
- Profile-specific generated instructions and metadata, plus Japanese and English quality-pipeline documentation.

## [0.1.0] — 2026-08-12

### Added

- Offline Python 3.11+ CLI with `init`, `import`, `validate`, `build`, `check`, `doctor`, and `link-translations`.
- Structure-only UTF-8 Markdown, Org mode, EPUB3, and plain-text importers.
- Deterministic bilingual pack generation from canonical JSON and templates.
- Schema, ID, reference, parity, translation-freshness, generated-output, and human release gates.
- Japanese and English specifications, quickstarts, workflows, concepts, rights guidance, and model-independent candidate prompts.
- Fully synthetic bilingual Level 3 example and public offline test suite.
