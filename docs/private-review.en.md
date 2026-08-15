# Private candidate review

`candidates review` creates a static HTML page that places a candidate beside its current canonical record and shows short evidence context re-resolved from the exact source at render time. It does not change canonical data, candidate state, or acceptance metadata.

```sh
reading-pack candidates review my-pack/.reading-pack/runs/run-001 \
  --source book.pdf --project my-pack --output review-001.html
```

Output is restricted to the immediate `my-pack/.reading-pack/reviews/` directory. Files use mode `0600` and directories use mode `0700`. External or nested destinations, symlinks, and overwriting an existing file are refused. The HTML has no scripts, external resources, or forms. It is nevertheless private material because it contains dynamically re-resolved source excerpts and candidate prose. Delete an obsolete review file explicitly when it is no longer needed.

Before rendering, the command rechecks:

- candidate-manifest integrity;
- source name, SHA-256, and normalized-text hash;
- every evidence span's position, hash, and candidate-record binding; and
- the all-language canonical snapshot from candidate-run creation.

If the source or canonical data changed, rendering stops without producing HTML. Use one `--id` per candidate to limit the display. Omitting IDs displays all candidates but does not create an accept-all action. According to state, each candidate shows accept, reject, or apply commands containing exactly one candidate ID.

```sh
reading-pack candidates review my-pack/.reading-pack/runs/run-001 \
  --source book.pdf --project my-pack \
  --id CAND-0123456789ABCDEF0123 --output claim-review.html
```

An excerpt-free semantic review can be shown alongside structural QA findings. It must be bound to the exact, unchanged candidate-run integrity hash; an accepted, rejected, or otherwise modified run therefore needs a fresh semantic-review artifact.

```sh
reading-pack candidates review my-pack/.reading-pack/runs/run-001 \
  --source book.pdf --project my-pack \
  --semantic-review my-pack/.reading-pack/semantic/review-001.json
```

## One-stop cross-workflow review

`review bundle` combines multiple candidate runs into one human-oriented page. It groups chapter structure, chapter summaries, claims, certainty definitions, people, terms, references, and author Q&A while continuing to show the current canonical record and dynamically re-resolved evidence. Each run must be paired with the exact source from which its evidence was created, so the primary book and an independent author-Q&A appendix can be reviewed together without merging their provenance.

```sh
reading-pack review bundle --project my-pack \
  --artifact my-pack/.reading-pack/runs/content-001 book.pdf \
  --artifact my-pack/.reading-pack/runs/catalog-001 book.pdf \
  --artifact my-pack/.reading-pack/runs/qa-001 appendix.org \
  --ledger catalog-001 my-pack/.reading-pack/catalog-001-ledger.json \
  --catalog catalog-001 my-pack/.reading-pack/catalog-001.json \
  --output one-stop-review.html
```

Use the manifest's exact `run_id` as the first value of each optional `--ledger RUN_ID FILE`, `--semantic-review RUN_ID FILE`, or `--catalog RUN_ID INVENTORY` pair. A semantic review requires the reconciled ledger for that run. A catalog inventory adds extraction counts, unresolved people/term signals, and chapter-map review status without rendering its labels in the catalog report; it is rebound to the run, source, canonical snapshot, normalized text, and each chapter-span hash. Supplying no artifact for a section is displayed as unavailable or incomplete; it never becomes an assertion that the source contains no such material.

The bundle has the same owner-only directory, `0600` file, no-overwrite, no-symlink, no-script, and no-external-resource protections as a single-run review. Before output, it rechecks every manifest, exact source, normalized evidence span, optional catalog inventory, ledger and semantic binding, and the current all-language canonical snapshot. It changes no canonical or private-run state and exposes no accept-all operation. A reviewer must record every accept, reject, or later apply decision separately by candidate ID. Even a fully reviewed bundle is not author or publication approval.

An evidence match proves only that the same normalized span occurs in the source. The reviewer must still decide whether the span supports the proposal, preserves qualifications, exceptions and uncertainty, and has correct attribution. Acceptance still permits only a canonical `draft`; it is not author or publication approval.

## AI review option

To replace human candidate review with AI review, pass `--reviewer-type ai` and `--review-artifact` to `accept`. The excerpt-free artifact records, per candidate, the exact run integrity, candidate ID, record hash, evidence artifact hash, model name, method, timestamp, and the source-support, semantic-fidelity, and scope/qualification checks. A stale run, another candidate, missing check, or blanket acceptance is rejected.

```sh
reading-pack candidates accept my-pack/.reading-pack/runs/run-001 \
  --id CAND-0123456789ABCDEF0123 \
  --reviewer "model-id" --reviewer-type ai \
  --review-artifact my-pack/.reading-pack/ai-review-run-001.json
```

AI acceptance is still draft editorial triage. It grants no author approval, rights decision, or release-gate approval.
An AI rejection can be recorded by setting `decision=reject` in the same artifact format and passing the same three options to `candidates reject`.

Copyright 2026 Koichi Takahashi / 高橋恒一. Licensed under CC BY 4.0.
