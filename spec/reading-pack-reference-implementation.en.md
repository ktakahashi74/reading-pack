PROFILE | name=reading-pack Reference Implementation Profile | version=0.6.0 | status=alpha | language=en | primary=false | date=2026-08-22 | author=Koichi Takahashi | code_license=MIT | document_license=CC BY 4.0

# reading-pack Reference Implementation Profile 0.6.0 (alpha)

This document describes the public contract of the Python implementation in this repository. It is not a condition for another implementation to claim Reading Pack format or production conformance. The Japanese version is canonical.

## 0. Implementation role

**RPI-001** `reading-pack` is an offline-first reference implementation for creating artifacts under the [Reading Pack Format Specification](reading-pack-format-spec.en.md) through a process aligned with the [Reading Pack Production Standard](reading-pack-production-standard.en.md). Use of the toolkit and conformance with either standard are displayed separately.

**RPI-002** Core operation requires no network connection, API key, or particular AI provider. An external agent or local model adapter remains subject to its own runtime, terms, and confidentiality boundary.

## 1. Project format

**RPI-003** A project is based on `reading-pack.toml`, `data/pack.<lang>.json`, `templates/pack.<lang>.md`, and `dist/`. It may add `quality-plan.json`; tool-managed private state is stored under `.reading-pack/`.

**RPI-004** The Draft 2020-12 schemas under `schema/` are the structural source of truth for JSON. A shared validator performs schema validation and adds semantic checks for URLs, hashes, provenance, state transitions, and multilingual correspondence. `RP`, `QP`, and similar CLI identifiers are implementation diagnostic codes.

**RPI-005** Language-specific canonical data manages chapters, certainty, claims, reading issues, policies, people, glossary terms, and references under stable IDs. A bilingual project checks shared IDs, ordering, and translation freshness through semantic hashes of source-language records.

## 2. Input boundary

**RPI-006** Direct import accepts one dependency-resolved local file. It supports UTF-8 Markdown, Org mode, and EPUB3 and also provides plain-text, ordinary PDF, and vertical-PDF adapters. Core import does not recursively resolve directories, custom bundles, Org `#+INCLUDE`, DOCX, RTF, or LaTeX dependencies.

**RPI-007** Import may move only bounded bibliography, headings, locations, and diagnostics into canonical data, not body text. EPUB uses standard ZIP/XML structures; PDF uses local Poppler. The implementation rejects DRM circumvention, external transmission, path traversal, unsafe XML entities, encrypted PDFs, and oversized input.

**RPI-008** `pdf-vertical` is selected explicitly and is not inferred automatically. PDF chapter structure, external characters, spreads, and printed page numbers require human review.

## 3. Public CLI

**RPI-009** The Python 3.11+ `reading-pack` CLI provides at least `init`, `import-plan`, `import-apply`, `validate`, `build`, `check`, `doctor`, `review export|status|plan|apply`, and `delivery build|check|measure|probes`. Commands return purpose-specific nonzero exit codes and explainable diagnostics.

**RPI-010** `init` rejects a non-empty destination by default. A canonical mutation separates a read-only plan from explicit application and does not overwrite existing files unconditionally.

**RPI-011** `validate` checks schemas, IDs, references, language correspondence, translation freshness, and size limits. `build` renders a Pack from canonical data, and `check` compares the current artifact byte-for-byte with a fresh rendering. `check --release` also checks production release gates but does not make human decisions.

## 4. Authority and producer boundaries

**RPI-012** The core library can import, validate canonical data, build, check byte reproducibility, and conduct single-Markdown author review without producer features. Author Input Packages and author review are authority workflows separated from the generation kernel.

**RPI-013** Catalog extraction, candidate generation, private display, generation sessions, provenance receipts, and Agent Skill distribution are optional producer functions. They are loaded lazily by the CLI without a direct core-to-producer dependency.

**RPI-014** For a book without structured input, the implementation provides resumable module/scope `work plan/next/ingest/close/status/retry/finalize` sessions. Responses follow a standalone public schema and are bound to the source, project, work ID, and chapter span.

## 5. Transaction and safety boundaries

**RPI-015** Operations changing multiple canonical artifacts share one transaction layer for before/after hashes, permitted paths, project locking, `prepared` records, atomic file replacement, and rollback after validation failure or interruption. They do not claim atomicity across the whole file system.

**RPI-016** A local model adapter bounds input, output, and runtime and invokes a configured executable without a shell. This boundary does not sandbox the executable or prevent local file access or networking, so the adapter executable is treated as trusted code.

**RPI-017** Candidate evidence, review forms, plans, and application records are bound to current source and canonical hashes and reject stale, cross-project, duplicate, out-of-scope, altered, or oversized input. Hashes detect freshness and accidental damage within a cooperative process; they are not electronic signatures, identity proof, or protection against hostile mutation.

## 6. Distribution and verification

**RPI-018** An Agent Skill directory and ZIP are optional deterministic distributions containing existing built Packs. They are not new canonical sources, format-conformance conditions, or production approval units.

**RPI-019** Public tests are offline, use synthetic material only, and cover schemas, diagnostic compatibility, byte reproducibility, bilingual correspondence, transaction rollback, path boundaries, and long-copy prevention. Live-model evaluation is not a mandatory CI gate.

**RPI-020** Implementations of this profile MAY display `Built with reading-pack toolkit 0.6.0`. This does not replace a format- or production-conformance claim.

**RPI-021** A Delivery Adapter is an optional deterministic derivative of a completed canonical Pack, not a canonical source, format-conformance condition, or approval unit. `delivery check` verifies canonical freshness, byte-identical aliases, manifests, markers, version and language binding, budgets, and exact reconstruction of every component. An over-budget artifact fails without truncation, and the command itself performs no publication.

## 7. Change management

The profile version follows the toolkit version. A change to the CLI, schemas, project format, transactions, or package boundaries updates code, bilingual documents, tests, and the synthetic example in the same release.

Copyright 2026 Koichi Takahashi. Document licensed under CC BY 4.0; implementation licensed under MIT as mapped in `LICENSES/README.md`.
