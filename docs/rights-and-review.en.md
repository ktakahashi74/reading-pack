# Rights and review

The toolkit's license does not grant rights in a source book or in a generated pack. A rights holder must review each project separately.

## Repository licenses

| Material | License |
|---|---|
| Python code, CLI, validators, and tests | MIT |
| Specifications, documentation, schemas, prompts, and READMEs | CC BY 4.0 |
| Synthetic Clockwork Garden example | CC0 1.0 Universal |
| A user's manuscript, structured data, and generated pack | Chosen by that project's rights holder |

Copyright in the repository documentation is held by Koichi Takahashi / 高橋恒一, 2026. Creative Commons terms do not apply to software code. Apache-2.0 is not used in this release because its express patent grant requires a separate policy decision.

## Material-by-material review

Confirm authority to use:

1. title, subtitle, chapter and section headings;
2. pagination and publication metadata;
3. author-written summaries and corrections;
4. glossary and index terms;
5. canonical claims and epistemic metadata;
6. cover art, logos, quotations, images, and third-party text;
7. translations and translated headings;
8. external links and any copied descriptions.

Exclude material beyond the available permission. Do not assume that permission to publish a book includes permission to publish a separate structured derivative, or that ownership of original text includes third-party quotations and images.

## Confidentiality and external AI

The manual workflow can be completed without AI. Before sending an unpublished manuscript or extract to an external AI provider, review the publishing agreement, confidentiality duties, provider terms, retention, training use, data location, and account controls. Obtain publisher or co-author guidance when authority is unclear. The local CLI never sends data externally.

AI prompts in this repository minimize requested text, prohibit long reconstruction, and label output as a candidate. They cannot enforce a provider's data policy.

## Author review

Author review covers wording, omissions, certainty categories, falsification and revision conditions, misreading corrections, names, glossary terms, translation, and whether public outputs reconstruct the original. Approval attaches to a specific version and content hash. It does not automatically carry across an edited source.

## Publisher review

Publisher review is required when the publishing agreement, book-derived material, branding, translation rights, or distribution relationship makes it relevant. `not_required` records a reasoned determination by a human; it does not mean “not yet asked.” Keep the rationale in the project's review record.

## Non-reconstruction review

Test the public bundle without relying on SYS. Ask a model to produce a substitute chapter from the data portion alone, then have a human judge whether it recovers argument order, examples, metaphors, prose, or a satisfying substitute reading experience. Use a predeclared rubric. If it fails, reduce published content; adding stronger instructions is not sufficient.

## Publication decision

Only a human sets `publication_decision = approved`. `reading-pack check --release` confirms that the decision and its prerequisites are recorded; it does not make the decision.

Copyright 2026 Koichi Takahashi / 高橋恒一. Licensed under CC BY 4.0.
