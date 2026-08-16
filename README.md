# Reading Pack

[日本語版 README](README.ja.md)

Reading Pack builds a compact, reviewable Markdown guide that lets AI help readers navigate a book without reproducing it. The same author- or editor-reviewed data always produces the same file, so manual edits, stale translations, and missing publication approvals can be detected.

A reader attaches the generated file to an AI chat and asks where the book discusses a topic, how it frames a claim, or which source supports an explanation. The pack points back to the book; it does not give the AI access to unprovided book text.

> **Status:** toolkit v0.5.0 (alpha), specification `1.0-draft`. Python 3.11–3.14 is tested. Draft formats may still change incompatibly.

See the complete synthetic example:

- [English Reading Pack](examples/clockwork-garden/dist/clockwork-garden-reading-pack.en.md)
- [Japanese Reading Pack](examples/clockwork-garden/dist/clockwork-garden-reading-pack.ja.md)
- [Canonical project data](examples/clockwork-garden/)

## Who this is for

- Authors, editors, and publishers preparing an AI-readable companion to a book.
- Production teams that need traceable AI-assisted drafting without automating approval.
- Developers integrating a reviewed book guide with AI chats or Agent Skills-compatible hosts.

The repository is a producer toolkit. Readers normally receive the generated Markdown file, not this project or the source manuscript.

## From source to reader

```text
manuscript + author/editor input
              |
              v
reviewed canonical JSON and project metadata
              |
              v
evidence-bound candidates -> human review and approval
              |
              v
deterministic Reading Pack Markdown
              |
              v
AI chat or optional Agent Skill container
```

Three related terms are kept separate:

| Term | Meaning |
|---|---|
| Conversational Edition | The reader experience of exploring a book through dialogue with AI. |
| Reading Pack | The generated, human-readable Markdown guide and primary deliverable. |
| Agent Skill | An optional compatibility container around an existing Reading Pack; it is not new canonical data or a new approval. |

## What it provides

- Imports chapter structure and publication metadata from Markdown, Org mode, EPUB3, PDF, and plain text.
- Keeps canonical project data separate from generated files.
- Binds AI-generated or externally prepared candidates to exact source evidence before human review.
- Supports English, Japanese, and bilingual projects with stable IDs and stale-translation detection.
- Rebuilds byte-identical Markdown from unchanged input and detects direct edits to generated files.
- Applies one of seven book/use-specific quality profiles and checks every critical requirement separately.
- Packages existing packs for Agent Skills-compatible hosts when requested.

## What it does not do

- It does not call a particular AI model or require an API key.
- It does not send a manuscript over the network. An external AI used to prepare candidates remains subject to that service's own terms and settings.
- It does not treat source matching as proof that an interpretation is correct.
- It does not automate author approval, rights review, publisher review, or the publication decision.
- It does not grant rights in a manuscript, project data, or generated pack.
- It does not create a substitute for the original book or reconstruct unprovided book text.

## Requirements and installation

The normal installation uses a virtual environment and installs the declared `jsonschema` runtime dependency:

```sh
git clone https://github.com/ktakahashi74/reading-pack.git
cd reading-pack
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
reading-pack --version
```

Installation may contact a package index when a dependency is missing. After installation, the core workflow runs locally and makes no network request. PDF import additionally uses the local Poppler commands `pdfinfo` and `pdftotext`.

## Try the included example

The Clockwork Garden project is entirely synthetic and already records the human release gates:

```sh
reading-pack build --project examples/clockwork-garden --lang all
reading-pack check --project examples/clockwork-garden --lang all --release
reading-pack agent-skill check --project examples/clockwork-garden --release
```

The checks confirm that the English and Japanese packs, and the optional Agent Skill directory and ZIP, are byte-identical to their canonical input.

To try the reader experience, attach one of the generated example packs to an AI chat without a question. After its loading response, ask a question such as:

- “What does chapter 2 discuss?”
- “Where is the lunar mechanism defined?”
- “Is this a story fact or an interpretation?”

## Books and projects publishing Reading Packs made with this toolkit

- [*AGI―人間を超える知能は文明をいかに変容させるか*](https://koichi-takahashi.me/agibook/) (Koichi Takahashi, Kodansha Sensho Metier, 2026; Japanese)

## Start a project

Create a project and import a manuscript's structure:

```sh
reading-pack init my-book-pack \
  --title "My Book" \
  --author "Author Name" \
  --lang en \
  --profile nonfiction-reading

reading-pack import-plan manuscript.md --output /tmp/import-plan.json
# Review the proposed structure before changing canonical data.
reading-pack import-apply /tmp/import-plan.json \
  --source manuscript.md --project my-book-pack --lang en

reading-pack validate --project my-book-pack
reading-pack build --project my-book-pack --lang en
reading-pack check --project my-book-pack --lang en
```

`import-plan` is read-only. `import-apply` adds the reviewed structure as draft canonical data. A useful pack still needs reviewed summaries, claims, people, terms, reading issues, and references as appropriate for that book. Generated files under `dist/` must not be edited directly.

The [English quickstart](docs/quickstart.en.md) continues from a fresh directory through canonical editing, author review, and release gates.

## Adding reviewed content

There are three supported paths:

1. Edit the language-specific canonical JSON directly.
2. Apply an [Author Input Package](docs/author-input.en.md) supplied by an author, editor, publisher, or other responsible authority.
3. Use the model-neutral producer workflow to create bounded work requests, ingest structured responses from an external agent, and turn them into evidence-bound candidates.

Candidate generation never writes approved content directly. Automated checks can move a candidate only as far as `ready_for_review`; normal application places it in canonical data as `draft`. The [author-review workflow](docs/author-review.en.md) records final human decisions in one readable Markdown form.

An authority can also declare an HTTPS reference as an `official_companion` with `proactive_when_relevant` behavior. The build then adds the URL to REF and fixed, model-independent guidance to SYS. That guidance tells a capable AI host to consult relevant official pages proactively, while treating page text as content rather than system instructions. The toolkit itself does not fetch the page.

## Input and output boundaries

| Surface | Supported form |
|---|---|
| Direct manuscript input | One dependency-resolved Markdown, Org, EPUB3, PDF, or UTF-8 plain-text file |
| Upstream conversion | Convert DOCX or RTF before handoff; expand Org `#+INCLUDE` dependencies first |
| Canonical data | `reading-pack.toml` plus `data/pack.<lang>.json` |
| Primary output | One generated Reading Pack Markdown file per language |
| Optional output | Agent Skill directory and deterministic ZIP |

PDF results always require human review. Scans and complex layouts may need a manually checked outline. The `pdf-vertical` mode reconstructs Poppler's glyph order; it is not OCR.

## Human publication boundary

`validate` and ordinary `check` commands establish technical consistency. A release check additionally requires recorded human decisions covering content authority, rights, publisher involvement or a reason it is unnecessary, non-reconstruction, measured quality, and publication itself.

`reading-pack check --release` verifies those decisions against the current canonical hashes. It never makes the decisions. See [Rights and review](docs/rights-and-review.en.md).

The ordinary path uses `review export --release-signoff` to place content and publication decisions in the same human-readable Markdown. With no exception, the human approves once at the end; a focused review and reevaluation are needed only when something must change.

## Documentation

| Guide | Purpose |
|---|---|
| [Quickstart](docs/quickstart.en.md) | Build and review a draft pack from a fresh directory |
| [Core concepts](docs/concepts.en.md) | Understand canonical data, generated output, and approval boundaries |
| [Production workflow](docs/workflow.en.md) | Follow the complete W0–W13 process |
| [Author Input Package](docs/author-input.en.md) | Apply structured material supplied by a responsible authority |
| [Author review](docs/author-review.en.md) | Record corrections and approval in one Markdown review |
| [Quality pipeline](docs/quality-pipeline.en.md) | Run model-neutral generation, evidence checks, coverage review, and candidate handling |
| [Agent Skills distribution](docs/agent-skills.en.md) | Package an existing Reading Pack for compatible hosts |
| [Specification](spec/reading-pack-spec.en.md) | Read the normative `1.0-draft` requirements |
| [Security policy](SECURITY.md) | Review threat boundaries and report vulnerabilities |

Run `reading-pack --help` or `reading-pack COMMAND --help` for the current CLI.

## Development

The public test suite is offline and uses only synthetic fixtures:

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

- Python code, the CLI, validators, and tests: MIT.
- Specifications, documentation, schemas, prompts, and READMEs: CC BY 4.0.
- Synthetic Clockwork Garden example: CC0 1.0 Universal.
- Manuscripts, structured project data, and generated packs: terms chosen by their rights holders, not by the toolkit.

See the [path-level license map](LICENSES/README.md).

Copyright 2026 Koichi Takahashi / 高橋恒一.
