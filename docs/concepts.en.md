# Concepts

## Conversational Edition and Reading Pack

A Conversational Edition is a book form intended for dialogue through an AI interface. A Reading Pack is its current portable artifact: one human-readable Markdown file. The distinction lets future interfaces change without changing the production discipline.

The pack behaves like a librarian. It locates material, reports approved summaries, distinguishes kinds of claims, corrects recorded misreadings, and returns the reader to the book. It must not become a compressed substitute for the book.

## Non-reconstruction

The central information-minimization test is: can an AI given the public bundle write something that satisfies the role of the book? A passing bundle states what a chapter addresses and the author's approved position, but omits the argument sequence, examples, metaphors, prose, and paragraph-level coverage. The human review applies to the pack together with every public derivative, not to one file in isolation.

Instructions in SYS are quality guidance, not access control. A later user instruction may defeat them. The durable protection is not to publish reconstructive material.

## Production levels

- Level 1 contains bibliography, extracted structure, navigation indexes, references, deterministic assembly, and editorial checking.
- Level 2 adds reviewed summaries, certainty categories, people, misreadings, AI instructions, model evaluation, and author approval.
- Level 3 adds a canonical claim set with evidence categories and falsification or revision conditions.

Projects may omit optional modules that do not fit the book. Fiction need not imitate a scientific canon; a scholarly monograph need not invent a people index.

A use profile turns that flexibility into a reviewable contract. It declares a minimum level, mandatory modules and chapter fields, critical policies, spoiler boundary, and measured thresholds appropriate to the genre. Profile conformance is all critical gates passing, not an average that lets one strength hide a dangerous omission.

## Canonical sources and generated files

The project TOML, quality plan, source registry, author-input provenance state, language JSON, and templates are canonical. `dist/` is disposable. Import plans, Author Input Package plans, and private candidate runs are review artifacts, not alternate canonical book data. This prevents a second source of truth: correcting a pack means changing structured data or a template and rebuilding.

## Epistemic metadata

`descriptive` records make claims about definitions, observations, constraints, arguments, forecasts, or story facts. They may carry a falsification condition. `normative` records state a value choice or institutional commitment. They may carry a reconsideration condition. A normative choice must not masquerade as an empirical fact.

Certainty categories identify kinds of evidence. They are not numeric confidence scores and must not become a ranking of contributors or ideas.

## Human and AI roles

AI may propose outline, summary, term, person, claim, misreading, and translation candidates. Automated checks may mark a content-hash-bound candidate `ready_for_review`. By default, a named human accepts it for draft application. A test or configured workflow may instead use AI review backed by a decision artifact bound to the exact run and candidate. Acceptance is editorial triage, not author approval. An author or editor determines accuracy, certainty, counterconditions, non-reconstruction, rights, and publication. No command can turn inference into author approval.

## Bilingual records and freshness

`primary_language` chooses the authoritative language. `languages` declares the generated set. Each collection uses stable common IDs in the same order. A secondary record contains the SHA-256 of the corresponding primary record's semantic value. Editing primary wording, headings, links, or structure makes the secondary record stale. Review-state changes do not.

`link-translations` records hashes after a human has revised the translation. It resets affected records to draft so a hash update cannot silently preserve an old approval.

## Release readiness

Technical validation checks shape and consistency. Release validation adds current hash-bound authority, measured profile thresholds, rights, author, publisher, non-reconstruction, and publication gates. `publisher_review = not_required` is a recorded human determination, not a default shortcut.

Copyright 2026 Koichi Takahashi / 高橋恒一. Licensed under CC BY 4.0.
