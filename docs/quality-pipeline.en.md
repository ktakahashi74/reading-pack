# Quality pipeline

Reading Pack treats “works for any book” as a controlled production claim. It
does not mean that one prompt can publish every genre. The software provides the
same bounded route from an exact source to a reviewable draft, while profiles
make the necessary differences between nonfiction, textbooks, fiction,
anthologies, and reference works explicit. Rights, interpretation, spoiler
judgment, authorial authority, and publication remain human decisions.

## Four assurance layers

1. **Quality contract** — `quality-plan.json` declares the profile, scope,
   authority, spoiler policy, module applicability, critical policies, and
   measured acceptance thresholds. Quality is the AND of critical gates, not an
   averaged score.
2. **Import plan** — `import-plan` produces a bounded, body-free structure plan
   before canonical mutation. It records the exact source name, size and hash,
   hierarchy, locators, confidence, provenance, diagnostics, and review outcome.
3. **Candidate run** — candidate records and source snippets enter a private run.
   The finalized manifest stores hashes and normalized-text spans, not excerpts.
   Automated checks can quarantine a candidate or mark it `ready_for_review`;
   they cannot accept or approve it.
4. **Canonical and release review** — an explicitly accepted candidate can be
   applied only by ID and only as `draft`. Separate author, rights, publisher,
   non-reconstruction, measured-evaluation, and publication gates control a
   release.

An import-plan checksum or candidate-manifest checksum detects accidental
corruption and unkeyed modification. It is not a digital signature, does not
authenticate the reviewer, and does not establish source ownership.
Reviewer names are self-attested metadata. A writer who can rewrite the private
run can also recompute its unkeyed hashes and forge that metadata; hostile
writers are outside this cooperative workflow's threat model. Use a separately
administered signature or approval system when authenticated identity is needed.

## Profile contracts

`reading-pack profiles` lists seven built-in contracts:

| Profile | Intended use | Mandatory emphasis |
|---|---|---|
| `general-navigation` | broad chapter/section routing | structure and non-reconstruction |
| `academic-argument` | monographs and research arguments | summaries, terms, claims, references, attribution |
| `nonfiction-reading` | trade and general nonfiction | claims, references, qualification preservation |
| `textbook-learning` | instructional books | learning objectives, claims, glossary, references, misconception safety |
| `fiction-spoiler-free` | discovery without plot disclosure | spoiler scope and interpretive openness |
| `anthology-attribution` | multi-author collections | contributors and authority attribution |
| `reference-routing` | dictionaries, handbooks, catalogues | inventory, aliases, and location routing |

Each profile sets a minimum production level and names mandatory modules,
chapter fields, critical policies, and its default spoiler policy. A module can
be `not_applicable` only when the profile permits that disposition and the plan
gives a reason. General nonfiction still audits references, but an inspected
edition with no Pack-usable reference target may declare that absence
explicitly; an empty collection without the declaration still fails. Academic
and textbook profiles continue to require references. This preserves genre
differences without allowing a difficult gate to disappear into a score.

New plans begin pending. Release validation requires all of the following:

- named human authority approval bound to the current canonical-data hash and
  the current substantive quality-contract hash;
- approved critical policies and complete profile-required data;
- `book_context` for every retained person and `book_meaning` for every
  retained term, recording their treatment in this book; very short text and
  formulaic “mentioned in this chapter” placeholders do not count;
- when a replacement project declares a `content_floor`, current canonical
  summaries, claims, qualifications, reading issues, index explanations,
  references, and content-character counts do not fall below the floor bound
  to the prior artifact's SHA-256. These are no-regression gates, not scores;
- every published record approved through the separate author-review process;
- measured structure precision and recall, invented-record count, and
  source-attribution-error count meeting the predeclared thresholds; and
- an evaluation evidence file whose recorded hash still matches.

A canonical or contract change makes the corresponding approval stale. A
quality plan that is absent, pending, self-declared without measured results, or
bound to old data cannot pass `--release`.

`reading-pack measure --project PACK --json` recomputes the same counts from
canonical data. If the prior pack is not canonical JSON, fix the measurement
definition in a reviewed comparison report and copy its artifact hash and
values into `content_floor`. Duplicate or padded records still fail source
fidelity and semantic review; raw quantity is never sufficient by itself.

## Structure import

```sh
reading-pack import-plan book.pdf --output /tmp/book-import-plan.json
# Inspect outcome, diagnostics, hierarchy, locators, and provenance.
reading-pack import-apply /tmp/book-import-plan.json \
  --source book.pdf --project my-pack --lang en
```

Planning does not change canonical data. Applying rechecks the exact source and
plan, acquires the cooperative project lock, and reconciles existing records.
Unambiguous matches retain stable IDs and eligible editorial fields. A source
change, ambiguous match, or validation failure stops before canonical
replacement. Re-import resets changed source or structure records to `draft`.

For a scan or complex layout, `--outline-sidecar outline.json` provides a manual
structure recovery path. The bounded sidecar is tied to the exact source
SHA-256 and records reviewer, reason, chapter kind, stable source key, title,
page, and section titles. It cannot carry summaries, terms, prose, or approval.
OCR output itself remains private and is not an outline sidecar.

PDF structure and printed pages remain conservative estimates: flat text cannot
reliably reconstruct every table of contents, heading level, or physical page.
Those cases require source inspection, not a higher-confidence heuristic.

For Japanese PDFs whose vertical text layer is emitted one glyph per line,
pass `--format pdf-vertical` to `import-plan`. Import records that format in the
source identity; candidate evidence, catalog extraction, and review bundles
then use the same internal reconstruction while remaining bound to the original
PDF hash; no arbitrary
text sidecar becomes the authority. This is not OCR and cannot guarantee every
font mapping or chapter boundary inside a two-page spread. A manual outline,
explicit chapter map, and stratified inspection against the rendered pages are
quality gates.

## Resumable bounded generation

`work plan --session-directory ... --source ...` derives work from AIP
`generate`/`augment` declarations and otherwise unfilled modules. The session
and ledger contain source and canonical hashes, chapter ranges, states, and
response hashes, but no manuscript body. `work next` emits one machine-readable
request containing a fixed prompt, source locator, exact work binding, and a
standalone Draft 2020-12 response Schema. It does not assemble the whole book
into one prompt.

The fixed module set includes book-scoped `policy`. A worker may return policy
only when the source explicitly states it and may never infer permission,
approval, or official status. Candidate application turns verified evidence
ranges into record-level `source_locations`; these complement, rather than
replace, the registered module source hash.

An outer agent may inspect only the declared source range and write a bounded
response file. If it finds no evidence-bearing candidate, it may close the next
item with `work close --outcome no_supported_candidate|skipped --reason
REASON_CODE`. The command constructs a zero-result response from that item's
binding and submits it through the ordinary Schema, stale, scope, and duplicate
checks. It deliberately excludes `failed`, which denotes a processing failure
rather than a content judgment. The operator may also explicitly select a local executable with
`work ingest --adapter-executable ...`. The executable receives the same JSON
request on standard input. It is invoked without a shell and with a timeout and
output limit, but it is trusted code rather than a sandbox. No built-in model,
provider API, network call, or provider-specific tool name is required.

Every accepted response repeats the session ID, work ID, project/config hash,
language, source hash, canonical hash, module, scope, and chapter range.
Duplicate, foreign, stale, out-of-scope, oversized, structurally invalid, or
timed-out responses do not advance the session. Outcomes distinguish
`complete`, `no_supported_candidate`, `failed`, and `skipped`; only `complete`
may carry records. A primary-book generation session rejects invented Q&A.
An explicitly selected `misreadings` module may produce neutral `issue` and
`response` records only for source-explicit objections, qualifications, or
distinctions that prevent a material reader error. It is not part of the
default automatic module set, may not portray cited critics as mistaken, and
does not create independent author Q&A.
When the operator supplies `work plan --chapter-map`, the reviewed,
source-hashed normalized-text spans become part of the session identity. Each
chapter-scoped evidence occurrence is then located and checked against that
span during ingest, before a response can advance the session.

The private session directory is permission-restricted. Ingested response files
may temporarily contain short evidence snippets. `finalize` re-verifies every
snippet against the exact source through the ordinary candidate pipeline,
reconciles the existing work ledger, publishes one excerpt-free candidate run
only after validation, and removes the transient response files. It leaves
canonical data unchanged. A failed candidate verification does not occupy the
requested run directory, so the source responses and open session remain
available for diagnosis. `work retry --id WORK_ID` is the explicit, hash-checked
operation that removes only that stored response and returns its item to
`awaiting_response`; duplicate ingestion never overwrites it. Candidate review, acceptance, draft application,
author review, rights, and release gates are unchanged.

An optional post-draft session uses `work plan --purpose coverage`. It is not a
free-form project prompt or a numerical quota. The producer supplies the fixed
`whole_book_gap_audit_v1` rubric and an excerpt-free inventory of the current
scope. The outer agent compares that hash-bound canonical baseline with the
same primary source and either proposes a materially supported addition or
replacement, or records an explicit zero outcome. The rubric checks summary
argument and qualifications, retrieval terms, descriptive and normative
claims, mechanisms and conditions, attribution and uncertainty, and
book-specific people and term context. Exhaustive people/term discovery remains
the catalog workflow: reviewed explicit chapter map, optional high-recall
`catalog candidates --responses`, ordinary candidate review, then
`catalog context-plan --refresh-existing` and `catalog context-candidates`.

## Candidate review

Prepare bounded JSON responses following `prompts/candidates.en.md`. Evidence
snippets are transient inputs used only to find spans in the exact source.

```sh
reading-pack candidates create /tmp/responses.json \
  --run-directory my-pack/.reading-pack/runs/run-001 \
  --source book.pdf --project my-pack --lang en

reading-pack candidates report my-pack/.reading-pack/runs/run-001
reading-pack candidates verify my-pack/.reading-pack/runs/run-001 \
  --source book.pdf

# A human reads the proposed content and accepts selected hash-bound candidates.
reading-pack candidates accept my-pack/.reading-pack/runs/run-001 \
  --id CANDIDATE_ID --reviewer "Editor Name"

# Or use an AI decision artifact bound to the exact run and candidate.
reading-pack candidates accept my-pack/.reading-pack/runs/run-001 \
  --id CANDIDATE_ID --reviewer "model-id" --reviewer-type ai \
  --review-artifact my-pack/.reading-pack/ai-review-run-001.json

# Application still requires each ID and writes draft records only.
reading-pack candidates apply my-pack/.reading-pack/runs/run-001 \
  --source book.pdf --project my-pack --lang en --id CANDIDATE_ID

# Bind sequentially applied runs to one current canonical handoff artifact.
reading-pack candidates receipt --project my-pack --lang en \
  --artifact my-pack/.reading-pack/runs/run-001 book.pdf \
  --artifact my-pack/.reading-pack/runs/run-002 book.pdf \
  --output applied-chain.json
```

PDF and EPUB source representations are derived internally from the exact input
file. These commands deliberately provide no option to substitute an arbitrary
extracted-text sidecar. UTF-8 text formats are decoded directly. Source files,
response JSON, and any local working extraction remain outside the finalized
manifest and should stay in a private workspace.

Evidence verification proves only that the recorded normalized-text span comes
from the exact source and still hashes the same way. It does not prove that the
span entails the candidate, that a summary is complete, that attribution is
correct, or that an interpretation is authoritative. The configured human or
AI reviewer must make those judgments before `accept`; AI review records them
in an excerpt-free run-bound artifact. Final author approval remains a later
human gate.

Candidate creation and application also enforce field types and limits,
globally safe IDs and references, exact term/name occurrence, bounded evidence,
and excessive-copy checks across record text. Unknown, unsupported, duplicate,
stale, or source-copy-risk records remain quarantined. Chapter candidates amend
eligible editorial fields; they do not silently replace imported structure.
For multilingual projects, the current candidate command does not apply new or
changed primary-language records unilaterally; use the canonical translation
workflow so ID parity and freshness remain explicit. A coordinated multi-run
translation transaction remains a known extension point.

## Concurrency and failure semantics

- A candidate run is bound to the source hash, normalized-source hash, canonical
  snapshot, candidate record hashes, and relevant base-record hashes.
- Acceptance records the named reviewer and type against the unchanged
  candidate hash; AI review also records the decision-artifact hash.
- Apply rechecks the source, canonical snapshot, base record, evidence, and
  acceptance under a cooperative project lock.
- A stale source, intervening canonical edit, ambiguous reconciliation,
  quarantined candidate, or failed validation stops rather than overwriting
  newer work.
- Canonical JSON replacement is one file operation. Candidate application also
  updates a separate private manifest; the pair is not a filesystem-wide
  transaction and must not be described as transactionally atomic. A prepared
  run state makes an interrupted application detectable and recoverable.
- A successful or recovered application retains the prepared transaction's
  before/after data and project hashes as a deterministic `application`
  receipt. `candidates receipt` rechecks every supplied run, source, evidence
  span, terminal state, adjacent hash link, and final canonical binding. Older
  manifests require `--allow-legacy` and cannot be reported as verified links.
- Successful import or candidate application still yields `draft`. It does not
  satisfy author or release approval.

These checks coordinate this toolkit's writers. They do not prevent another
program from ignoring the project lock.

## Local adapter trust boundary

The optional local JSON subprocess adapter uses a bounded request, bounded
response, timeout, and direct executable invocation without a shell. That is an
I/O and resource boundary, not a sandbox. The configured executable can still
read files available to its user and can use the network. Treat it as trusted
code, inspect its configuration, and apply the manuscript's confidentiality and
provider rules. The fact that the core toolkit makes no network requests says
nothing about an arbitrary adapter executable.

## Current boundary

The shipped structure adapters cover UTF-8 Markdown, Org, EPUB3, conservative
PDF tables of contents, and conservative plain text. Scanned PDFs require OCR
plus a source-checked manual outline. DOCX, HTML, richer EPUB navigation,
automatic page-region evidence, and a graphical review interface remain future
adapters. None may bypass the same plan, evidence, acceptance, stale-write, and
release boundaries.

Copyright 2026 Koichi Takahashi / 高橋恒一. Licensed under CC BY 4.0.
