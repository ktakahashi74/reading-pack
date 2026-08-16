STANDARD | name=Reading Pack Production Standard | version=1.0-draft | status=beta | language=en | primary=false | date=2026-08-16 | author=Koichi Takahashi | license=CC BY 4.0

# Reading Pack Production Standard 1.0-draft (beta)

The Japanese version is canonical. The key words MUST, MUST NOT, SHOULD, and MAY are normative. Requirements may change incompatibly during the draft period.

## 0. Scope

**RPP-001** This standard defines the process for producing a Reading Pack with evidence, human review, evaluation, and a publication decision. The completed artifact is defined by the [Reading Pack Format Specification](reading-pack-format-spec.en.md). This standard does not require a particular CLI, programming language, AI model, provider, or internal data structure.

**RPP-002** Production conformance MUST be determined independently of format conformance. A format-conforming Pack made by a nonconforming process MUST NOT be rejected as a format artifact, and production conformance MUST NOT be inferred from format conformance alone.

## 1. Production levels

**RPP-003** Production has the following three levels. A higher level includes the requirements of lower levels.

| Level | Minimum capability | Required human review |
|---|---|---|
| Level 1 | Bibliography and chapter/section navigation | Structure, bibliography, rights, non-reconstruction, and publication decision |
| Level 2 | Level 1 plus book-appropriate summaries, indexes, reading issues, or references | Content, attribution, qualifications, index context, and publication gates |
| Level 3 | Level 2 plus canonical claims separated into descriptive/normative, and certainty and falsification/revision conditions when used | Claims, certainty, qualifications, and falsification/revision conditions |

**RPP-004** A level is a declaration of produced capability and review scope, not an information-volume score. Modules inappropriate to a book MUST NOT be added merely to increase counts.

## 2. Process and authority

**RPP-005** Production MUST use W0 through W13 and distinguish D (reproducible processing), L (model assistance followed by inspection), R (candidate triage by a human or auditable AI), and H (human judgment that software does not perform).

| Stage | Subject | Principal actor | Required output or decision |
|---|---|---|---|
| W0 | Design constraints | H | Scope, rights, languages, level, profile, evaluation thresholds |
| W1 | Bibliography | D → H | Verified bibliography |
| W2 | Certainty | L → H, optional | Categories adopted by the author |
| W3 | Chapter map | D/L → R/H | Source-bound structure |
| W4 | Canonical claims | D/L → H, optional | Attribution, type, location, conditions |
| W5 | People | L → R/H, optional | Treatment within the book |
| W6 | Terms | L → R/H, optional | Meaning within the book |
| W7 | Reading issues | L → H, optional | Classified issues and responses |
| W8 | AI rules | H | SYS and book-specific policies |
| W9 | References | D → H, optional | REF with reviewed relation and official status |
| W10 | Assembly | D → H | Validated format-conformance candidate |
| W11 | Evaluation | L → H | Measurements against predeclared criteria |
| W12 | Author review | H | Evidence of corrections, approval, or hold |
| W13 | Publication and versioning | D + H | Publication decision, version, distribution, changelog |

**RPP-006** W0, the final W11 judgment, W12, and the W13 publication decision MUST NOT be automated. AI MAY extract decision points, explain them, prepare candidates, and help fill response fields, but MUST NOT create human consent.

## 3. Quality plan and canonical source

**RPP-007** Production MUST begin by recording a book/use quality profile, scope, responsible authority, spoiler policy, languages, level, mandatory modules, rights boundary, size budget, non-reconstruction rubric, and evaluation thresholds. Conformance is the logical AND of mandatory gates; an average score MUST NOT offset a critical omission.

**RPP-008** The editable canonical source MUST be separate from the distributed artifact. The same canonical source, settings, and template MUST reproduce the same Pack bytes, and direct edits to the artifact MUST be detectable.

**RPP-009** Canonical records MUST have stable IDs, states, and provenance. Automated processing MUST NOT set a record to `approved`. Approval affected by changes to source, structure, meaning, or translation source MUST become stale and be revoked.

## 4. Import, candidates, and evidence

**RPP-010** Source import MUST separate a read-only plan from explicit application. The plan MUST retain no body text and contain only source identity, hash, structure, locations, confidence, and diagnostics. Source and canonical freshness MUST be rechecked immediately before application.

**RPP-011** Content produced by AI or external processing MUST remain a candidate outside the canonical source. An applicable candidate MUST be bound to the source hash, candidate hash, and at least one bounded evidence span, and MUST NOT advance beyond `ready_for_review` through automated checks.

**RPP-012** Evidence verification establishes only that a recorded span exists in the stated source and that the candidate is unchanged. It MUST NOT be described as proof of correctness, completeness, attribution, or interpretive validity. Final public provenance MUST NOT retain source excerpts.

**RPP-013** Candidate triage MUST be recorded against candidate IDs and content hashes. If AI triage is used, the record MUST include model, settings, method, time, and candidate/evidence hashes. Neither form of triage replaces final author review, and applied records MUST remain `draft`.

**RPP-014** Explanations of people and terms MUST be bounded abstractions of their role or meaning within the book and checked for contiguous source reproduction. A zero-result coverage check MUST NOT automatically be interpreted as not applicable.

## 5. Authority-supplied material and language editions

**RPP-015** Structured input from an author, editor, publisher, or other responsible authority MUST distinguish, per module, complete replacement, augmentation by matching ID, delegation to generation, and an explicit not-applicable result. Supplied approval states MUST NOT be trusted; applied values MUST become `draft`.

**RPP-016** Multiple language editions MUST use shared IDs and explicit correspondence, and each translation MUST be bound to a semantic hash of its source-language record. A source-language change MUST make stale translations and approvals detectable and revoke them.

**RPP-017** `misreading`, `clarification`, `open_objection`, and `author_update` MUST be distinguished through authority input or explicit classification. Criticism MUST NOT automatically be called a misreading, and author Q&A MUST NOT be invented.

## 6. Author review and publication decision

**RPP-018** Author review MUST use one human-readable and editable Markdown file bound by hash to the current canonical source and production conditions. The edited file is evidence of consent, revision, exclusion, or hold. Conversation or AI output alone MUST NOT count as consent.

**RPP-019** Review MUST distinguish `approve`, `revise`, `revise_approve`, `exclude`, and `hold`, applying each only to displayed targets and revisions. Matching records MAY be decided as an evidence-bound group only if exceptions can override the group and application expands it into record-level decisions.

**RPP-020** Aggregate release signoff is permitted only for a complete, unscoped review that lists every public record, rights, publisher review or a not-required decision, non-reconstruction, quality authority, version, measured evaluation, and publication decision. With no exception, one explicit human instruction MAY record submission, final signoff, and publication approval.

**RPP-021** Operations changing canonical source, settings, quality plan, review, or evaluation evidence MUST verify before/after hashes, reject intervening changes, and use a recoverable transaction that leaves no partial change after failure.

## 7. Evaluation

**RPP-022** Before model evaluation, the model ID, settings, date, question categories, and pass criteria MUST be fixed. Evaluation MUST include navigation, certainty, falsification/revision conditions, complete listing, absent items, quotation pressure, author impersonation, normative-as-factual framing, outside-pack questions, rule override, initial receipt, one-word input, and reconstruction.

**RPP-023** The public layer MUST include question categories, judgment framework, pass criteria, level mapping, and safe examples for every category. Exact attack wording, the complete question set, red-team procedure, source material, and actual answers MAY remain private. Access to a secret evaluation set MUST NOT be a condition of conformance.

## 8. Production conformance

**RPP-024** Production conformance MAY be claimed only when the target level, all mandatory profile gates, human approval bound to current canonical data, rights, non-reconstruction, publication decision, and measured evaluation meeting predeclared thresholds are complete. A `beta` label MUST NOT waive unfinished requirements.

**RPP-025** A conforming production MAY self-declare `Reading Pack Production 1.0-draft Level N beta`. This claim MUST be displayed independently of format conformance, and the tool name MUST NOT be presented as part of the production standard.

**RPP-026** Use of the `reading-pack` reference implementation is not required for conformance. A third party may provide an independent process, implementation, or commercial production service that meets the same requirements.

## 9. Public supporting material

The following public guides help apply this standard. If they conflict with this standard, this standard controls.

- [Detailed W0–W13 operating guide](../docs/workflow.en.md)
- [Author Input Package](../docs/author-input.en.md)
- [Author review](../docs/author-review.en.md)
- [Quality pipeline](../docs/quality-pipeline.en.md)
- [Public model-evaluation form](../evaluation/model-evaluation-template.md)

## Change history

- 1.0-draft beta (2026-08-16): Separated production levels, W0–W13, evidence, review, evaluation, and release conformance from the former aggregate specification; removed a particular CLI and internal implementation as production-conformance conditions.

Copyright 2026 Koichi Takahashi. Licensed under CC BY 4.0.
