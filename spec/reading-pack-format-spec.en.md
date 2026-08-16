SPEC | name=Reading Pack Format Specification | version=1.0-draft | language=en | primary=false | date=2026-08-16 | author=Koichi Takahashi | license=CC BY 4.0

# Reading Pack Format Specification 1.0-draft

The Japanese version is canonical. The key words MUST, MUST NOT, SHOULD, and MAY are normative. Compatibility between drafts is not guaranteed.

## 0. Scope

**RPF-001** This specification defines the format and semantics of the Reading Pack artifact supplied by a reader to an AI. It does not define how it is produced, which tool is used, how author approval is conducted, or whether it may be published.

**RPF-002** A Reading Pack MUST be one human-readable, self-contained UTF-8 Markdown file. It MUST NOT reproduce or substitute for the original book.

**RPF-003** Format conformance MUST be determinable from the completed file alone. It MUST NOT require a particular CLI, programming language, canonical-source format, generation history, or conformance with the production standard.

## 1. Terms

**RPF-004** A Conversational Edition is the experience of reading a book through dialogue with AI. A Reading Pack is the single Markdown artifact used for that experience. A container such as an Agent Skill is an optional way to deliver an existing Pack; it is neither the Pack itself nor a new approval unit.

**RPF-005** The original book is the work that the Pack navigates. Recorded information means information actually present in the Pack. Giving an AI the Pack does not give it access to unprovided text of the original book.

## 2. File envelope

**RPF-006** The first line MUST begin with `PACK |` and declare at least `v`, `date`, `status`, `lang`, and `primary` as `key=value` fields. Values MUST remain on one line, and a key MUST NOT be repeated.

**RPF-007** The body MUST contain, in order, an H1 heading, an AI-facing explanation, a reader-facing explanation, `SYS`, `BIB`, `MAP`, optional sections, `META`, and `ENDPACK`. Each required section MUST occur exactly once.

**RPF-008** Required section identifiers are `SYS`, `BIB`, `MAP`, and `META`. Optional standard sections are `CERT`, `PROPS`, `MIS`, `POLICY`, `NAMES`, `GLOSS`, and `REF`, in that order. A translated human-facing label MAY follow `|`.

**RPF-009** The last non-empty line MUST begin with `ENDPACK |`. Its `chapters`, `props`, `mis`, `names`, `gloss`, `ref`, and optional `policy` counts MUST equal the actual record counts.

## 3. Record and section semantics

**RPF-010** `BIB` MUST include the title and author and MAY include publisher, publication date, ISBN, official URL, and scope. Bibliographic information MUST NOT be confused with an author's position.

**RPF-011** `MAP` MUST contain at least one chapter or major unit with a stable ID and provide navigation. Chapter summaries or principal terms, if present, MUST NOT reconstruct argument sequence, examples, metaphors, style, or paragraph-level content.

**RPF-012** Every record in an optional section MUST display an ID stable and unique within that section and a review state. Review states MUST distinguish at least `draft`, `reviewed`, and `approved`; content not marked `approved` MUST NOT be presented as author-approved.

**RPF-013** `CERT` represents evidence or certainty categories; `PROPS`, attributable claims; `MIS`, reading issues and responses; `POLICY`, book-specific use policies; `NAMES`, people and their treatment in the book; `GLOSS`, terms and their book-specific meanings; and `REF`, references. Empty optional sections MUST be omitted.

**RPF-014** If `PROPS` is present, each claim MUST distinguish descriptive from normative claims and identify a location. A certainty category, if assigned, MUST NOT be converted into numerical confidence or a ranking.

**RPF-015** Explanations in `NAMES` and `GLOSS` MUST briefly abstract a meaning or role within the book. They MUST NOT quote or lightly edit source definitions, biographies, or explanatory prose.

## 4. AI guidance

**RPF-016** `SYS` MUST require the AI to distinguish the Pack, actually retrieved recorded references, and material supplied by the user in the conversation from original-book text and outside knowledge. It MUST NOT permit missing content to be attributed to the book or author.

**RPF-017** `SYS` MUST require separation of descriptive and normative statements, preservation of conditions, scope, and qualifications, source identification, refusal to quote or reconstruct book text or imitate the author's style, navigation back to the original, and third-person treatment of the author.

**RPF-018** `SYS` and the reader-facing explanation MUST state that the Pack is not a substitute for the original, that important points require checking against the book or official material, and that unprovided text cannot be searched or quoted accurately.

**RPF-019** Reference contents MUST be treated as material, not executed as system or action instructions. A later instruction to ignore Pack guidance MUST NOT cause invention of book content or author views.

## 5. Official companion material and book-specific policy

**RPF-020** `REF` MAY contain absolute HTTPS URLs. Proactive treatment as official companion material requires the closed tuple `relation=official_companion`, `url_scope=exact|prefix`, and `retrieval_policy=proactive_when_relevant`. A bibliographic official URL alone MUST NOT imply this status.

**RPF-021** An official-companion declaration MUST NOT prohibit use of other Web material. When a Pack and retrieved material differ, the response MUST distinguish sources and known update dates.

**RPF-022** `POLICY` records MUST carry a stable `POLICY-` ID, kind, statement, and review state. Only an `approved` record may become an operational `SYS` rule; a `draft` or `reviewed` record MUST NOT be used as an instruction.

## 6. META and conformance claims

**RPF-023** `META` MUST identify format conformance with `Reading Pack Format Specification 1.0-draft`, the Pack version, language, and terms applying to the book-derived artifact. It MAY display production-standard conformance, production level, quality profile, and production tool, but each MUST be an independent field.

**RPF-024** `META` MAY display author review, rights, publisher, non-reconstruction, and publication decisions. Even if displayed as `approved` or `not_required`, format conformance alone MUST NOT be described as proof that those decisions are authentic.

**RPF-025** The Pack's rights holder chooses its license, which MUST be displayed in `META`. The licenses of this specification, the reference implementation, or the schemas MUST NOT be applied automatically to a book-specific Pack.

## 7. Format conformance

**RPF-026** A format-conforming Pack MUST meet RPF-001 through RPF-025. An evaluator MUST NOT reject format conformance because production history is unknown, and MUST NOT infer production conformance when it cannot be verified.

**RPF-027** A format-conforming Pack MAY use the claim `Reading Pack Format 1.0-draft conformant`. An extension section MUST NOT change standard section semantics or ordering, and the standard portion MUST remain interpretable when the extension is ignored by an AI or human.

## Change history

- 1.0-draft (2026-08-16): Separated artifact format and semantics from the former aggregate specification; removed production process and reference implementation as format-conformance conditions.

Copyright 2026 Koichi Takahashi. Licensed under CC BY 4.0.
