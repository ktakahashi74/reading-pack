# Changelog

All notable changes are recorded here. The format follows Keep a Changelog; versioning follows Semantic Versioning after the draft period.

## [Unreleased]

## [0.5.0] — 2026-08-16

### Added

- Optional deterministic Agent Skills-compatible directory and ZIP generation from already-built Reading Packs, with read-only checking, byte-identical multilingual references, transactional replacement, release-gate reuse, and bounded path, file-type, size, and archive validation.
- Japanese and English guidance that separates the Conversational Edition reader experience, the Reading Pack artifact, and the optional Agent Skill compatibility container.
- A shared artifact transaction layer for hash-bound, path-restricted, recoverable canonical writes.
- Record-scoped author-review forms, exact QA-passed candidate-run suggestions, and signed `revise_approve` decisions that can apply and approve one reviewed revision atomically while retaining bilingual parity checks.
- A guarded `work close` command for recording source-supported zero-result generation items without constructing an external response file or turning execution failure into a content judgment.
- An optional one-shot release-signoff form that records complete author review, publisher disposition, quality authority, and publication approval under one explicit human signature while retaining hash-bound, transactional validation.

### Changed

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
