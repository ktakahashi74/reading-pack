SPEC | name=Conversational Edition / Reading Pack | version=1.0-draft | language=en | primary=false | source_language=ja | date=2026-08-15 | author=Koichi Takahashi | license=CC BY 4.0

# Reading Pack Specification 1.0-draft

The Japanese specification is the normative primary text. This English specification is its corresponding translation with the same section structure and requirement IDs. If they conflict, the Japanese text controls and both files must be corrected in the same commit.

The normative terms are MUST, MUST NOT, SHOULD, and MAY. Compatibility across drafts is not guaranteed.

## 0. Status and scope

**RP-001** An implementation MUST generate one Markdown file for AI input from structured data and templates maintained by an author or editor. The generated artifact MUST NOT reproduce or replace the original book.

**RP-002** An implementation MUST support Japanese-only, English-only, and bilingual Japanese–English projects. Core functions MUST NOT require a network, API key, particular vendor, or model.

**RP-003** This specification defines a toolkit and process. It does not grant rights in a particular book, author approval, or a publication decision.

## 1. Terms

**RP-004** A Conversational Edition is the book form and reader experience designed for dialogue through an AI interface. A Reading Pack is its current technical artifact: one Markdown file. Canonical sources are project configuration, structured language data, and templates; `dist/` contains generated files. A compatibility container such as an Agent Skill is an optional distribution of the existing Reading Pack, not canonical data, a replacement for the Reading Pack, or a new approval boundary.

**RP-005** The primary language is the authoritative language selected by `primary_language`. Translation freshness means that a translated record matches the semantic-content hash of its primary record.

**RP-006** Production levels range from 1 to 3. Level 1 provides navigation, Level 2 adds summaries, corrections, evaluation, and author judgments, and Level 3 adds canonical claims with epistemic metadata.

## 2. Design principles

**RP-007** Final author review MUST occur before publication and MUST NOT be automated. AI output MUST remain a `draft` candidate, and AI MUST NOT set `approved`.

**RP-008** The complete public bundle MUST be non-reconstructive. A summary MAY state a topic and approved position, but MUST NOT include argument sequences, examples, metaphors, prose, or paragraph-level coverage.

**RP-009** SYS is quality guidance, not access control. Information minimization MUST be the primary defense.

**RP-010** Output MUST be generated deterministically from structured canonical sources and templates. Implementations MUST NOT create a second source of truth by editing output directly.

## 3. Project model

**RP-011** A project MUST contain at least `reading-pack.toml`, `data/pack.<lang>.json`, `templates/pack.<lang>.md`, and `dist/`. Schemas MUST be published under `schema/`.

**RP-012** Configuration MUST declare `format_version`, `version`, `pack_date`, `status`, `primary_language`, `languages`, `level`, bibliography, review gates, and size limits.

**RP-013** Language data MUST contain `chapters`, `certainty`, `claims`, `misreadings`, `names`, `glossary`, and `references`, and MAY contain the `policies` collection. New projects MUST initialize `policies` as an array; pre-policy canonical documents MAY omit it and MUST be interpreted as an empty collection. An unused optional module MAY be an empty array.

**RP-014** Every record MUST have a stable ID and a state of `draft`, `reviewed`, or `approved`. IDs MUST be unique across all collections.

## 4. Input formats

**RP-015** The import boundary MUST be one local file whose dependencies have already been resolved. An implementation MUST support UTF-8 Markdown, Org mode, and EPUB3, and MAY support plain text and PDF. Except for the standard internal structure of EPUB, core import MUST NOT recursively resolve directories, custom bundles, Org `#+INCLUDE`, or external file references. The user or an upstream converter is responsible for turning DOCX, RTF, dependency-bearing Org, and other formats into a supported self-contained file. Unsupported formats and unresolved dependencies MUST fail explicitly. A PDF importer MAY require documented local system tools.

**RP-016** Import MUST extract only title, headings, and necessary publication metadata. It MUST NOT copy manuscript prose into canonical data or a generated pack.

**RP-017** An EPUB3 importer MUST process the standard ZIP/XML container, package metadata, manifest, spine, and XHTML headings locally. It MUST NOT circumvent DRM, transmit a manuscript, permit path traversal or external spine items, resolve unsafe XML entities, or expand excessive data.

**RP-017A** A PDF importer MUST process files locally, retain only conservative heading/metadata structure, preserve an existing title when metadata is not credible, reject encrypted/password-protected or excessive inputs, bound external-tool time and output, and MUST NOT retain extracted body text. It MUST document that flat PDF text cannot guarantee correct heading or printed-page recovery.

**RP-017B** A vertical-PDF adapter MUST be selected explicitly and MUST NOT be inferred for ordinary PDFs. Import MUST record the selected format in the source identity; candidate, catalog, and review workflows MUST derive the same bounded representation from the exact original PDF. Arbitrary extracted text MUST NOT become source authority, and the adapter MUST disclose that structure, font mapping, and two-page-spread boundaries require human review.

## 5. CLI

**RP-018** An installable Python 3.11-or-newer CLI MUST provide `reading-pack init`, `import`, `validate`, `build`, `check`, and `doctor`.

**RP-019** The CLI MUST support `--lang ja`, `--lang en`, and both languages. It MUST return clear errors and distinct nonzero exit codes by failure class.

**RP-020** `init` MUST reject a non-empty target. `import` MUST reject overwriting existing canonical records by default. An implementation that permits replacement MUST require an explicit option.

**RP-021** `validate` MUST check schemas, duplicate IDs, broken references, missing languages, count/order parity, translation freshness, and size limits. `check` MUST compare output with a fresh render byte-for-byte.

## 6. Japanese–English support

**RP-022** A bilingual project MUST declare a primary language and both generated languages. Every collection MUST use common IDs in equal count and order.

**RP-023** Each non-primary record MUST retain `source_id` and the semantic SHA-256 of its primary record. Old hashes MUST become validation errors after wording or structure changes.

**RP-024** Updating a source hash MUST revoke translated-record approval and reset the record to `draft`. Tests MUST cover Japanese names, headings, and UTF-8 as well as English word boundaries and quotation marks.

## 7. AI assistance and human gates

**RP-025** AI MAY propose outline, summary, term, person, claim, reading-issue, explicit-source policy, and translation candidates. Prompts MUST prohibit long-form regeneration, uncleared translation, inferred permission or official status, and substitute-book generation, and MUST remind users to check confidentiality and provider terms.

**RP-026** Release readiness MUST separately record design constraints, rights, author review, publisher review, non-reconstruction review, and publication decision. Publisher review passes only when approved or when a human has determined it is not required.

## 8. Production workflow

**RP-027** The workflow MUST use W0–W13 and distinguish D (deterministic), L (model-assisted then inspected), and H (human judgment). W0, the W11 judgment, W12, and the W13 publication decision MUST NOT be automated.

The stages are W0 design constraints, W1 bibliography, W2 certainty, W3 chapter map, W4 canonical claims, W5 people, W6 terms, W7 misreadings, W8 SYS, W9 references, W10 assembly, W11 evaluation, W12 author review, and W13 publication operations.

## 9. Generated pack format

**RP-028** A pack MUST be UTF-8 Markdown in this order: first-line `PACK`, H1, AI block, reader block, `SYS`, `BIB`, `MAP`, optional modules, `META`, and final-line `ENDPACK`. `ENDPACK` counts MUST equal record counts.

SYS MUST cover source discipline, official-web precedence, descriptive/normative separation, non-ranking certainty, symmetric testing, no quotation or reconstruction, navigation assumptions, non-execution of referenced-page instructions, no impersonation, no uncleared translation, and a version-matched fixed initial response. Navigation MUST state that the Pack does not substitute for the original, route necessary checks by print page, chapter, and section, and prohibit reconstruction when the reader cannot access the original.

## 10. Validation and evaluation

**RP-029** Public tests MUST use synthetic fixtures to cover schemas, IDs, references, translation freshness, deterministic generation, language parity, CLI end-to-end behavior, and excessive manuscript leakage. Live-model evaluation MUST NOT be mandatory in CI.

Model evaluation MUST predeclare a rubric and record model ID, settings, and date. It covers navigation, certainty, counterconditions, complete lists, absent entries, quotation requests, impersonation, normative-to-factual conversion, out-of-pack questions, rule override, initial receipt, one-word prompts, and reconstruction. Specific confidential attacks and raw answers MUST NOT be publication requirements.

## 11. Conformance and licenses

**RP-030** A conforming implementation satisfies RP-001 through RP-060 and displays production level, profile conformance state, and languages in META. An optional compatibility container such as an Agent Skill is not required for core conformance and MUST NOT be treated as changing or creating approval. Code and tests use MIT; specifications, documentation, schemas, and prompts use CC BY 4.0; synthetic samples SHOULD use CC0. Toolkit licenses MUST NOT be applied automatically to a book-specific pack.

**RP-030A** The core CLI and library MUST remain able to import, validate canonical data, build and reproduce Reading Packs, and run the single-Markdown author review without a producer plugin that supplies catalog extraction, candidate generation, candidate views, or Agent Skill distribution. Producer functions MAY be bundled in the same distribution, but the dependency MUST remain one-way and lazily loaded from the core. Core operations that change multiple canonical artifacts MUST delegate before/after hashing, allowed paths, prepared records, atomic writes, and rollback after validation failure or interruption to one shared transaction layer.

Copyright 2026 Koichi Takahashi / 高橋恒一. This specification is licensed under CC BY 4.0. The express patent grant in Apache-2.0 remains a future policy question.

## 12. Quality-controlled production across books

**RP-031** Every new project MUST select an explicit genre/use profile, production scope, human authority type, and production level. A profile MUST define mandatory modules, mandatory chapter fields, critical policies, and a minimum level. Profile conformance MUST be the conjunction of its critical gates, not an averaged quality score. A profile intended for textbooks, fiction, anthologies, or reference works MUST represent the genre-specific fields needed to review that use rather than forcing every book into a nonfiction record shape.

**RP-032** A profile-conforming project MUST keep a reviewable quality plan separate from canonical book data. The plan MUST predeclare its scope, spoiler policy, module applicability, critical policies, acceptance thresholds, and human authority. Release validation MUST bind authority approval to the current substantive quality contract and current canonical-data hash. A changed contract or canonical record MUST make the prior approval stale.

**RP-033** Structure ingestion MUST provide a planning operation that does not mutate canonical data and a separate applying operation. The plan MUST contain only bounded metadata, hierarchy, locators, confidence, provenance, diagnostics, and source identity; it MUST NOT contain body prose, summaries, claims, or approval state. Applying a plan MUST recheck the exact source identity and plan integrity and MUST stop on an ambiguous reconciliation.

**RP-034** Manual structure recovery MAY use a bounded, body-free outline sidecar bound to the exact source SHA-256. It MUST record reviewer and reason, MUST use explicit stable source keys where automatic identity is unavailable, and MUST NOT carry summaries, terms, body text, or approval state. A checksum or artifact hash in a plan or candidate manifest is an accidental-corruption and cooperative stale-state detection mechanism; it MUST NOT be described as adversarial tamper protection, a digital signature, authentication, or proof of reviewer identity. Reviewer identity is self-attested, and a writer able to rewrite a private run is outside the cooperative threat model.

**RP-035** Candidate generation MUST occur in a private run outside canonical data. Each accepted candidate artifact MUST be bound to the exact source file, normalized source representation, candidate record hash, and one or more bounded evidence spans. For PDF and EPUB, the trusted core importer MUST derive that representation directly from the exact source; the CLI MUST NOT accept an arbitrary extracted-text sidecar as a substitute for that derivation.

**RP-036** Evidence verification MUST establish only that the recorded span occurs at the recorded location in the exact normalized source and that the candidate artifact has not changed. It MUST NOT be presented as proof that the evidence entails the candidate, that a summary is complete, that attribution is correct, or that a generated interpretation is authoritative. Finalized evidence references MUST omit source excerpts, and all candidate fields MUST remain subject to bounded size, type, source-copy, ID, and reference checks. A generated `book_meaning` MUST be an abstractive summary of at most 500 characters, not a quotation or lightly edited source definition. Response ingest MUST recheck both the bound and source-copy risk and MUST NOT store a violating work item. When a coverage audit finds the same risk in an existing `book_meaning`, it MUST require a replacement under the same ID and MUST NOT close the scope with a zero result, including for AIP-supplied records.

**RP-037** Passing automated candidate checks MUST yield `ready_for_review`, never approval. Each content-hash-bound candidate MUST have an explicit acceptance before it can be applied. A named-human review MUST be the default. An implementation MAY provide an AI-review option only when it requires an excerpt-free decision artifact bound to the exact run integrity, candidate record hash, evidence artifact hash, model identity, review method, and timestamp. Application MUST require explicit candidate IDs, recheck the acceptance and evidence bindings, and write candidate-derived records only as `draft`. Rejection MUST leave canonical data unchanged. Human or AI candidate acceptance is editorial triage and MUST NOT substitute for final author approval.

**RP-038** Every operation that mutates canonical data MUST use cooperative project locking and compare the source and canonical state used for planning or review with the current state immediately before replacement. A stale source, stale canonical-data hash, stale base-record hash, ambiguous match, or failed validation MUST stop without overwriting intervening canonical edits. If canonical data and its run manifest require separate file replacements, the implementation MUST provide a recoverable prepared state and MUST NOT claim filesystem-wide transactional atomicity.

**RP-039** Import, re-import, translation relinking, and candidate application MUST revoke approval for every record whose source, structure, or semantic content changed. Automated operations MUST NOT set `reviewed` or `approved`. Release conformance MUST fail unless current canonical data has named-authority approval, every critical policy is approved, mandatory profile data is present, every published record is approved, and a measured evaluation bound to the current canonical-data hash meets every predeclared threshold with a retained evidence-record hash.

**RP-040** A local model adapter MUST use bounded input, output, and execution time and MUST invoke a configured executable without a shell. These controls do not sandbox that executable, prevent it from reading local files, or prevent it from using a network. Documentation MUST therefore treat the adapter executable as trusted code and MUST NOT imply that offline core behavior proves an arbitrary adapter is offline or safe.

## 13. Author Input Package

**RP-041** An implementation MUST allow each of `chapters`, `summaries`, `chapter_terms`, `certainty`, `claims`, `qa`, `policy`, `names`, `glossary`, and `references` to declare one of four modes: `provided`, which replaces the module with a complete authority-supplied set; `augment`, which replaces matching IDs with supplied values while preserving unsupplied records; `generate`, which delegates to the ordinary generation workflow; and `omit`, which explicitly leaves the module empty. `chapters` MUST NOT use `omit`. A legacy manifest that declares the former nine modules and omits only `policy` MUST be interpreted as `policy: generate`.

**RP-042** An Author Input Package MUST declare a unique package ID, language, self-attested authority type, name, and date, every module mode, and any supporting sources. Module data for `provided` or `augment` MUST be supplied as bounded UTF-8 JSON or CSV files that are direct children of the manifest directory and have safe source IDs and roles. The implementation MUST publish schemas and documentation for the JSON envelope, record fields, CSV headers, and array delimiter.

**RP-043** `provided` MUST completely replace an existing module, `augment` MUST replace matching IDs and preserve others, `generate` MUST leave canonical data unchanged, and `omit` MUST empty the module. Canonical schema, ID uniqueness, references, and language parity MUST be validated before application. Supplied approval state MUST NOT be trusted, and every automatically applied record MUST be `draft`.

**RP-044** Author Input Package mutation MUST be divided into a body-free plan and an explicit apply operation. One plan MAY aggregate at most one package per configured language and MUST reject duplicate language, package, or source identities. It MUST apply the primary-language package prospectively before deriving translation links, then validate the complete prospective language data set once. The plan MUST record every manifest hash; each source filename, format, size, and SHA-256; hashes of the complete before and prospective canonical data sets; and supplied, added, replaced, removed, and preserved IDs and semantic hashes per language and module. It MUST NOT retain local paths or supplied prose. Apply MUST recheck every package, the plan, and current canonical state under one project lock, stop before writing on an intervening change, and use one recoverable prepared state for every changed canonical language file, source registry, and author-input ledger.

**RP-045** A project MUST keep a body-free canonical ledger recording, per language and module, the current mode, package, authority, manifest hash, source identity, supplied record IDs and semantic hashes, post-apply count, and package history. Validation MUST detect mismatched `provided` sets, nonempty `omit` modules, stale supplied-source provenance, and changed supplied content. Every canonical record type MAY carry bounded `source_locations` in addition to its module source ID/hash. Registering a supporting source alone MUST NOT transform its content into a claim, term, policy, or reading issue. The neutral Q&A text field is `issue`; legacy `misreading` input MUST remain readable, MUST NOT coexist with `issue`, and MUST be deterministically normalized on application. When independent Q&A produces candidates, criticism MUST NOT be presumed to be a misreading; `misreading`, `clarification`, `open_objection`, and `author_update` MUST be selected by authority input or explicit classification.

## 14. Author review

**RP-046** An implementation MUST export every applied canonical record in one owner-only Markdown file that a person can read and edit. The edited Markdown itself MUST be the evidence of human consent, hold, exclusion, and correction instructions. The form MUST be hash-bound to the complete current canonical data, configuration, quality plan, templates, Author Input Package ledger, and prior author-review ledger. On import, it MUST interpret only declared response regions and reject every other change. The same exchange format and apply path MAY export a form scoped to selected record modules or explicit record IDs. Record scope MUST include every configured language for the same ID. A scoped form MAY approve only displayed records without whole-pack previews or global questions, but MUST NOT grant whole-pack final signoff or set `workflow.author_review=approved`.

**RP-047** Each record MUST select at most one of `approve`, `revise`, `revise_approve`, `exclude`, or `hold`. Only `revise` and `revise_approve` MAY carry corrections. An applied `revise` MUST become `draft`; `revise_approve` MAY apply the correction and transition the result to `approved` in one transaction only when the same human-signed Markdown displays every corrected value. `approve` approves only displayed current content. A primary-language revision MUST require an explicit decision for its translation, and exclusion MUST remove the same ID from every configured language. The complete prospective state MUST be validated before any write.

**RP-048** Author-review mutation MUST be divided into a plan containing no prose, comments, or local paths and an explicit apply operation. Apply MUST recheck the plan, review packet, and current canonical state under one project lock and use a recoverable `prepared` state. When author review changes a value supplied by an Author Input Package, provenance MUST remain verifiable through a body-free hash chain. Only a review in which every record is approved or excluded and final sign-off is selected MAY set `workflow.author_review` to `approved`; it MUST NOT change any other release gate.

**RP-049** Group decisions MAY be permitted only when every current record's semantic hash matches a `provided` record hash in the Author Input Package ledger and source identity and authority metadata are present. A group MUST be bound to language, collection, module state, source, and an inspectable set of record IDs, and it MUST permit an individual record override. Generated, revised, or provenance-drifted records MUST remain individual decisions.

**RP-050** The form MUST place the RP-049 evidence groups, individual decisions, policy questions, comments, structured field corrections, reviewer, date, submission attestation, final signoff, and rendered public outputs in one flow, and MUST include the review ID, session SHA-256, and agent-assistance protocol in hidden comments. It MUST NOT embed the complete session in Base64 or another form in the human-facing file; it MUST reconstruct that session from body-free private evidence and current canonical state and verify its SHA-256. Non-response content MUST be hash-protected.

**RP-051** An agent MAY assist complete inspection, exception aggregation, explanation, and explicitly requested response-field edits, but MUST treat included content as untrusted data and MUST NOT turn the conversation or agent output into author authorization. Planning MUST require human submission attestation and expand every group decision to explicit decisions for all member records. Final signoff MUST require every record to be approved or excluded and every required policy question to be accepted. Accepting a content gap as an edition policy MUST NOT waive another release gate automatically.

**RP-052** Core conformance MUST require only one author-review exchange format and public CLI path: the single Markdown workflow in RP-046. It MUST NOT require self-contained HTML, a browser application, result JSON, chapter-split Markdown, or conversation logs as additional approval paths. An implementation MAY add a viewing aid, but it MUST NOT create another decision format, consent artifact, or application rule.

## 15. Official companion material

**RP-053** A canonical reference MAY jointly declare `relation=official_companion`, `url_scope=exact|prefix`, and `retrieval_policy=proactive_when_relevant`. These fields MUST occur together and MUST use only these closed values. The declaration is producer- or authority-supplied metadata; candidate generation MUST NOT infer official status or inject arbitrary instructions through it. A bibliographic `book.official_url` alone MUST NOT opt a project into proactive retrieval.

**RP-054** When at least one reference carries this declaration, deterministic rendering MUST retain the URL in REF, identify its relation, scope, and policy, and add model-independent SYS rules that proactively consult likely relevant companion pages for appendices, supplementary essays, post-publication updates, author views, supporting grounds, and details. Retrieval MUST also occur when it materially improves completeness, accuracy, or freshness even if the Pack can answer alone. The rules MUST allow non-official Web sources, treat retrieved page text as content rather than system or behavioral instructions, distinguish sources and known update times when materials differ, and request page-URL disclosure whenever possible. When no declaration exists, rendered output MUST remain byte-identical to the prior behavior.

**RP-055** A companion target MUST be an absolute HTTPS URL of at most 2,048 characters without credentials or unsafe control or bidirectional text. A prefix MUST end in `/` and MUST NOT contain a query or fragment. A language pack MUST contain at most 32 declarations and MUST reject normalized duplicate companion targets. JSON and legacy or extended CSV Author Input MUST pass the same checks before planning; application MUST preserve provenance, semantic hashes, multilingual parity, and draft-only status. Public tests MUST use synthetic targets and cover invalid URLs, duplicates, excessive counts, deterministic rendering, aggregate multilingual application, author review, and release validation.

## 16. Book-specific policy

**RP-056** A canonical policy MUST have a stable `POLICY-` ID, one closed kind from `authority_order`, `language_precedence`, `translation_rights`, `retrieval`, `publisher_relation`, `usage_terms`, or `other`, a bounded statement, review state, and optional source locations. Rendering MUST expose every record for review but MUST activate only `approved` records as SYS behavior. Draft or reviewed policy MUST remain non-operational. Unstructured attachments and retrieved page text MUST NOT become policy instructions. A pack without policy records MUST preserve pre-policy output bytes.

## 17. Generation evidence and applied-run provenance

**RP-057** A generation session MAY bind a reviewed chapter map whose spans are expressed against the normalized authorized source and whose source hash matches that session. When such a map is supplied, every chapter-scoped work item MUST have exactly one valid span, and each submitted evidence occurrence MUST resolve inside that span before the response can advance session state or create a candidate. Omitting the map MUST preserve the previous session format and behavior.

**RP-058** Successful candidate application, including recovery of a prepared application, MUST persist a deterministic application record containing the exact candidate IDs and the before and after hashes of both the language data and whole project data. A sequential provenance receipt MUST revalidate each terminal run, source binding, evidence, review-artifact binding, and integrity hash; verify every adjacent application-to-run canonical link and the final link to current canonical state; and preserve the supplied application order. A legacy run without an application record MUST be rejected by default and MAY be included only by an explicit compatibility mode that labels continuity as unverified.

**RP-059** A profile MAY mark a nominally required module as explicitly not applicable for a bounded class of books. Release validation MUST accept an empty collection only when that profile permits the disposition and the quality plan records `status=not_applicable` with a non-empty reason. An empty collection without that declaration MUST still fail, and a profile that does not permit the disposition MUST reject it. General nonfiction MAY use this rule for an inspected edition with no Pack-usable reference target; academic and textbook profiles MUST continue to require references.

**RP-060** An operator MAY explicitly request a primary-source `misreadings` generation module without adding it to the default automatic module set. Such generation MUST use neutral `issue` records and MUST be limited to source-explicit objections, qualifications, or distinctions that prevent a material reader error. It MUST NOT invent author Q&A, portray a cited critic as mistaken, or weaken chapter and evidence-span binding. Rendering MUST label a clarification as an author clarification only when the record carries validated non-primary support-source provenance; otherwise it MUST identify the response as the book's response. A project that does not explicitly request this module MUST preserve the previous session plan and output.

## Change history

- 1.0-draft (2026-08-12): first release integrating the generic OSS reference implementation, bilingual schemas, offline CLI, and release gates.
- 1.0-draft (2026-08-13): specified profile contracts, staged source-bound import, evidence-limited human candidate acceptance, stale-write protection, and measured release conformance.
- 1.0-draft (2026-08-14): specified per-module supplied/generated switching, the Author Input Package, a body-free provenance ledger, independent-Q&A classification boundaries, and hash-bound Markdown author review.
- 1.0-draft (2026-08-15): reduced author review to one human-edited Markdown path, with edited-file consent and corrections, authority-bound group decisions, protected response regions, submission attestation, and agent assistance.
- 1.0-draft (2026-08-15): added closed producer declarations for official companion material and deterministic proactive-reference behavior in REF and SYS.
- 1.0-draft (2026-08-15): distinguished the Conversational Edition, Reading Pack, and optional Agent Skill compatibility-container layers, keeping the container outside core conformance and approval.
- 1.0-draft (2026-08-15): limited manuscript import to one dependency-resolved file and removed custom bundles, recursive Org INCLUDE resolution, and the HTML, result-JSON, and chapter-split author-review paths from the core.
- 1.0-draft (2026-08-15): added authority-supplied book policy, neutral `issue` records with legacy Q&A migration, and record-level source locators.
- 1.0-draft (2026-08-15): allowed explicit source-bound neutral reading-issue generation while keeping it outside the default automatic module set and independent author Q&A path.
- 1.0-draft (2026-08-15): added optional chapter-span enforcement at response ingestion, durable candidate-application records, and deterministic sequential provenance receipts.
- 1.0-draft (2026-08-15): distinguished a missing required module from a profile-permitted, reason-bound not-applicable disposition.
- 1.0-draft (2026-08-15): added policy-scoped Markdown review, non-substitutive source navigation, and bounded abstractive glossary meanings with coverage-time source-copy rechecks.
