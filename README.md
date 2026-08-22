# Reading Pack

[日本語版 README](README.ja.md)

Reading Pack is an open-source toolkit for creating compact guides that help readers explore books with AI. A reader attaches the generated Markdown to an AI chat and asks where the book discusses a topic, how the author frames a claim, or which source supports an explanation.

A Reading Pack points the reader back to the original book. It is not a reproduction or compressed substitute. The toolkit builds it reproducibly from structured data reviewed by an author or editor, then detects manual changes, stale translations, and missing publication approvals.

> **Status:** The toolkit is v0.6.0 (alpha). The Format Specification and Production Standard are `1.0-draft`; the Production Standard is currently designated beta. Python 3.11–3.14 is tested. Incompatible changes remain possible during the draft period.

## Public standards

Reading Pack separates three concerns. The first is the Markdown artifact delivered to readers. The second is the process used to produce and approve it. The third is the reference implementation in this repository. Format conformance does not require this Python toolkit or the same internal design.

- [Reading Pack Format Specification 1.0-draft](spec/reading-pack-format-spec.en.md) defines the structure and meaning of the single Markdown artifact.
- [Reading Pack Production Standard 1.0-draft (beta)](spec/reading-pack-production-standard.en.md) defines Levels 1–3, W0–W13, evidence, author review, evaluation, and publication gates.
- [reading-pack Reference Implementation Profile 0.6.0 (alpha)](spec/reading-pack-reference-implementation.en.md) documents this toolkit's project layout, CLI, import, transaction, and plugin boundaries.

Koichi Takahashi authored the Format Specification and Production Standard in 2026 and publishes them under CC BY 4.0. They may be modified, independently implemented, and used in commercial Reading Pack production services. The [standards-suite overview](spec/reading-pack-spec.en.md) explains the relationship among the three documents and gives suggested citations.

## Try it first

The repository includes a complete example based on the entirely fictional *Clockwork Garden*.

- [English Reading Pack](examples/clockwork-garden/dist/clockwork-garden-reading-pack.en.md)
- [Japanese Reading Pack](examples/clockwork-garden/dist/clockwork-garden-reading-pack.ja.md)
- [Example project with canonical data](examples/clockwork-garden/)

Attach a generated Pack to an AI chat without adding a question. After the loading response, try asking:

- “What does chapter 2 discuss?”
- “Where is the lunar mechanism defined?”
- “Is this a story fact or an interpretation?”

## Who this is for

- Authors, editors, and publishers adding an AI dialogue experience to a book.
- Production teams using AI-assisted drafting while retaining traceable evidence and human decisions.
- Developers integrating a reviewed book guide with AI chats or Agent Skills-compatible hosts.

This repository is for producers. Readers normally receive one generated Markdown file. The producer retains the project and source manuscript.

## From source to reader

The canonical source is the structured data and configuration from which the distributed Markdown can always be rebuilt.

```text
manuscript + author/editor input
              |
              v
reviewed canonical JSON and project settings
              |
              v
evidence-bound candidates -> human review and approval
              |
              v
reproducible Reading Pack Markdown
              |
              v
AI chat or optional Agent Skill
```

The project uses three related terms:

| Term | Meaning |
|---|---|
| Conversational Edition | The experience of reading a book through dialogue with AI. |
| Reading Pack | The generated, human-readable Markdown guide delivered to readers. |
| Agent Skill | An optional container that delivers an existing Reading Pack to a compatible host. It adds no new canonical source or approval unit. |

## What it provides

- Imports chapter structure and publication metadata from Markdown, Org mode, EPUB3, PDF, and plain text.
- Keeps the editable canonical source separate from reproducible output.
- Binds AI-generated and externally prepared candidates to exact source evidence before review.
- Manages English, Japanese, and bilingual projects under stable IDs and detects stale translations after source-language changes.
- Rebuilds the same Markdown bytes from unchanged input and detects direct edits to generated files.
- Applies one of seven book- and use-specific quality profiles, checking every mandatory gate separately.
- Packages an existing Reading Pack for Agent Skills-compatible hosts when requested.

## Deliberate boundaries

- The core toolkit calls no particular AI model and requires no API key.
- It sends no manuscript over the network. An external AI used for candidate drafting remains subject to that service's terms and settings.
- A source-text match alone does not establish that a candidate interpretation is correct.
- Author approval, rights review, publisher disposition, and publication approval are never automated.
- The toolkit grants no rights in a manuscript, project data, or generated Pack.
- It does not create a substitute for the original book or reconstruct unprovided book text.

## Installation

Create a virtual environment and install the toolkit with its `jsonschema` runtime dependency:

```sh
git clone https://github.com/ktakahashi74/reading-pack.git
cd reading-pack
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
reading-pack --version
```

Installation may contact a package index if a dependency is not already available. Once installed, core operations run locally without network access. PDF import also uses the local Poppler commands `pdfinfo` and `pdftotext`.

## Rebuild the included example

The *Clockwork Garden* project records every human release gate for a synthetic book.

```sh
reading-pack build --project examples/clockwork-garden --lang all
reading-pack check --project examples/clockwork-garden --lang all --release
reading-pack agent-skill check --project examples/clockwork-garden --release
```

These commands confirm that the English and Japanese Packs, optional Agent Skill directory, and ZIP match a fresh build from the canonical source byte for byte.

### Optional delivery adapters

The complete Markdown file remains the primary, portable artifact. After that file passes `check`, producers may build transport-specific copies, an experimental Web-lazy bundle, and `web-core-index-v2` one-touch core plus question-routed lazy modules in a separate directory:

```sh
reading-pack delivery measure --project examples/clockwork-garden --lang all --json
reading-pack delivery build --project examples/clockwork-garden --lang all \
  --base-url https://staging.example/reading-packs/clockwork-garden \
  --output /tmp/clockwork-garden-delivery
reading-pack delivery check --project examples/clockwork-garden --lang all \
  --base-url https://staging.example/reading-packs/clockwork-garden \
  --output /tmp/clockwork-garden-delivery
reading-pack delivery probes --output /tmp/reading-pack-probes
```

These commands call no model and publish nothing. `build` fails if the canonical Pack is stale, if a record would need to be split, or if a byte, character, or part budget is exceeded. A Web adapter is advertised only after dated, product-specific testing; failure never invalidates the complete Pack. See the [portable-first delivery strategy](docs/reading-pack-delivery-strategy.ja.md), [Web adapter design](docs/reading-pack-web-delivery-design.ja.md), and [one-touch core/lazy-module design](docs/reading-pack-web-core-index-v2-design.ja.md) (Japanese).

## Published example

- [*AGI―人間を超える知能は文明をいかに変容させるか*](https://koichi-takahashi.me/agibook/) (Koichi Takahashi, Kodansha Sensho Metier, 2026; Japanese)

## Related project

- [Reading Pack Bot](https://github.com/ktakahashi74/reading-pack-bot) is an optional alpha server for publishing a reviewed Reading Pack as a conversational interface on Slack or Discord. It is not required to create or use a Reading Pack.

## Start a project

Create a project, then import the manuscript's chapter structure:

```sh
reading-pack init my-book-pack \
  --title "My Book" \
  --author "Author Name" \
  --lang en \
  --profile nonfiction-reading

reading-pack import-plan manuscript.md --output /tmp/import-plan.json
# Review the proposed structure before applying it to canonical data.
reading-pack import-apply /tmp/import-plan.json \
  --source manuscript.md --project my-book-pack --lang en

reading-pack validate --project my-book-pack
reading-pack build --project my-book-pack --lang en
reading-pack check --project my-book-pack --lang en
```

`import-plan` changes neither the manuscript nor canonical data. After review, `import-apply` adds the proposed structure as a draft.

A chapter map alone is not yet a useful Reading Pack. Select and review the summaries, claims, people, terms, reading issues, and references appropriate to the book. Edit canonical data and rebuild; do not edit files under `dist/` directly.

The [quickstart](docs/quickstart.en.md) continues from a fresh directory through canonical editing, author review, and publication gates.

## Add reviewed content

There are three routes into canonical data:

1. Edit the language-specific canonical JSON directly.
2. Apply an [Author Input Package](docs/author-input.en.md) supplied by an author, editor, publisher, or other responsible authority.
3. Use the model-neutral production workflow to create bounded work items and bind structured responses from an external agent to source evidence.

Candidate generation never writes approved content directly. Automated checks advance a candidate only to `ready_for_review`; applying it to canonical data still produces a `draft`. Final human decisions are recorded in one readable Markdown file through the [author-review workflow](docs/author-review.en.md).

An authority may declare an HTTPS reference as an `official_companion` with `proactive_when_relevant` behavior. The build places that URL in `REF` and adds fixed, model-independent guidance to `SYS`. A capable AI host is then instructed to consult relevant official pages when useful while treating page text as content rather than system instructions. The toolkit itself does not retrieve the page.

## Input and output boundaries

| Surface | Supported form |
|---|---|
| Direct manuscript input | One dependency-resolved Markdown, Org, EPUB3, PDF, or UTF-8 plain-text file |
| Upstream conversion | Convert DOCX and RTF before handoff; expand Org `#+INCLUDE` dependencies first |
| Canonical data | `reading-pack.toml` plus `data/pack.<lang>.json` |
| Primary output | One generated Reading Pack Markdown file per language |
| Optional output | Agent Skill directory and a byte-reproducible ZIP |

PDF-derived structure always requires human review. Scans and complex layouts may need a separately checked outline. The `pdf-vertical` mode reconstructs Poppler's glyph order; it is not OCR.

## Publication remains a human decision

`validate` and ordinary `check` inspect structure and consistency. `check --release` also verifies that a human has recorded decisions covering content authority, rights, publisher involvement or a reason it is unnecessary, non-reconstruction, measured quality, and publication.

The command checks that those decisions exist and remain bound to the current canonical hashes. It does not make them. See [Rights and review](docs/rights-and-review.en.md).

The ordinary path uses `review export --release-signoff` to place content and publication conditions in one human-readable Markdown file. When there are no exceptions, one final human approval is enough. A focused review and reevaluation are needed only when something must change.

## Documentation

| Guide | Purpose |
|---|---|
| [Quickstart](docs/quickstart.en.md) | Build and review a draft Pack from a fresh directory |
| [Core concepts](docs/concepts.en.md) | Understand canonical data, generated output, and approval boundaries |
| [Production workflow](docs/workflow.en.md) | Apply Production Standard W0–W13 with this toolkit |
| [Author Input Package](docs/author-input.en.md) | Apply structured material supplied by a responsible authority |
| [Author review](docs/author-review.en.md) | Record corrections and approval in one Markdown review |
| [Quality pipeline](docs/quality-pipeline.en.md) | Run model-neutral generation, evidence checks, coverage review, and candidate handling |
| [Agent Skills distribution](docs/agent-skills.en.md) | Package an existing Reading Pack for compatible hosts |
| [Portable-first delivery strategy (Japanese)](docs/reading-pack-delivery-strategy.ja.md) | Keep the single Pack canonical while evaluating optional transport adapters |
| [Standards overview](spec/reading-pack-spec.en.md) | Understand the format, production, and implementation boundaries |
| [Format specification](spec/reading-pack-format-spec.en.md) | Read the normative requirements for the Reading Pack artifact |
| [Production standard](spec/reading-pack-production-standard.en.md) | Read the normative levels, process, evaluation, and release requirements |
| [Reference implementation profile](spec/reading-pack-reference-implementation.en.md) | Review the public contract specific to this toolkit |
| [Adding another language](docs/adding-languages.en.md) | Extend the implementation beyond English and Japanese |
| [Security policy](SECURITY.md) | Review threat boundaries and report vulnerabilities |

Run `reading-pack --help` or `reading-pack COMMAND --help` for the current CLI.

## Development

The public test suite runs offline against synthetic fixtures only:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  python3 -m unittest discover -s tests -v
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  python3 -m compileall -q src tests
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  python3 -m reading_pack check \
  --project examples/clockwork-garden --lang all --release
```

See [CONTRIBUTING.md](CONTRIBUTING.md) before submitting a change.

## License

- Python code, CLI, validators, and tests: MIT
- Specifications, documentation, schemas, prompts, and READMEs: CC BY 4.0
- Synthetic *Clockwork Garden* example: CC0 1.0 Universal
- Manuscripts, structured project data, and generated Packs: terms chosen by their rights holders

See the [file-level license map](LICENSES/README.md).

Copyright 2026 Koichi Takahashi / 高橋恒一.
