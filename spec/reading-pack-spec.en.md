OVERVIEW | name=Reading Pack standards suite | version=1.0-draft | language=en | primary=false | date=2026-08-16 | author=Koichi Takahashi | license=CC BY 4.0

# Reading Pack standards suite 1.0-draft

The public norms for Reading Pack are divided into three layers: the artifact, its production process, and the reference implementation. This document is an entry point and is not itself a conformance target.

The Japanese version is canonical. The English version has the same structure and requirement IDs. If they differ, the Japanese version controls and both versions must be corrected in the same commit. Compatibility between drafts is not guaranteed.

## Three documents

| Document | Defines | Does not define | Example claim |
|---|---|---|---|
| [Reading Pack Format Specification](reading-pack-format-spec.en.md) | Structure, semantics, and safety boundary of the single Markdown delivered to readers | Which tool or production process created it | `Reading Pack Format 1.0-draft conformant` |
| [Reading Pack Production Standard](reading-pack-production-standard.en.md) | Levels 1–3, W0–W13, evidence, review, evaluation, and publication gates | A programming language, CLI, or internal implementation | `Reading Pack Production 1.0-draft Level 2 beta` |
| [reading-pack Reference Implementation Profile](reading-pack-reference-implementation.en.md) | This repository's project format, CLI, import, transaction, and plugin boundaries | Conditions another implementation must meet to claim Reading Pack conformance | `Built with reading-pack toolkit 0.5.0` |

## Independent conformance

Format conformance is determined solely by inspecting a completed Reading Pack. Production conformance is determined by inspecting the production records, evidence, human decisions, and measured evaluation. Use of the reference implementation is required for neither.

The following combinations are therefore valid:

- Build a format-conforming Pack with an independent tool.
- Claim format conformance but not production conformance for an independent process.
- Use this toolkit without claiming production conformance while author review remains incomplete.
- Use another implementation and claim both format and production conformance.

The former aggregate label `Reading Pack Specification 1.0-draft` is deprecated. New Packs identify format and production conformance separately.

## Norms and implementation diagnostics

Published schemas are machine-readable contracts referenced by either the format specification or the production standard. CLI diagnostic identifiers such as `RP` and `QP` are stable identifiers of the reference implementation, not requirement numbers in the standards suite. Retaining those diagnostic identifiers does not imply conformance with the former aggregate specification.

## License

The format specification and production standard are published under CC BY 4.0. Modification, independent implementation, and use in commercial services are permitted. Attribution should identify Koichi Takahashi, the document title, version, and reference URL.

Suggested citations:

- Takahashi, Koichi (2026). “Reading Pack Format Specification 1.0-draft.” `https://github.com/ktakahashi74/reading-pack/blob/main/spec/reading-pack-format-spec.en.md`
- Takahashi, Koichi (2026). “Reading Pack Production Standard 1.0-draft (beta).” `https://github.com/ktakahashi74/reading-pack/blob/main/spec/reading-pack-production-standard.en.md`

This license is not automatically applied to a particular book's manuscript, structured data, or generated Reading Pack. Their rights holders choose separate terms.

Copyright 2026 Koichi Takahashi. Licensed under CC BY 4.0.
