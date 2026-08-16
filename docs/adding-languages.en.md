# Adding support for another language

[日本語版](adding-languages.ja.md)

Reading Pack currently supports English (`en`) and Japanese (`ja`) as Pack languages. This document is an implementation guide for extending the toolkit. It does not mean that an arbitrary language code can already be used in a project.

The canonical model is already mostly multilingual: a project names one primary language and any other configured language is checked as a translation with the same record IDs, primary-record hashes, and explicit translation status. The remaining limitation is that supported language codes, generated prose, command-line choices, schemas, and parts of the producer and review workflow are closed over English and Japanese.

## Design requirements

An implementation that adds another language must preserve these properties:

- Unchanged English and Japanese canonical input produces byte-identical existing output.
- The supported Pack languages form an explicit, reviewed set. A project cannot activate an unknown language merely by supplying a code or prompt.
- Generated SYS rules remain code-owned, model-independent rules. Project input cannot inject arbitrary instructions into SYS.
- All configured translations retain the primary language's record IDs and are bound to the current primary semantic hashes.
- Author Input Package, author review, release checking, and Agent Skill generation work for every configured language, including projects with three or more languages.
- Language-specific extraction is conservative. Weak linguistic heuristics cannot promote content to approved canonical data.

Use normalized BCP 47 tags for Pack languages, such as `fr`, `de`, `es`, or `zh-Hans`. Reserve `und` for source metadata whose language is unknown; it is not a Pack output language.

## One-time generalization

The first additional language should not be implemented by adding the same code to every existing `ja`/`en` branch. Generalize the following boundaries once.

### 1. Establish one language registry

Create one code-owned registry that defines every supported Pack language and its implementation resources. CLI validation, project creation, producer workflows, review workflows, and semantic validation should read from that registry instead of maintaining separate `{"ja", "en"}` sets.

The registry should identify at least:

- the canonical BCP 47 tag;
- the Pack template and locale catalog;
- the locale used for generated Pack prose;
- any extraction or typography adapter;
- whether a fully localized human-review interface exists.

Registration is an implementation change reviewed with the source code. It must not be supplied by project metadata.

### 2. Separate structural language syntax from runtime support

The JSON Schemas currently enumerate English and Japanese in several artifacts. Replace repeated enums with one shared definition for a bounded, path-safe, normalized BCP 47 tag. Runtime semantic validation must then reject a structurally valid tag that is not present in the language registry.

Source-language fields may additionally accept `und`. Pack-language, primary-language, candidate, ledger, plan, review, and output fields must accept only registered Pack languages.

This separation keeps the public schemas useful without requiring many unrelated schema edits for each newly registered language. It also keeps filenames such as `data/pack.<lang>.json` safe.

### 3. Move generated prose into locale catalogs

Extract language-specific prose from rendering branches into reviewed locale resources. A complete Pack locale must provide:

- section and metadata labels;
- the loading response and capability names;
- the base SYS rules, non-reconstruction rules, and source-use rules;
- official-companion C1-C3 behavior;
- all quality-profile rules;
- claim, certainty, reading-issue, policy, people, glossary, and reference labels;
- wording for provenance, qualifications, update times, and translation rights.

These resources are fixed application data. Do not make a raw project string or author-supplied prompt executable as a system rule.

### 4. Keep review-interface language explicit

Pack language and reviewer-interface language are different choices. A French Pack may still be reviewed through an English form. Do not silently treat every non-Japanese primary language as English.

Either provide a complete review locale for the new language or require an explicit supported review locale. The chosen interface must cover instructions, choices, release signoff, validation errors intended for the reviewer, and the agent-assistance protocol.

### 5. Isolate language-specific analysis

Importing structure is generally Unicode-safe, but candidate recall and layout handling can depend on language. Keep these behaviors behind explicit adapters rather than adding scattered conditionals.

Review at least:

- sentence and token boundaries;
- names, capitalized terms, acronyms, and aliases;
- definition and glossary heuristics;
- combining characters and normalization;
- CJK text without spaces and vertical layout;
- right-to-left display and bidirectional controls.

When no suitable heuristic exists, conservative empty results plus human or model-assisted, source-checked recall are preferable to an English heuristic presented as language support.

### 6. Add a transactional project command

Existing projects do not currently have a supported command for adding a language. Implement a command with behavior equivalent to the following proposed interface:

```sh
reading-pack language add fr --project my-book-pack
```

The operation should atomically:

- add the registered language to `reading-pack.toml`;
- create `data/pack.fr.json` and `templates/pack.fr.md`;
- add the language to the body-free Author Input state;
- initialize translated records with the primary IDs and draft translation status where appropriate;
- validate the complete prospective project;
- leave every file unchanged if validation or writing fails.

Do not implement this as instructions to copy and edit several canonical files manually.

## Adding one registered language

After the one-time generalization, adding a language should require this bounded change set:

1. Register the normalized language tag.
2. Add its Pack locale catalog and template.
3. Add or explicitly select a review locale.
4. Add a conservative linguistic or typography adapter when needed.
5. Add a fully synthetic project fixture; do not use a real book or private production data.
6. Run the acceptance tests below and document any intentionally unsupported feature.

For a newly created three-language project, the intended interface is:

```sh
reading-pack init my-book-pack \
  --title "My Book" \
  --author "Author Name" \
  --lang ja \
  --lang en \
  --lang fr \
  --primary-language ja
```

After a human updates a translation, it is bound to the current primary records and all languages are checked together:

```sh
reading-pack link-translations --project my-book-pack --lang fr
reading-pack build --project my-book-pack --lang all
reading-pack check --project my-book-pack --lang all
reading-pack check --project my-book-pack --lang all --release
reading-pack agent-skill check --project my-book-pack --release
```

These commands illustrate the required interface after support is implemented. They do not work with `fr` in the current release.

## Acceptance tests

A language is supported only when public synthetic tests cover all of the following:

- a single-language project with the new language as primary;
- the new language as a translation of English or Japanese;
- the new language as primary with at least one translation;
- a project containing at least three languages;
- record ID and order parity, primary hash binding, stale detection, and translation approval;
- direct canonical editing and Author Input Package plan/apply;
- aggregate author review, release signoff, and rollback;
- official-companion REF and localized, model-independent SYS generation;
- deterministic Pack and Agent Skill directory/ZIP output;
- manuscript import with representative Unicode, punctuation, and diacritics;
- rejection of unknown, non-normalized, duplicated, excessive, or path-unsafe language tags;
- byte-identical existing English and Japanese fixtures.

For right-to-left or unspaced scripts, add targeted rendering and extraction tests rather than treating generic Unicode acceptance as sufficient language support.

## Documentation and release

When support is complete:

1. Change the README support statement only after every public test passes.
2. Add the language to CLI help and the relevant user guides.
3. Record schema and compatibility effects in the changelog.
4. Build distributions and verify that the new templates and locale resources are included.
5. Run every declared Python version and the complete multilingual release checks.

Until then, describe the language as planned or experimental, not supported.
