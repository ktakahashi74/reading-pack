# Finite generator-model comparison

This optional feature compares generator models while fixing the manuscript, workflow, quality criteria, judge and resource limits. It is not required for routine production or individual Pack acceptance and does not change the reader-facing file format.

Start with zero repairs. A comparison allowing at most one repair is a separate, preregistered condition shared by every model. Never add stages, repetitions or models in response to results. Model names do not define a capability ranking. Freeze exact model IDs, effort settings and adapter files.

## Preparation and execution

Reuse `start --prepare-only` and the existing finite artifact studies. Prepare a separate fresh run for each model, case and repetition. The following two-model, one-case, one-repetition example illustrates the interface, not measured quality or a recommended budget.

1. Follow the [production guide](pipeline-artifact-workflow.en.md) to create two recipes with `artifact-acceptance-1` and `operating_envelope.mode: qualification`. Use `--repair-rounds 0` to omit repair. Only the generator model, its adapter `--model` argument and per-worker `--audit-dir` may differ. Pass model IDs to the same adapter rather than using separate scripts with hardcoded models. Keep the judge and its configuration identical.
2. Prepare all runs without model calls.

```sh
reading-pack pipeline start book.md --run private/a-0 --recipe a.json --prepare-only
reading-pack pipeline start book.md --run private/b-0 --recipe b.json --prepare-only
reading-pack pipeline plan --run private/a-0
reading-pack pipeline plan --run private/b-0
```

3. Create `comparison-definition.json`. Use absolute run paths. The following monetary and time values illustrate the format; replace them with your recipe totals and actual cumulative budget. Unknown prior cost must not be entered as zero. Record the location of the budget ledger or authorization in `basis`.

```json
{
  "schema_version": 1,
  "variants": {"a": "generator-model-a", "b": "generator-model-b"},
  "cases": {"body-only": "book-id"},
  "repetitions": 1,
  "book_budgets": {
    "book-id": {"prior_cost_usd": 20, "max_cost_usd": 100, "basis": "private/budget-ledger.json"}
  },
  "max_cost_usd": 80,
  "max_wall_seconds": 14400,
  "minimum_completion_rate": 0.9,
  "equivalence_margin": 0.05,
  "trials": [
    {"variant": "a", "case": "body-only", "repetition": 0, "run": "/absolute/private/a-0"},
    {"variant": "b", "case": "body-only", "repetition": 0, "run": "/absolute/private/b-0"}
  ]
}
```

4. Register the complete matrix, then execute within the authorized input, destination and spending scope.

```sh
reading-pack pipeline comparison-register --definition comparison-definition.json --output private/comparison
reading-pack pipeline comparison-run --comparison private/comparison --output private/comparison-result.json
reading-pack pipeline comparison-report --comparison private/comparison --output private/comparison-report.json
```

Registration checks all models × cases × repetitions, identical inputs and common workflow, judge and resource limits. It sums all trial monetary allowances and deadlines, checking the comparison limit and each book's cumulative limit including prior cost. Insufficient budgets produce a saved `admitted: false` plan without executing trials. The budget `basis` records the operator's declaration; it does not authenticate or fetch external accounting records.

Trials run in the registered list order. To reduce time-of-day effects, alternate model order when registering repetitions. Re-execution handles only untouched slots and keeps the original comparison deadline. Started, failed and unknown-outcome slots are never automatically retried. Retain all run directories as immutable evidence; moving or editing them can invalidate reporting.

## Interpreting the report

Per-model results include completion rate over every registered slot, completion within resource limits, acceptance counts, known provider-reported cost, calls with unknown cost and individual elapsed times. Completion means direct `pass` and delivery of an author packet, not author adoption. Completion within limits additionally requires known cost and evidence of termination within the deadline and monetary limit. Unstarted costs and times are not measurements of zero.

Each trial's `inspection` retains coverage, defects and unresolved issues. Counts depend on generated records and inspection partitioning, so they are not standalone ranking scores. `fresh_live_measurement` revalidates underlying provider responses. Synthetic workers, existing-candidate reassessments and reused responses do not establish fresh live production. Reported costs are not invoices.

`comparisons` reports paired differences in completion within limits and conservative intervals. It uses simultaneous 95% Hoeffding intervals across model pairs and lower bounds for the absolute completion target, assuming independent executions conditional on the fixed cases. It does not model correlated outages or generalize to untested books.

A pair has `status: within_margin` only with live evidence for all pairs, an absolute target supported by its lower bound and the entire difference interval inside `±equivalence_margin`. Otherwise the status is `inconclusive`. One-shot ties, nonsignificant differences and equally low quality do not establish saturation.

This statistic concerns a binary completion endpoint. It does not establish equivalence in reader usefulness or absence of important shared omissions; `quality_saturation` therefore remains `not_established`. Inspect artifacts against their sources with generator identities hidden, including a sample of passing artifacts, to check for shared judge omissions and an overly coarse evaluation scale. Comparison results never change Pack acceptance, author approval or publication state.
