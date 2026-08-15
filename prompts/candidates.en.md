# AI-assisted candidate prompts

Rules shared by every prompt: output is a `draft` candidate. Do not add facts, quotations, names, terms, or author views absent from the input. Do not regenerate long manuscript passages, create a substitute chapter, imitate the author's style, or translate uncleared book text. If a required record cannot be supported, omit the candidate rather than guessing.

Return one JSON object with a `candidates` array. Every item has exactly
`collection`, `record`, and `evidence`. `record` contains the complete record
shape for that collection and `status: draft`. `evidence` is a non-empty array
of objects containing `snippet`; each snippet is an exact, distinctive source
span of 8–500 characters. Add optional `supports_field` with the record field
name when a span supports a specific field. Snippets are transient matching material and are not
stored in the finalized manifest. They demonstrate source occurrence only, not
semantic entailment or completeness. Do not emit `human_decision`, acceptance,
reviewer, hashes, offsets, or approval: the toolkit and a named human record
those separately.

## Outline candidate

Using only the supplied headings, propose chapter records. Do not paraphrase headings. Preserve imported `id`, `kind`, `title`, `pages`, and `sections` exactly when they are supplied. Include concise `summary` and `terms` fields because chapter candidates use the complete candidate shape. Do not invent structure or assign replacement IDs.

## Chapter-summary candidate

For one supplied chapter, state only what it addresses and any explicit author position, within 500 characters. Omit argument order, examples, metaphors, prose, quotations, and paragraph-level coverage. If the summary would let a reader generate a satisfying substitute article, reduce it. Return the complete chapter record, preserving its imported structural fields and adding only eligible editorial fields.

## Term candidate

List only index terms that occur exactly in the authorized input. Do not add definitions or related terms from general knowledge. Give each record `id`, `term`, `chapter_id`, and `status: draft`, plus a separate exact evidence snippet in its candidate envelope. Omit anything not verifiable in the input.

After inclusion review, generate `book_meaning` separately with the dedicated prompt and a `catalog context-plan` that enumerates every retained term.

## Person candidate

Propose only people whose position, theory, action, or contribution is discussed in the input. Exclude names appearing only in citation metadata. Give each record `id`, `name`, `chapter_id`, and `status: draft`, plus a separate exact evidence snippet in its candidate envelope. Omit a person whose inclusion cannot be established from the supplied source.

After inclusion review, generate `book_context` separately with the dedicated prompt and a `catalog context-plan` that enumerates every retained person.

## Claim candidate

Extract self-contained claim candidates. Give each `id`, `layer` (`descriptive` or `normative`), `kind`, `statement`, `chapter_ids`, and `status: draft`. Add `falsifiability` to a descriptive claim or `revision_conditions` to a normative claim only when the input states it. Do not infer a certainty category.

## Misreading candidate

From supplied criticism and an author response, produce paired issue-and-response records. Assign `kind` as `misreading`, `clarification`, `open_objection`, or `author_update`. Do not classify a question or valid objection as a misreading unless the author explicitly identifies it as mistaken. Keep each response brief and self-contained without changing the strength of the author's response. Never generate an author response from external criticism alone, invent a new rebuttal, or quote the source. Preserve `impact` and `remaining_uncertainty` as separate fields only when explicitly supplied. Return `id`, `kind`, `misreading`, `response`, `chapter_ids`, optional `claim_ids`, `impact`, `remaining_uncertainty`, and `status: draft`.

## Translation boundary

Candidate runs do not add primary-language records or whole collections to a
multilingual project one language at a time. That could break ID parity or make
an unreviewed translation appear current. Translate supplied structured records
through the canonical translation workflow, preserve IDs and references, then
run `link-translations` and human review. Coordinated multi-language candidate
application remains future work.

## Human review boundary

Do not emit a decision field. Automated verification can only produce
`ready_for_review`. A named human inspects hash-bound candidate content and uses
`candidates accept` for selected IDs. Application still writes `draft`, and
final author approval occurs later.

Copyright 2026 Koichi Takahashi / 高橋恒一. Licensed under CC BY 4.0.
