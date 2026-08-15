# Author review

The default author-review format is one human-readable Markdown file. A person edits and submits that file. The edited file itself is the evidence of decisions, consent, holds, and correction instructions.

An agent may assist by inspecting the complete session, aggregating exceptions, explaining recommendations, and filling response fields at the human's request. The conversation and agent output are not the authorization evidence. The human remains the decision-maker and inspects the final edited Markdown before submission.

## 1. Export the review form

```sh
reading-pack review export \
  --project ./my-pack \
  --output author-review
```

Use the same path with a module scope when book-specific policy must be decided before the complete review:

```sh
reading-pack review export \
  --project ./my-pack \
  --module policy \
  --output policy-review
```

The scoped form contains policy records only and omits whole-pack previews and global questions. It uses the normal `submitted`, plan, and apply evidence path but cannot grant whole-pack `final_signoff`. Approved policy becomes operational on the next build; unapproved policy remains visible but non-operational.

For one decision, scope the form by record ID instead of displaying the complete module. A bilingual project includes every configured language for that ID.

```sh
reading-pack review export \
  --project ./my-pack \
  --record TERM-06-004 \
  --output singleton-review
```

To place exact QA-passed bilingual candidates into that form as suggested revisions, repeat `--candidate-run`:

```sh
reading-pack review export \
  --project ./my-pack \
  --candidate-run ./.reading-pack/runs/term-ja \
  --candidate-run ./.reading-pack/runs/term-en \
  --output term-review
```

The command creates an unsubmitted form containing only candidate target records and prefills changed fields as `revise_approve`. Candidate runs must match the current canonical hash and pass QA; a primary-language replacement requires suggestions for every configured language.

This creates:

- `PROJECT/.reading-pack/reviews/author-review.review.md`, the form the human edits and returns;
- `PROJECT/.reading-pack/reviews/author-review/manifest.json`, body-free private evidence retained for validation and apply.

The form places scope, evidence groups, individual exceptions, policy questions, corrections, rendered previews, and submission in one flow. Agent-assistance instructions sit in a hidden comment at the end. The form records only the review ID and session SHA-256; validation reconstructs the complete session from private evidence and current canonical state. The human-facing form does not embed Base64 or the complete machine session.

Both the form and evidence contain private review material.

## 2. Human decisions

Only edit regions bounded by `RP_RESPONSE` or `RP_OVERRIDES`. Change `[ ]` to `[x]` for exactly one selected choice.

Records whose current semantic hashes match authority-provided content and have source and authority metadata may be decided in evidence-bound groups. Generated, revised, and provenance-drifted records appear as individual decisions.

Each policy shows a recommendation and reason. Rights, official status, language-edition status, and other facts not established by the session are marked as owner judgments. An unresolved configuration is never recommended as accepted; for example, `pack_license = "rights-holder decision pending"` makes the rights recommendation `needs_work`.

## 3. Agent assistance

Give the form to an agent and ask it to inspect everything and explain only exceptions and owner judgments. The embedded protocol tells the agent to:

1. inspect canonical content, provenance, translations, and rendered previews;
2. aggregate repeated gaps instead of asking through hundreds of records;
3. explain recommendations for groups, exceptions, and policies;
4. edit response regions only after an explicit human request;
5. return the edited file for human inspection.

The agent cannot select a choice merely because it recommended it. It may check `submitted` or `final_signoff` only after the human explicitly says the file represents their decisions.

## 4. Correction instructions

Place revisions, exclusions, or holds under the override region using the example embedded in the form:

```markdown
### ARU-000123
- `decision`: `revise_approve`
- `comment`: Reason for the correction
#### `summary`
- `operation`: `set`
<!-- RP_VALUE_START -->
Replacement content
<!-- RP_VALUE_END -->
```

The human may state a correction conversationally and ask the agent to format it. Planning validates editable fields, list values, translation decisions, and exclusion parity. `revise_approve` applies the exact revision displayed in the signed Markdown and marks that result `approved`. Use the existing `revise` decision when the changed record should return to `draft` for a later review.

## 5. Submit

Complete:

- `reviewer`, the human reviewer;
- `reviewed_at`, the review date;
- `submitted`, attesting that the edited file records the human's decisions;
- `final_signoff`, only for a complete final approval.

Partial decisions and correction instructions still require `submitted`. Record- and module-scoped forms cannot grant whole-pack final signoff. In a complete form, final signoff requires every record to resolve to `approve`, `revise_approve`, or `exclude`, every required policy to resolve to `accept`, and no unapproved revision, hold, or pending decision.

Changing headings, explanations, item lists, previews, HTML comments, or response boundaries invalidates the protected-content hash. This binds the edited answers to the exact review target.

## 6. Validate and apply

```sh
reading-pack review status ./author-review.review.md \
  --evidence ./my-pack/.reading-pack/reviews/author-review \
  --project ./my-pack

reading-pack review plan ./author-review.review.md \
  --evidence ./my-pack/.reading-pack/reviews/author-review \
  --project ./my-pack \
  --output ./author-review-plan.json

reading-pack review apply ./author-review-plan.json \
  --review ./author-review.review.md \
  --evidence ./my-pack/.reading-pack/reviews/author-review \
  --project ./my-pack
```

Group decisions expand to explicit per-record decisions before planning. The body-free plan and ledger retain each record ID, decision, and before/after hash. Any intervening change to canonical content, configuration, quality plan, templates, AIP state, or review state makes the form stale.

Successful final signoff changes only `reading-pack.toml.workflow.author_review` to `approved`. Rights, publisher review, accountable non-reconstruction review, measured model evaluation, quality authority, and publication remain separate release gates.

Copyright 2026 Koichi Takahashi / 高橋恒一. CC BY 4.0.
