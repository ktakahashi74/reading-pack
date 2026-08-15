# Author Input Package

An Author Input Package (AIP) switches, module by module, between information supplied by an author, editor, publisher, or rights holder and information produced by the generation workflow. It covers appendices, chapter structure, author summaries, people, terms, canonical claims, independent Q&A, book-specific policy, and references.

## 1. Modes and precedence

New packages declare all ten modules exactly once: `chapters`, `summaries`, `chapter_terms`, `certainty`, `claims`, `qa`, `policy`, `names`, `glossary`, and `references`. A legacy nine-module package without `policy` remains readable and is deterministically treated as `policy: generate`.

- `provided` treats the supplied file as the complete authoritative set and replaces the existing module. For `summaries` and `chapter_terms`, it clears that field on every chapter before assigning the supplied values.
- `augment` replaces matching record or chapter IDs and preserves existing records that were not supplied.
- `generate` leaves the canonical module unchanged for the ordinary book-derived generation workflow.
- `omit` empties the module. `chapters` cannot be omitted.

Selecting `provided` or `augment` is not approval. Application ignores supplied `status` and `review_notes` and writes every changed record as `draft`. Final author review and the normal release gates remain separate.

## 2. Directory format

Create a template first:

```console
reading-pack author-input template ./author-input-2026-08 \
  --package-id AIP-BOOK-202608 \
  --lang en \
  --authority-type author \
  --authority-name 'Author Name'
```

The directory contains `author-input.json`, ten JSON templates, and a README. The manifest and every referenced file must be direct children of the same directory; absolute paths and subdirectories are rejected. `authority.type` is one of `author`, `editor`, `publisher`, or `rights-holder`. `SRC-1` remains reserved for the primary book.

The complete manifest format is defined by `schema/author-input-manifest.schema.json`. A module using `provided` or `augment` adds `file`, `format` (`json` or `csv`), and a unique `source_id`; it may override the default support-source `role`. A `generate` or `omit` declaration contains only `mode`.

## 3. Module files

JSON uses this envelope; record fields are defined by `schema/author-input-module.schema.json`:

```json
{
  "schema_version": 1,
  "module": "names",
  "records": [
    {
      "id": "NAME-001",
      "name": "Person Name",
      "aliases": ["Alternate form"],
      "chapter_id": "CH-01",
      "book_context": "Who the book presents this person as and which work, view, quotation, or evaluation it connects to them."
    }
  ]
}
```

CSV must be UTF-8. Headers are fixed and array cells use `|` separators:

| module | CSV header |
|---|---|
| `chapters` | `id,kind,title,pages,sections,summary,terms,contributors,aliases,learning_objectives,prerequisites,spoiler_scope,source_locations` |
| `summaries` | `chapter_id,summary` |
| `chapter_terms` | `chapter_id,terms` |
| `certainty` | `id,label,definition,source_locations` |
| `claims` | `id,layer,kind,statement,chapter_ids,certainty_id,falsifiability,revision_conditions,source_locations,reader_note` |
| `qa` | `id,kind,issue,response,impact,remaining_uncertainty,chapter_ids,claim_ids,anchor,source_locations` |
| `policy` | `id,kind,statement,source_locations` |
| `names` | `id,name,aliases,chapter_id,book_context,source_locations` |
| `glossary` | `id,term,aliases,chapter_id,book_meaning,source_locations` |
| `references` | `id,url,label,source_locations` or `id,url,label,relation,url_scope,retrieval_policy,source_locations` |

IDs and cross-references must satisfy the canonical schema. The plan reports added, replaced, removed, and preserved IDs so reviewers can verify `provided` completeness and `augment` precedence.

`source_locations` is an optional record-level provenance locator for every canonical record type. It preserves authority-supplied source paths, anchors, or producer-verified normalized-text ranges separately from the module source ID/hash and canonical `chapter_ids`. `reader_note` retains an authority-supplied note without folding it into the claim statement. `anchor` retains the stable official-page fragment for a classified Q&A record. Locators must not be generated speculatively.

The neutral Q&A text field is `issue`. Legacy JSON or CSV using `misreading` remains accepted, but exactly one of the two fields is allowed and legacy input is normalized to `issue` when applied.

`policy` carries closed, book-specific records with an ID, `kind`, and `statement`. Its kinds are `authority_order`, `language_precedence`, `translation_rights`, `retrieval`, `publisher_relation`, `usage_terms`, and `other`. A policy is rendered for review while `draft` or `reviewed`, but only a human-approved record becomes an operational SYS rule. This module does not turn arbitrary attachment text into instructions or confer legal permission.

An authority may mark a reference as official companion material by supplying all three closed fields: `relation=official_companion`, `url_scope=exact|prefix`, and `retrieval_policy=proactive_when_relevant`. This causes REF metadata and fixed proactive-reference SYS rules to be generated; it does not accept raw prompt text. Companion URLs must be HTTPS, at most 2,048 characters, credential-free, unique among companion declarations, and limited to 32 per language. A prefix ends in `/` and has no query or fragment. Legacy CSV headers without `source_locations`, including three-column reference CSV and Q&A using `misreading`, remain valid.

Each AIP targets one language. In a bilingual project, pass the matching primary- and secondary-language packages to one plan. The planner applies the primary package in memory first, derives secondary `source_hash` values from that prospective primary data, and validates the complete resulting data set. A one-package plan remains valid only when it preserves shared IDs, ordering, and translation freshness by itself.

## 4. Appendices and independent Q&A

`attachments` hash-register supplied raw materials in the source ledger without storing source prose or a local path. Registration alone never silently converts an attachment into a claim, term, or misreading correction.

Independent Q&A has two distinct routes:

1. When the supplier specifies canonical records, structure them as the AIP `qa` module and choose `provided` or `augment`. Explicitly classify each record as `misreading`, `clarification`, `open_objection`, or `author_update`.
2. When starting from raw Org or JSON Q&A, register it as `author-qa` and use `reading-pack qa plan/classify/candidates`. The workflow must not infer that every criticism is a misreading.

The first route is authority-classified canonical input. The second is an evidence-bound candidate workflow. They are deliberately not interchangeable.

## 5. Plan, apply, and record

```console
reading-pack author-input plan ./author-input-ja ./author-input-en \
  --project ./my-pack --output ./author-input-plan.json

# Review the package and the plan's added/replaced/removed/preserved ID lists.

reading-pack author-input apply ./author-input-plan.json \
  --package ./author-input-ja --package ./author-input-en \
  --project ./my-pack

reading-pack author-input report --project ./my-pack
reading-pack validate --project ./my-pack
reading-pack build --project ./my-pack
```

The aggregate plan carries no supplied prose or local paths. It is bound to every manifest and supplied-file fingerprint plus one hash of the complete before data set and one hash of the complete prospective data set. Package languages, package IDs, and source IDs must be unique. Apply reloads all packages under one project lock, rejects any changed package or intervening canonical edit before writing, and uses one recoverable prepared state for every changed language file, `sources.json`, and `author-input-state.json`, without claiming filesystem-wide transaction atomicity.

`author-input-state.json` records, per language and module, the current mode, package ID, authority, manifest hash, source identity, supplied record IDs, semantic hashes, post-apply count, and package history. `validate` reports `RP502` if authority-provided canonical content changes without a new recorded input.

`author-input report` is a body-free mode, count, and source report. Use [agent-assisted Markdown author review](author-review.en.md) for final content decisions. Matching AIP records may be decided in evidence-bound groups, and an agent may explain exceptions and help fill the form. The edited Markdown remains the human decision record. Author corrections overlay the original AIP provenance as body-free before/after hash history. Candidate triage remains a separate `reading-pack review bundle` workflow.
