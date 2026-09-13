# Selecting and freezing the acceptance contract (M2)

Each new run freezes its acceptance contract. Omission selects `legacy-reader-evaluation-1`; explicit `artifact-acceptance-1` selects the [direct production workflow](pipeline-artifact-workflow.en.md). `start --prepare-only` saves inputs without model calls, `plan` checks finite allocations, `resume` runs the selected contract, and `finalize` imports that contract's author decision. `contract_execution_supported` indicates implementation availability, not sufficient resources or quality acceptance.

Unknown versions and recipe/manifest conflicts are rejected. Historical manifests without a contract field remain legacy and are not rewritten. Engine hashes and source identity still apply: changing the engine does not make an old run resumable.

`restart` inherits the frozen contract and rejects a different explicit contract before creating a successor. Cross-contract worker-response reuse is rejected. Use `reassess-artifact` for a saved old candidate; it preserves origin evidence, cumulative calls and the original monetary envelope and deadline. Old approvals and semantic judgments do not confer new acceptance. Historical workflow signatures retain their original calculation.

Add `--contract-version artifact-acceptance-1` to `pipeline recipe` to choose the new contract. A non-enveloped research recipe still requires explicit `--experimental`. Fixed profiles, call limits and inspection limits are described in the workflow guide.
