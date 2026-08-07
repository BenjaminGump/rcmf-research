# Stage C1 5B Corrected Leave-One-Out Diagnostics

Date UTC: 2026-08-07

## VERIFIED

- Scope respected: existing Stage-C1 checkpoints only; no retraining, no Stage
  C2, no selector/program/injector/Qwen fine-tuning, no AppWorld
  generation/evaluation, and no teacher-response cache regeneration.
- Source commit: `f998a45e2889802d0ba06dd00757461b1ebf16c5`.
- Artifact:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c1/stage_c1_5b_diagnostics_20260807_001`.
- Response cache revalidation passed: 638 states, 0 errors.
- Runtime: 5,757.08 seconds, about 1.60 H100 hours.
- Local tests: `80 passed`.
- Lambda Stage-C1 mask tests: `11 passed`.

## Mask Bug

The original Stage-C1 leave-one-out audit is invalid. It set
`legal_effective_mask[remove_index] = False`, but `_compute_z()` called
`build_include_mask(..., validation_full_bank=True)`, which rebuilt validation
masks as all true. The requested memory was not removed.

Commit `f998a45` adds explicit `include_mask_override` support. Normal
validation still uses the complete 36-memory train bank; counterfactual
validation now supplies an explicit mask.

## Corrected Leave-One-Out

All 115 positive-teacher validation states were audited for all three seeds.
Effects are `NLL_without_memory - NLL_full`.

- teacher-best: mean `0.002334`, CI `[0.000444, 0.004588]`, median `0.000359`
- neutral: mean `0.001784`, CI `[0.000353, 0.003362]`
- most-negative: mean `0.001118`, CI `[-0.000598, 0.002874]`
- random-valid: mean `0.001864`, CI `[0.000115, 0.003814]`
- selector-top: mean `0.006578`, CI `[0.002255, 0.012133]`
- largest-contribution: mean `0.002892`, CI `[-0.002419, 0.008387]`

Teacher-best minus selector-top was negative: mean `-0.004244`, CI
`[-0.008464, -0.000886]`. The selector-top memory had a larger compiled
behavioral effect than the raw-teacher-best memory.

## Selector Alignment

- teacher-best Recall@1: `0.113043`
- teacher-best Recall@4: `0.313043`
- teacher-best Recall@8: `0.466667`
- teacher-best signed-score rank mean/median/p75/p95:
  `12.521739 / 10 / 20 / 32`
- negative signed-score fraction for teacher-best memories: `0.243478`
- teacher utility vs signed score: Pearson `0.168978`, Spearman `0.271534`

## Contribution Decomposition

Teacher-best memories are usually not dominant in the compiled field:

- mean fraction of summed contribution norm: `0.035032`
- median fraction of summed contribution norm: `0.032790`
- mean fraction of numerator norm: `0.043474`
- median contribution rank: `13`

Across 14,790 valid teacher rows:

- teacher utility vs analytic `||delta_z_i||`: Pearson `0.002932`,
  Spearman `0.024077`
- signed score vs analytic `||delta_z_i||`: Pearson `-0.049890`,
  Spearman `0.042973`
- gate contribution norm vs analytic `||delta_z_i||`: Pearson `0.942373`,
  Spearman `0.987043`

## All-Memory LOO Subset

The 32-positive-state all-memory compiled LOO subset produced 3,456
state-seed-memory rows.

- compiled effect mean/std/median/p95/max:
  `0.000672 / 0.012188 / 0.000095 / 0.012659 / 0.198074`
- compiled effect vs raw teacher utility: Pearson `-0.006813`, Spearman
  `-0.010966`
- compiled effect vs signed score: Pearson `-0.035242`, Spearman `-0.043908`
- compiled effect vs analytic delta-z norm: Pearson `0.047080`, Spearman
  `0.007785`
- effect top-1 matched raw-teacher-utility top-1 in `0.041667` of cases
- effect/raw-utility top-4 overlap: `0.135417`
- effect/raw-utility top-8 overlap: `0.225260`

## Content vs Free-ID

Paired differences are content minus free-ID.

- all target NLL: mean `0.007893`, CI `[0.000152, 0.015463]`
- positive-teacher target NLL: mean `0.009981`, CI `[0.001650, 0.018212]`
- baseline/no-positive target NLL: mean `-0.002111`, CI
  `[-0.021579, 0.018152]`
- all sparse teacher KL: mean `0.002110`, CI `[-0.004788, 0.008445]`

Free-ID is statistically lower on target NLL overall and on positive-teacher
states, but not clearly better on sparse KL or no-positive states.

## Injector Scale Sweep

Evaluation-only delta multipliers:

- scale `0.0`: target NLL `0.532409`, no-positive degradation `0.0`,
  teacher-best LOO `0.0`
- scale `0.05`: target NLL `0.515801`, no-positive degradation `0.000374`,
  teacher-best LOO `0.001429`
- scale `0.1`: target NLL `0.480903`, no-positive degradation `0.000268`,
  teacher-best LOO `0.000439`
- scale `0.25`: target NLL `0.306223`, no-positive degradation `0.001138`,
  teacher-best LOO `0.003694`
- scale `0.5`: target NLL `0.226846`, no-positive degradation `0.043378`,
  teacher-best LOO `0.003646`
- scale `0.75`: target NLL `0.203627`, no-positive degradation `0.028398`,
  teacher-best LOO `0.003288`
- scale `1.0`: target NLL `0.196607`, no-positive degradation `0.028565`,
  teacher-best LOO `0.002334`

Scale `0.25` is diagnostically interesting because it fixes no-positive
degradation and slightly increases teacher-best LOO, but it sacrifices much of
the positive-state target-NLL gain.

## Aggregate Read

On a 16-positive-state teacher-forced subset, the current normalized read had
lower NLL than fixed-denominator, unnormalized matched-scale,
top-absolute-contribution-only, and raw-teacher-best-only diagnostics for all
three seeds. This does not support replacing the read rule before repairing
selector alignment.

## Decision

- Branch: `selector_teacher_alignment_issue`
- Recommendation: repair selector-teacher alignment before another
  program-channel run.
- Stage C2 allowed: false

## Artifacts

- Summary:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c1/stage_c1_5b_diagnostics_20260807_001/summary.json`
- Report:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c1/stage_c1_5b_diagnostics_20260807_001/report.md`
- Corrected LOO rows:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c1/stage_c1_5b_diagnostics_20260807_001/corrected_leave_one_out_rows.jsonl`
- Contribution rows:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c1/stage_c1_5b_diagnostics_20260807_001/contribution_rows.jsonl`
- All-memory subset rows:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c1/stage_c1_5b_diagnostics_20260807_001/compiled_all_memory_loo_subset.jsonl`
- Scale sweep:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c1/stage_c1_5b_diagnostics_20260807_001/injector_scale_sweep.json`

## INFERENCES

- The corrected LOO result retracts the exact-zero claim but not the Stage-C1
  failure: the memory-specific effect is small and poorly aligned with raw
  teacher utility.
- The dominant next bottleneck is selector-teacher alignment, not simply the
  normalized read or injector scale.
