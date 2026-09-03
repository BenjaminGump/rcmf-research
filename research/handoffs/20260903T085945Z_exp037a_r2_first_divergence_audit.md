# EXP-037A-R2 First-Divergence Audit Handoff

Timestamp: `2026-09-03T08:59:45Z`

## Identity

- Repository: `BenjaminGump/rcmf-research`
- Starting branch: `research/v6-rcmf-reproducible-pipeline-d08-repair`
- Starting commit: `255eec178e6e5a1a0e3d0d1f4d246c240a4f6ecb`
- Diagnostic branch: `research/v6-rcmf-exp037a-first-divergence-audit`
- Run ID: `rcmf_exp037a_r2_first_divergence_audit_14d_20260903_001`
- Lambda diagnostics root:
  `/lambda/nfs/rcmf-persist/project/runs/diagnostics/rcmf_exp037a_r2_first_divergence_audit_14d_20260903_001_published`
- Global seed: `25101`
- Scientific result: `NOT_EVALUATED`
- Decision: `CAUSE_IDENTIFIED`
- Optimizer updates/backward passes: `0/0`

## Verified Result

Historical `366` is independently proven real by the historical paired-outcome
artifact and the EXP-031A data/training-unit manifests. All 366 scoreable train
rows existed and were consumed as correct training units; the heldout count was
98.

The fresh reconstruction diverges at two upstream contracts:

1. `L4_selector_training_provenance`: it substitutes the downstream query-task
   split for the historical selector parent split. This moves 97,734 of
   310,433 legal pair labels, changes CV folds, and changes the winner from
   `hard_lr3e4_e120_t075` to `balanced_lr3e4_e60_t1`.
2. Paired-panel admission: it uses the historical post-outcome count 366 as an
   a-priori quota, producing 464 initial rows and `minimum_per_label=0`.
   Historical preparation used 256 initial rows, maximum 499, and
   `minimum_per_label=40`, so it expanded through all 499 states.

Exact train-row reconciliation:

```text
366 historical
- 25 historical completions omitted before fresh generation
-  1 historical completion changed to fresh over-context
+  2 fresh-only completions
= 342 fresh
```

The single in-panel historical loss is
`appworld:trace:afc0fce_1:step:13:line:69`: identical 23,205-token base prompt,
but historical/fresh memory increments are 11,045/19,981, producing totals
34,250 PASS and 43,186 OVER_CONTEXT against the identical 40,960 limit.

## Fresh Missing-24 Clarification

The fresh attempted panel has 19 over-context and five replay-semantic missing
rows, with zero overlap. The previously cited 23 over-context rows include
four unattempted expansion slots. Only one of the fresh missing 24 was a
historical scoreable row; the other 23 were already historical failures.

The exact IDs and L0-L8 ladder are in
`research/analysis/exp037a_r2_first_divergence_rows.jsonl`.

Selector transition-ID agreement is:

- all 499: `367/499`
- historical completed train: `271/366`
- fresh missing 24: `20/24`
- matched successful controls: `18/24`
- heldout: `68/98`

Thus the missing 24 are not enriched for changed selected memories. The
dominant count loss is panel omission; a changed longer memory directly causes
one net historical completion loss.

## Historical Selector Safety

The historical selector checkpoint was never loaded, deserialized, or
executed. No historical q/k tensor or historical decision was used as fresh
scientific input. Historical JSON/text selections were inspected only to
answer what the historical run recorded.

## Evidence Gap

Historical paired rows preserve exact rendered base-prompt SHA256 and token
count but not separate structured-message-array or input-token-ID SHA256.
Rendered prompt hashes and token counts match for all 499 states. This gap is
not material to the directly evidenced split/admission count cause.

## Artifacts

- Human report:
  `research/analysis/EXP_037A_R2_FIRST_DIVERGENCE_AUDIT.md`
- Machine summary:
  `research/analysis/exp037a_r2_first_divergence_summary.json`
- Per-state ladder:
  `research/analysis/exp037a_r2_first_divergence_rows.jsonl`
- State-set comparison:
  `research/analysis/exp037a_r2_state_set_comparison.json`
- Selector provenance:
  `research/analysis/exp037a_r2_selector_provenance_comparison.json`
- Context/replay evidence:
  `research/analysis/exp037a_r2_context_budget_comparison.jsonl`
- Historical ordered state manifest:
  `research/analysis/exp037a_r2_historical_state_manifest.json`
- Input/output index:
  `research/analysis/exp037a_r2_artifact_index.json`
- Audit utility:
  `scripts/audit_exp037a_first_divergence.py`
- Focused tests:
  `tests/test_exp037a_first_divergence_audit.py`

Published artifact-index SHA256:
`4f0920551b67e8c856ddfdf5fbc03811a0c72cd32d3f983eb50807c360bfed2d`.

## Verification

- Focused local tests: `10 passed in 0.17s`.
- Focused Lambda tests: `10 passed in 0.07s`.
- Full local tests with process-start `PYTHONHASHSEED=25101`:
  `837 passed, 2 skipped in 19.04s`.
- JSON and JSONL parse checks passed.
- Established Git-safe secret patterns found zero matches in all new files.

## Proposed Repair - Not Executed

Restore the historical selector parent split and the 256/499/40 panel
admission contract, retrain the selector from authoritative inputs without
loading historical weights, and require exact pre-generation set/fold/winner/
selection/context invariance before another scientific run.

No repair, D08 preparation, optimizer work, `_002` run, 1D arm, Test-Normal,
or follow-on experiment was started.

## Deviations

The first local focused pytest invocation encountered Windows default-temp
permission errors in five fixture tests. The unchanged suite passed 10/10
with a repository-local ignored `--basetemp`. Append-only instrumentation
trial roots preceded the final published diagnostics root; no immutable run
root was mutated.

The first unseeded full-suite invocation reached the intentional EXP-036C
collection guard; the required process-start `PYTHONHASHSEED=25101` rerun
passed completely.
