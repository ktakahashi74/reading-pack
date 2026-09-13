# Quickstart

For new generation with evaluation delivery, use [Pack delivery and quality reporting](pipeline-delivery.en.md). Scores do not decide adoption or trigger repairs. The older acceptance/approval workflows on this page remain separate.

`artifact-acceptance-1` runs direct content, instruction and local delivery checks, fixed resource limits, at most one repair, separate candidate reassessment and a hash-bound author packet. Standard question generation, reader answers and grading make zero calls. Omission preserves `legacy-reader-evaluation-1`. See [Direct artifact production](pipeline-artifact-workflow.en.md) and [Model comparison](pipeline-model-comparison.en.md). Comparison is optional and is not an individual Pack acceptance gate.

Automatic production using questions and holdout below describes the legacy contract. Individual production and review commands remain available.

Fresh runs freeze a reader-utility contract before questions or answers. Generated questions no longer create mandatory Pack content. Central meanings, material qualifications and correct attribution remain essential; incidental numbers may be handled by an honest limitation and a specific relevant location already present in the Pack. Generic deflection and false statements still fail. Existing studies retain their original scope and results on restart; changing scope requires a separately identified reassessment, without implying that the candidate must be regenerated.

The automatic workflow is an unqualified alpha. Legacy-contract production mode requires a measured workflow certificate and an end-to-end time/cost envelope; legacy research commands require explicit `--experimental`. Control tests do not establish real-book completion. See [predictable production and qualification](predictable-production.en.md) for planning, measurement and current limits.

Given a manuscript, `reading-pack pipeline start` extracts structure, generates content, evaluates quality and repairs defects within fixed limits. A passing candidate is delivered with an author-review form. Author-provided appendices and other supplements are optional. Printed bibliographic references do not need a URL. Chapter reviews can include bounded neighboring source text when a chapter crosses a processing window. See the [automatic production guide](automatic-pipeline.en.md) for setup and execution.

The pipeline freezes a finite coverage contract before generation. Independent adjudication checks suspected defects against the whole Pack and bounded source context, including registered supplements. Optional improvements are saved separately and do not trigger repairs or block reader tests. Confirmed material errors and unresolved source questions still block acceptance.

New question sets are reviewed for relevance to a compact reading aid before their requirements are frozen. Existing frozen sets survive restart unchanged. Unresolved audit questions receive one bounded source lookup and rejudgment per round; supported clarifications of editable records can then be reviewed and re-audited alongside confirmed repairs. Uncertainty continues to block acceptance, and the existing round and budget limits still apply. Duplicate observations are grouped without losing their reasons or evidence.

The steps below describe individual commands for inspection or manual operation. Automatic production does not require a person to dispatch each stage. Supplied records remain protected unless the user explicitly permits selected unapproved revisions; see the automatic production guide.

For recovery across an engine update, `pipeline restart` can retain a completed-round or stopped working-draft checkpoint. The automatic production guide explains the explicit options, validation and remaining-budget requirements; a checkpoint does not imply a quality pass.

This walkthrough starts from a fresh directory, uses no network service, and ends with a technically valid draft pack. Publication still requires the human release gates.

## 1. Install the local checkout

```sh
python3 -m pip install --no-deps --no-build-isolation \
  --target .reading-pack-site .
export PYTHONPATH="$PWD/.reading-pack-site"
export PATH="$PWD/.reading-pack-site/bin:$PATH"
reading-pack doctor --project examples/clockwork-garden
```

## 2. Create a project

```sh
mkdir -p /tmp/reading-pack-quickstart
cd /tmp/reading-pack-quickstart

reading-pack init demo \
  --title "Clockwork Garden" \
  --author "Mira Aoki" \
  --lang en \
  --profile nonfiction-reading
```

Use `reading-pack profiles` to inspect all contracts. Initialization refuses a non-empty target. It creates `reading-pack.toml`, `quality-plan.json`, `sources.json`, `author-input-state.json`, `data/pack.en.json`, `templates/pack.en.md`, and the workflow directories. Human authority and profile policies begin as pending.

## 3. Import structure

Copy your UTF-8 Markdown, Org, EPUB3, PDF, or text manuscript into `demo/manuscripts/`, then run. PDF import additionally requires local Poppler `pdfinfo` and `pdftotext` (plus `pdftohtml` for vertical-layout recovery) commands:

```sh
reading-pack import-plan demo/manuscripts/book.md \
  --output /tmp/demo-import-plan.json

# Review the plan before this explicit mutation.
reading-pack import-apply /tmp/demo-import-plan.json \
  --source demo/manuscripts/book.md --project demo --lang en
```

The first command changes no canonical data. Its plan records only source identity, hierarchy, locators, confidence, provenance, and diagnostics; it contains no body prose. The second command preserves existing IDs and editorial fields only when matching is unambiguous. The older direct `import` command remains as a compatibility shortcut.

## 4. Edit and review canonical data

If the author or publisher supplies chapters, summaries, people, terms, Q&A, or references, use `reading-pack author-input template`, review and edit its complete per-module manifest, then run `author-input plan` and `author-input apply`. This records whether each module was provided, augmented, generated, or intentionally omitted without treating supplied status as approval. See [Author Input Package](author-input.en.md).

Open `demo/data/pack.en.json`. Add concise author-written chapter summaries and optional records:

- `certainty`: evidence categories defined by the author;
- `claims`: stable statements with descriptive/normative layer and falsification or revision conditions;
- `misreadings`: common misreadings and self-contained corrections;
- `names` and `glossary`: navigation entries, not invented definitions;
- `references`: official HTTP(S) resources.

Every record starts at `draft`. An AI may propose candidates using `prompts/`; audited AI review can accept candidates for draft application, while final author approval remains human. The guarded path is explicit:

```sh
reading-pack candidates create /tmp/responses.json \
  --run-directory demo/.reading-pack/runs/run-001 \
  --source demo/manuscripts/book.md --project demo --lang en
reading-pack candidates report demo/.reading-pack/runs/run-001
reading-pack candidates verify demo/.reading-pack/runs/run-001 \
  --source demo/manuscripts/book.md

# Inspect the content before recording this human decision.
reading-pack candidates accept demo/.reading-pack/runs/run-001 \
  --id CANDIDATE_ID --reviewer "Editor Name"
reading-pack candidates apply demo/.reading-pack/runs/run-001 \
  --source demo/manuscripts/book.md --project demo --lang en \
  --id CANDIDATE_ID
```

Keep response JSON private. PDF and EPUB source text is derived internally from the exact file; there is no extracted-text sidecar option. Evidence snippets are discarded from the finalized run. Their spans prove occurrence and integrity, not that the candidate interpretation follows from them. Acceptance permits draft application only; it is not final author approval. See [Quality pipeline](quality-pipeline.en.md).

## 5. Validate and build

```sh
reading-pack validate --project demo
reading-pack build --project demo --lang en
reading-pack check --project demo --lang en
```

Run `build` twice and compare if desired:

```sh
cp demo/dist/reading-pack.en.md /tmp/first-pack.md
reading-pack build --project demo --lang en
cmp /tmp/first-pack.md demo/dist/reading-pack.en.md
```

`check` performs the same byte comparison internally. A manual edit in `dist/` fails with exit code 5.

## 6. Complete release gates

Confirm rights and conduct author, publisher, non-reconstruction, and publication review. Once measured evaluation and retained evidence are ready, the release-signoff form can update approved records, `[workflow]`, accountable quality authority, and critical policies atomically from one human signature. When replacing an existing pack, bind the `reading-pack measure --json` metrics and comparison-artifact hash under `content_floor`. Record measured structure precision/recall, invented records, and attribution errors together with a retained evaluation evidence path and hash, all bound to the current canonical-data hash. Use `publisher_review = "not_required"` only when a human has established that no publisher approval is needed.

Use one human-readable Markdown review bound to the current canonical state. An agent may inspect everything, explain exceptions and owner judgments, and help fill the form. The edited Markdown itself records the human's decisions and correction instructions.

```sh
reading-pack review export --project demo --release-signoff --output author-review
# Read author-review.review.md; optionally ask an agent to explain and fill it.
reading-pack review status /path/to/author-review.review.md \
  --evidence demo/.reading-pack/reviews/author-review --project demo
reading-pack review plan /path/to/author-review.review.md \
  --evidence demo/.reading-pack/reviews/author-review --project demo \
  --output /tmp/author-review-plan.json
reading-pack review apply /tmp/author-review-plan.json \
  --review /path/to/author-review.review.md \
  --evidence demo/.reading-pack/reviews/author-review --project demo
```

With no exception, the human inspects the agent's explanation and gives one final approval instruction. If a correction is needed, apply it with a focused form and export a fresh release-signoff form after reevaluation. See [Author review](author-review.en.md) for human editing, agent assistance, and correction instructions.

```sh
reading-pack check --project demo --lang en --release
```

The release check fails until every required gate, measured profile threshold, and published record is approved and current. A later canonical or quality-contract edit makes its earlier bound review stale. Save model-evaluation metadata and the predeclared human rubric in `evaluation/` without committing manuscript text, credentials, or confidential attacks.

## Bilingual variation

Initialize with `--lang ja --lang en --primary-language ja`, import the primary manuscript first, and import the translation second. Common chapter positions receive common IDs. The secondary records retain hashes of their primary counterparts.

If validation reports `RP202`, update the translation, then run `link-translations --lang en`. The command resets changed translations to `draft`; review and approve them before release.

Copyright 2026 Koichi Takahashi / 高橋恒一. Licensed under CC BY 4.0.
