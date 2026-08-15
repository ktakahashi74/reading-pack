# Reading Pack

Reading Pack is a toolkit for making a book easier to explore with AI. It distinguishes three layers:

- A **Conversational Edition** is the reader experience: exploring a book through dialogue with AI.
- A **Reading Pack** is the technical artifact: one human-readable Markdown file generated from author- or editor-reviewed data.
- An **Agent Skill** is an optional compatibility container that bundles the existing Reading Pack for hosts that support the Agent Skills format. It is not canonical data, a replacement for the Reading Pack, or a new approval.

A reader can give the Reading Pack to an AI chat and ask where the book discusses a topic or how the author frames a claim.

The pack is a guide back to the book. It deliberately omits the sequence of argument, examples, metaphors, and prose that would let it become a substitute for reading the original. Technical checks and human approval are kept separate to preserve that boundary.

Reading Pack supports Japanese, English, and bilingual projects. It runs on Python 3.11 or newer and uses `jsonschema` for Draft 2020-12 validation. The core workflow needs no API key and makes no network requests.

[日本語版 README](README.ja.md)

## What it does

The toolkit has four main jobs.

1. Import chapter structure and publication metadata from Markdown, Org mode, EPUB3, PDF, and plain text.
2. Bind proposed summaries and other records to exact source evidence, then record each review decision separately.
3. Keep Japanese and English records aligned and detect translations made stale by a primary-language change.
4. Rebuild the same Markdown from canonical data and detect manual edits or missing publication approvals.

Seven quality profiles cover different kinds of books and uses. Conformance depends on every critical requirement, including rights, content, translation, and non-reconstruction. A high score in one area cannot hide a failure in another.

## Installation

Run the following commands from a checkout:

```sh
python3 -m pip install --no-deps --no-build-isolation \
  --target .reading-pack-site .
export PYTHONPATH="$PWD/.reading-pack-site"
export PATH="$PWD/.reading-pack-site/bin:$PATH"
reading-pack --version
```

This remains offline when setuptools is already available locally. You can use a virtual environment instead when your operating system provides `venv`.

## Try it in five minutes

Create an English project and import its structure:

```sh
reading-pack init my-book-pack \
  --title "My Book" \
  --author "Author Name" \
  --lang en \
  --profile nonfiction-reading

reading-pack import-plan manuscript.md --output /tmp/import-plan.json
reading-pack import-apply /tmp/import-plan.json \
  --source manuscript.md --project my-book-pack --lang en

reading-pack validate --project my-book-pack
reading-pack build --project my-book-pack --lang en
reading-pack check --project my-book-pack --lang en
```

`import-plan` does not change canonical data. Review its diagnostics and chapter structure before applying it as a draft with `import-apply`. Add summaries, claims, people, terms, corrections, and references to `my-book-pack/data/pack.en.json`. Files under `dist/` are generated and should not be edited.

The [English quickstart](docs/quickstart.en.md) gives a clean-directory walkthrough. Read [Core concepts](docs/concepts.en.md) first if you want the design rationale.

To package already-built, current Reading Packs for an Agent Skills-compatible host, see [Agent Skills distribution](docs/agent-skills.en.md). This optional step does not alter the normal packs or their approvals.

## Manuscript handoff

Import accepts one file whose dependencies have already been resolved. Markdown, Org, EPUB3, plain text, and PDF can be used directly. Reading Pack does not define a custom manuscript bundle or a cook command whose only job is to create one.

Convert DOCX or RTF with the author's normal writing tool or a conversion-assisting agent to Markdown, EPUB3, or plain text before handoff. EPUB3 and PDF are already single files and can be handed over directly. If an Org manuscript uses `#+INCLUDE`, expand it with Org itself into one file or export it to EPUB3 first. Import stops on `#+INCLUDE` instead of silently dropping included material.

These are transport formats, not the Reading Pack canonical format. Canonical data remains `reading-pack.toml` plus `data/pack.<lang>.json`. Publisher XML such as BITS is not added as an internal canonical format or mandatory input.

## Importing PDF

PDF import is available when the local Poppler commands `pdfinfo` and `pdftotext` are installed.

```sh
reading-pack import-plan typeset-book.pdf --output /tmp/import-plan.json
reading-pack import-apply /tmp/import-plan.json \
  --source typeset-book.pdf --project my-book-pack --lang en
```

PDF results always require human review. Scans, untagged files, and complex multi-column layouts may need a manually checked outline. The importer never treats an arbitrary body line as a chapter heading.

For a Japanese vertical-text layer emitted one glyph per line, select the format explicitly:

```sh
reading-pack import-plan vertical-book.pdf --format pdf-vertical \
  --outline-sidecar outline.json --output /tmp/vertical-import-plan.json
```

This keeps the original PDF as the source of record and reconstructs only Poppler's reading order. It is not OCR. Subset-font errors can remain, so compare the outline with the page image.

## Resumable generation from an unstructured book

After applying a reviewed structure plan, the producer plugin can turn AIP `generate`/`augment` declarations and still-unfilled modules into bounded chapter or book work items. It manages hashes, scope, response validation, progress, and candidate creation; it does not call a particular model or grant approval.

```sh
reading-pack work plan --project my-book-pack --lang en \
  --session-directory my-book-pack/.reading-pack/generation/session-001 \
  --source book.pdf
reading-pack work next my-book-pack/.reading-pack/generation/session-001 \
  --project my-book-pack --source book.pdf > /tmp/work-request.json
# An external agent returns one response conforming to the included standalone Schema.
reading-pack work ingest my-book-pack/.reading-pack/generation/session-001 \
  /tmp/work-response.json --project my-book-pack --source book.pdf
# Close a supported zero-result item without constructing an external JSON file.
reading-pack work close my-book-pack/.reading-pack/generation/session-001 \
  --project my-book-pack --source book.pdf \
  --outcome no_supported_candidate --reason no_explicit_source_support
reading-pack work status my-book-pack/.reading-pack/generation/session-001 --json
reading-pack work finalize my-book-pack/.reading-pack/generation/session-001 \
  --project my-book-pack --source book.pdf \
  --run-directory my-book-pack/.reading-pack/runs/generated-001
```

Repeat `next` and either `ingest` or `close` until the session is ready to finalize. Each response is bound to the session, project configuration, source and canonical hashes, work ID, module, scope, and chapter range. For a zero-result item, an agent can call `work close` with `no_supported_candidate` or `skipped` and a machine-readable reason. The command constructs the same bound response internally and sends it through the ordinary ingest validation, without an external JSON file or custom adapter. It deliberately cannot record `failed`, so processing failures are not mistaken for content judgments. The primary-book session cannot invent author Q&A, and it should report no candidate rather than infer an unexpressed certainty system or official reference. Finalization uses the ordinary evidence verifier and candidate run; acceptance, draft application, author approval, rights, and release remain separate steps. See [Quality pipeline](docs/quality-pipeline.en.md) for storage, adapter, and failure boundaries.

If evidence verification fails only at finalization, inspect the diagnostic and explicitly return that one response for regeneration with `reading-pack work retry SESSION --id WORK_ID --project PROJECT --source SOURCE`. Duplicate ingestion never overwrites it implicitly.

After the reviewed initial candidates have been applied as drafts, run a second session with `--purpose coverage`. This optional pass uses a fixed structured rubric for summaries, chapter terms, claims, people, and glossary entries. Every chapter/module scope must end with either an evidence-bearing improvement candidate or an explicit zero result, so omission review is no longer inferred from the number of accepted candidates. Glossary meanings are abstractive summaries of at most 500 characters; an existing source-copy risk, including one supplied through AIP, must return as a replacement candidate and cannot be closed with a zero result. The request includes an excerpt-free baseline inventory and a hash-bound locator for the current canonical data; project input cannot inject arbitrary prompt text. For high-recall people and term discovery, keep using `catalog candidates --responses` with an explicit reviewed chapter map, then use `catalog context-plan --refresh-existing` and `catalog context-candidates` for book-specific descriptions.

```sh
reading-pack work plan --project my-book-pack --lang en --purpose coverage \
  --session-directory my-book-pack/.reading-pack/generation/coverage-001 \
  --source book.pdf \
  --chapter-map my-book-pack/.reading-pack/catalog-chapter-map.json
```

When `--chapter-map` is supplied, the reviewed normalized-text spans are bound
into the session and each evidence occurrence is rejected at ingest if it lies
outside the requested chapter. Omitting the option preserves the legacy
session bytes and defers ordinary source evidence validation to finalization.

After several sequential runs have been applied, create one compact handoff
receipt in application order. New runs retain their CAS before/after hashes and
produce fully verified continuity; `--allow-legacy` is required for older runs
that lack that durable application record and marks those links unverified.

```sh
reading-pack candidates receipt --project my-book-pack --lang en \
  --artifact my-book-pack/.reading-pack/runs/generated-001 book.pdf \
  --artifact my-book-pack/.reading-pack/runs/coverage-001 book.pdf \
  --output generation-chain.json
```

## Building a bilingual pack

Keep both languages in one project:

```sh
reading-pack init my-bilingual-pack \
  --title "書名" \
  --author "Author Name" \
  --lang ja --lang en \
  --primary-language ja

reading-pack import-plan manuscript.ja.org --output /tmp/import-ja.json
reading-pack import-apply /tmp/import-ja.json \
  --source manuscript.ja.org --project my-bilingual-pack --lang ja
reading-pack import-plan manuscript.en.epub --output /tmp/import-en.json
reading-pack import-apply /tmp/import-en.json \
  --source manuscript.en.epub --project my-bilingual-pack --lang en

reading-pack validate --project my-bilingual-pack
reading-pack build --project my-bilingual-pack --lang all
reading-pack check --project my-bilingual-pack --lang all
```

Changing a primary-language record raises `RP202` for each stale translation. After a person updates and checks the translation, record the new source hash:

```sh
reading-pack link-translations --project my-bilingual-pack --lang en
```

This resets the translation to `draft`. It does not approve the new wording. A person must review it before changing the record to `approved`.

## Reviewing candidates safely

AI-generated or externally prepared material should not write directly to canonical data. Reading Pack quarantines each candidate run under `.reading-pack/runs/` and binds its records to exact source spans. Automated checks can move a candidate only as far as `ready_for_review`.

```sh
reading-pack candidates create responses.json \
  --run-directory my-book-pack/.reading-pack/runs/run-001 \
  --source manuscript.md --project my-book-pack --lang en
reading-pack candidates verify my-book-pack/.reading-pack/runs/run-001 \
  --source manuscript.md
reading-pack candidates review my-book-pack/.reading-pack/runs/run-001 \
  --source manuscript.md --project my-book-pack --output review.html
reading-pack candidates accept my-book-pack/.reading-pack/runs/run-001 \
  --id CANDIDATE_ID --reviewer "Editor Name"
reading-pack candidates apply my-book-pack/.reading-pack/runs/run-001 \
  --source manuscript.md --project my-book-pack --lang en \
  --id CANDIDATE_ID
```

Normal candidate apply still places an accepted candidate into canonical data as `draft`. Editorial selection and final author approval are separate decisions. When the author is deciding an exact QA-passed replacement directly, pass the paired language runs to `review export --candidate-run`. It creates an unsubmitted Markdown form containing only the target records; after the author inspects and signs it, `revise_approve` applies and approves the exact revision in one transaction. See [Private review](docs/private-review.en.md) for reviewing several runs in one read-only page, and [Quality pipeline](docs/quality-pipeline.en.md) for the limits of evidence checks.

When an author or publisher already has structured material, use an [Author Input Package](docs/author-input.en.md). Each module can replace the current set, augment it, remain assigned to generation, or be intentionally omitted. The package can carry closed book-specific policy, neutral reading issues, and record-level source locators; legacy Q&A using `misreading` remains readable. A bilingual plan can apply one package per language under one lock and derives translation links from the prospective primary-language data.

An authority-supplied reference can also declare closed `official_companion` and `proactive_when_relevant` semantics. The normal build then emits its URL in REF and fixed, model-independent proactive-reference rules in SYS; projects without that declaration render exactly as before.

For final decisions on applied canonical records, use [agent-assisted Markdown author review](docs/author-review.en.md). One human-readable file contains evidence groups, individual exceptions, policy questions, corrections, and signoff. Use `--module policy` for policy alone or `--record RECORD_ID` for one focused decision. An agent may inspect everything, extract exceptions, explain recommendations, import candidate-run suggestions, and help fill the form, but the edited Markdown itself is the human consent and correction evidence. Canonical data, translation links, and AIP provenance are rechecked before a body-free plan is applied. This is the only author-review exchange format.

## Implementation boundary

`reading_pack` is an approximately 6,600-line kernel for import, canonical data, validation, rendering, and shared transactions. `reading_pack_review` is the standard workflow for Author Input Packages and single-Markdown author review. The optional `reading_pack_producer` plugin owns catalog extraction, candidate generation, work ledgers, private candidate views, and Agent Skill distribution. The current distribution bundles all three for compatibility, but the core CLI loads the producer plugin lazily and can still import, validate, build, check, and run author review when that plugin is absent.

Author Input Package and author-review multi-file changes use one shared artifact transaction layer. That layer alone owns before/after hashes, allowed relative paths, prepared records, atomic writes, and rollback after validation failure or interruption. Feature modules remain responsible only for their decisions and plan validation.

## Main commands

| Command | Purpose |
|---|---|
| `reading-pack init` | Create canonical data and templates. |
| `reading-pack import-plan` | Produce a body-free structure proposal. |
| `reading-pack import-apply` | Apply a reviewed structure proposal as draft data. |
| `reading-pack work plan/next/ingest/close/status/retry/finalize` | Run a resumable, model-neutral bounded generation session. |
| `reading-pack candidates ...` | Use the producer plugin to create, inspect, decide, and apply evidence-bound candidates. |
| `reading-pack catalog ...` | Use the producer plugin for people, term, and reference candidates. |
| `reading-pack review bundle` | Use the producer plugin to combine candidate runs in one read-only review page. |
| `reading-pack review export/status/plan/apply` | Export, validate, plan, and apply one human-edited Markdown review. |
| `reading-pack author-input ...` | Prepare, plan, apply, and audit authority-supplied data. |
| `reading-pack validate` | Check schemas, IDs, references, parity, and translation freshness. |
| `reading-pack build` | Generate a pack for one or all languages. |
| `reading-pack check` | Compare generated output with canonical data. |
| `reading-pack check --release` | Add the human publication gates to technical checks. |
| `reading-pack agent-skill build` | Use the producer plugin to package packs as an Agent Skill directory and ZIP. |
| `reading-pack agent-skill check` | Use the producer plugin to check that directory and ZIP. |
| `reading-pack doctor` | Diagnose the local environment and project files. |

Exit status `0` means success, `2` a command error, `3` invalid canonical data, `4` a filesystem or environment problem, and `5` missing or mismatched generated output.

## Decisions that remain human

`validate` and an ordinary `check` establish technical consistency. Publication requires seven separate human decisions.

1. The design constraints are approved.
2. Rights have been checked for headings, summaries, indexes, and other book-derived material.
3. The author has approved every public record.
4. The publisher has approved the pack, or a person has determined that publisher review is not required.
5. The public material as a whole cannot reconstruct a substitute for the original book.
6. Measured results meet the quality thresholds fixed in advance.
7. A person has made the publication decision.

`reading-pack check --release` verifies that these decisions are recorded against the current canonical data. It does not make them. See [Rights and review](docs/rights-and-review.en.md) for the full boundary.

## Security boundary

The core toolkit does not send manuscripts outside the local machine. If you use an external AI service, separately check the publishing agreement, confidentiality duties, retention and training terms, data location, and account settings.

An exact evidence match proves that a source span exists; it does not prove that the proposed interpretation is correct. Checksums detect accidental changes but are not signatures or identity checks. PDF processing invokes Poppler as an external parser, so process only inputs whose origin and parser risk you accept.

See [Quality pipeline](docs/quality-pipeline.en.md), [Rights and review](docs/rights-and-review.en.md), and [SECURITY.md](SECURITY.md) for details.

## Development

The full test path runs offline:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  python3 -m unittest discover -s tests -v

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  python3 -m compileall -q src tests

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  python3 -m reading_pack check \
  --project examples/clockwork-garden --lang all --release
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for contribution rules, the [English specification](spec/reading-pack-spec.en.md) for normative requirements, and [Production workflow](docs/workflow.en.md) for the complete W0–W13 process.

## License

- Python code, the CLI, validators, and tests are under the MIT License.
- Specifications, documentation, schemas, prompts, and READMEs are under CC BY 4.0.
- The synthetic Clockwork Garden example is under CC0 1.0 Universal.
- This repository does not impose a license on a user's manuscript, structured project data, or generated pack. The relevant rights holder chooses those terms.

See the [license map](LICENSES/README.md) for path-level details.

## Status

The current toolkit version is v0.4.0. The public specification remains `1.0-draft`, and compatibility between draft revisions is not guaranteed. See [CHANGELOG.md](CHANGELOG.md) for release history.

Copyright 2026 Koichi Takahashi / 高橋恒一. Documentation licensed under CC BY 4.0.
