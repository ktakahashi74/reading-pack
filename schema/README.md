# Schemas

These schemas are the machine-readable contract of the `reading-pack` reference implementation. They are not the serialization of the final Markdown artifact and are not required for independent [Reading Pack Format](../spec/reading-pack-format-spec.en.md) conformance. The [Production Standard](../spec/reading-pack-production-standard.en.md) is tool-neutral; a producer using this toolkit uses these schemas as its implementation evidence.

The schemas define three boundaries: canonical project data, body-free planning records, and private review records. The table below is the shortest route to the right file.

| Schema | What it describes |
|---|---|
| `project.schema.json` | The project object after `reading-pack.toml` is parsed. |
| `language-pack.schema.json` | Canonical `data/pack.<lang>.json`. |
| `quality-plan.schema.json` | The selected quality profile, human authority, critical policies, measured thresholds, and optional no-regression floor. |
| `source-plan.schema.json`, `source-registry.schema.json` | Body-free identities for the primary book and supporting sources. |
| `import-plan.schema.json` | A body-free structure proposal made before canonical mutation. |
| `manual-outline.schema.json` | Human-checked, source-bound structure recovery for difficult layouts. |
| `candidate-run.schema.json`, `evidence-ref.schema.json` | Private candidate records and excerpt-free evidence references. |
| `provenance-receipt.schema.json` | Ordered terminal applied runs, durable CAS transitions, and their binding to the current canonical state. |
| `generation-ledger.schema.json`, `generation-results.schema.json` | Planned work coverage and the outcome of each generation task. |
| `generation-session.schema.json`, `generation-response.schema.json` | Resumable, source-bound generation sessions, optional fixed post-draft coverage rubrics, and standalone bounded agent responses. |
| `semantic-findings.schema.json`, `semantic-review.schema.json` | Excerpt-free semantic findings, run binding, and named-human adjudication. |
| `catalog-inventory.schema.json` | A private source-bound inventory of people, subject terms, and references. |
| `catalog-context-plan.schema.json`, `catalog-context-responses.schema.json` | Complete plans and evidence-bearing responses for book-specific people and term descriptions. |
| `author-qa.schema.json`, `qa-plan.schema.json` | Structured four-facet author Q&A and its body-free staging form. |
| `author-input-manifest.schema.json` | The supplied, augmented, generated, or omitted mode selected for every module. |
| `author-input-module.schema.json` | JSON records supplied through an Author Input Package, including closed policy, neutral reading issues, and record locators. |
| `author-input-plan.schema.json` | A body-free, stale-checked plan for applying one or more packages. |
| `author-input-state.schema.json` | Canonical provenance and history for applied packages. |
| `author-review-manifest.schema.json` | Body-free record and project-state hashes used to validate one human-edited Markdown review, optionally including aggregate release signoff. |
| `author-review-plan.schema.json` | Body-free, stale-checked author decisions, prospective canonical hashes, and optional release/quality bindings. |
| `author-review-state.schema.json` | Body-free history that overlays explicit author corrections on AIP provenance and records any aggregate release decision. |

These 29 files are the sole structural source of truth. The command-line tool loads them with `jsonschema.Draft202012Validator` and consumes every result from `iter_errors()` through one deterministic error adapter. It separately performs semantic checks that JSON Schema cannot express conveniently: globally unique IDs, valid references, safe URL policy, bounded and deduplicated official-companion targets, bilingual parity, source and canonical-state hashes, provenance and work-to-candidate binding, applied-run continuity, finding-to-evidence binding, state transitions, named reviewer decisions, and human publication gates.

Checksums detect stale or modified files. They are not digital signatures and do not authenticate a reviewer.

CSV headers and array delimiters for Author Input Packages are documented in [`docs/author-input.en.md`](../docs/author-input.en.md) and [`docs/author-input.ja.md`](../docs/author-input.ja.md).

Copyright 2026 Koichi Takahashi / 高橋恒一. Licensed under CC BY 4.0.
