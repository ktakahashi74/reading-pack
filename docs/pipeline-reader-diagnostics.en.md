# Separating reader evaluation from Pack acceptance (M3)

Under `artifact-acceptance-1`, a wrong reader answer does not by itself fail the Pack. A correct answer does not excuse a defect in the Pack either. M3 connects this boundary to evaluation components; it does not complete the direct-check production workflow.

| Component | Artifact contract | Legacy contract |
|---|---|---|
| Standard question generation | Returns an empty suite without generating or saving questions | Keeps frozen development/holdout questions |
| Standard answer evaluation | Returns `reader_diagnostics / not_run` without worker calls | Keeps existing answers, grading and gates |
| Single-case and batch evaluator components | Return model diagnostics | Preserve existing `passed` and `failures` |
| Source-audit requirements | Do not import questions or reader results into content requirements or the audit-contract hash | Preserve the original frozen contract |
| Author handoff | Rejects using legacy reader success to create artifact-contract approval forms | Keeps existing conditions |

New diagnostic results identify themselves with `kind = reader_diagnostics` and have no Pack-gating `passed` or `failures` fields. Wrong answers, grading uncertainty, critical errors and grader findings remain observations bound to each case and repetition. `critical_errors` counts actual critical findings, while `uncertain_grades` counts uncertain grades. A completed diagnostic is not quality acceptance, and a diagnostic alone does not establish whether the cause lies in the model or the Pack.

Pack, case-set and model-setting bindings and answer-quotation checks are retained. Invalid quotations, source evidence, cross-case batch evidence and requirement-count mismatches remain errors rather than completed diagnostics. Saved jobs and resource records from an interrupted evaluation are not evidence of successful completion.

New runs freeze the `reader-utility-artifact-1` purpose contract, distinguishing direct acceptance from optional diagnostics while retaining central meanings, material conditions, source fidelity and specific source navigation. Existing runs retain their own purpose contracts and judgments; saved answers are not silently regraded.

## Current execution scope

[Direct artifact production](pipeline-artifact-workflow.en.md) now connects the content inventory, instructions, delivery, bounded repair and M1 author records. The standard workflow invokes no reader tests. Single and batch diagnostics remain independent components; no additional diagnostic CLI or live experiment is enabled by these changes. Legacy reader success still cannot enter the artifact author gate. Synthetic separation tests do not establish real-book or model quality.
