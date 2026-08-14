# Handoff: EXP-019 Interaction Representation Repair

## Status

- Completed under run UUID
  `state_transition_representation_6c_20260814_001`.
- Branch: `research/v4-decision-transition-memory`.
- Final source/audit commit before records:
  `5ca600bf76fcdb9db5b0278c60a31dc35b6a7128`.
- Last experiment-runner fix commit:
  `cbb75d474e01ee19e35a35d76814b8c63f1efdc7`.
- Result-record commit: the commit containing this handoff; report its exact
  SHA after committing.
- Decision branch: `query_task_coverage_insufficient`.
- Representation gate repaired: no.
- Behavioral `p(s,m_transition)` remains blocked.
- V4 remains a candidate; no V4 tag was created.

## What Ran

- Reproduced all EXP-018 cheap-gate metrics and retained exact A/B/C/D cells.
- Measured cell-A utility state/transition main effects and interaction
  residuals.
- Added within-state Spearman, NDCG@1/4/8, best recall, positive utility mass,
  pairwise accuracy, residual correlation, and grouped bootstrap intervals.
- Trained decomposed residual/listwise models on the original 4096D vectors.
- Built two-layer-choice, ten-view frozen-Qwen caches for states and
  transitions with decoded span validation.
- Trained multiview signed bilinear, low-rank tensor, pair-MLP upper-bound, and
  deterministic structured-feature models.
- Cached and evaluated all 4,579 prompt-only frozen-Qwen cross-encoder pairs.
- Ran deterministic 4/8/12-query-task, five-fold cell-A learning curves for
  seven model families.

## What Did Not Run

- No behavioral state-transition program.
- No additive-token injector.
- No selector or production transition field.
- No Qwen behavioral backpropagation or parameter update.
- No Stage C2, end-to-end RCMF, AppWorld generation/evaluation, prompt-demo
  change, or V4 tag.
- No 64-query or 96-query teacher expansion.

These omissions are required by the failed representation gate and final
decision branch, not unfinished execution.

## Exact Inputs And Cells

| Cell | Pairs | Utility mean/std | Positive / neutral / negative | Majority sign |
|---|---:|---:|---:|---:|
| A train/train | 2,667 | .067686/.331588 | 1,343/370/954 | .584676 |
| B held-out/train | 904 | .024758/.096050 | 403/349/152 | .726126 |
| C train/held-out | 752 | .084375/.324114 | 408/119/225 | .644550 |
| D held-out/held-out | 256 | .030112/.074908 | 117/103/36 | .764706 |

All 4,579 scoreable rows are accounted for. The 61 over-context rows remain
masked and untruncated. D's sign baseline is exactly `117/(117+36)`.

## Main-Effect Decomposition

- `mu = 0.0676861715`.
- Total utility variance: `0.1099505467`.
- State component variance / R2: `0.0454531963 / 0.412241`.
- Transition component variance / R2: `0.0040498008 / 0.035677`.
- Additive-main R2: `0.449074`.
- Interaction-residual variance: `0.0605746571`.
- Utility/residual effective rank: `14.4530 / 16.0971`.

## Gate Results

Double-held-out D values are NDCG@4 / per-state Spearman / residual Spearman:

| Model | Correct | State shuffle | Transition shuffle | Positive tasks | Gate |
|---|---:|---:|---:|---:|---|
| Original-vector signed residual | .484875/.050357/.129785 | .472130/.066761/-.060378 | .336205/-.080187/.060836 | 1/4 | fail |
| Multiview signed bilinear | .374059/-.040277/.082560 | .403282 | .341603 | 0/4 | fail |
| Multiview low-rank tensor | .554092/.121518/.188452 | .533599 | .420201 | 1/4 | fail |
| Multiview pair MLP | .392459/.015900/.333560 | .508507 | .415213 | 0/4 | fail |
| Structured features | .395926/-.009943/.227923 | .502783 | .290140 | 1/4 | fail |
| Prompt-only cross-encoder | .379564/-.054573/-.035283 | .350995 | .495154 | 1/4 | fail |

The transition-only baseline D NDCG@4 is `0.566808`. No model beats it by the
required 0.05. Bootstrap intervals for primary shuffle contrasts include zero.

## Multiview Cache

- State shape: `[32,10,4096]` for final and final-four-mean layers.
- Transition shape: `[148,10,4096]` for both layer choices.
- Span count: 900; exact decoded: 461; token-boundary-expanded but
  source-aligned: 439; invalid: 0.
- State tokens min/mean/max: `7,889 / 11,725.06 / 22,433`.
- Transition tokens min/mean/max: `7,413 / 11,470.13 / 35,608`.
- No truncation, target action, future observation, or cross-query solution
  entered a query representation.

The final-layer centered effective-rank ranges are `9.980-26.714` for state
views and `24.129-92.432` for transition views. Full geometry and singular
spectra are in `parts_c_d_summary.json`.

## Cross-Encoder

- Cache version: `prompt_transition_cross_encoder_cache_6c_v1`.
- Pair count: 4,579; over-context/truncated: 0/0.
- Prompt token min/mean/max: `15,318 / 22,923.28 / 40,829`.
- Aggregate shape: `[4579,12288]`.
- Aggregate SHA256:
  `d40f4e2dfc516a02bb4066f195a27e6e3612a344a6761858787aefe86bc1c763`.
- A/C NDCG@4: `0.820228 / 0.661463`.
- B/D NDCG@4: `0.309609 / 0.379564`.
- D transition shuffle NDCG@4: `0.495154`.

The cross-encoder is a nondeployable O(number-of-memories) information upper
bound. It failed D and cannot unlock behavioral training.

## Learning Curves And Next Question

Prompt cross-encoder NDCG@4 at 4/8/12 tasks was
`0.469506/0.426066/0.431144`; residual Spearman was
`-0.068862/-0.151063/0.003225`. The final interval was slightly rising and
fold results remained unstable. This triggers `query_task_coverage_insufficient`.

Projected, not launched:

- 64 queries: 9,280 legal, 9,158 scoreable, 122 over-context, 4.5510 H100h.
- 96 queries: 13,920 legal, 13,737 scoreable, 183 over-context, 6.8265 H100h.

The next reviewed milestone should preregister an expanded query-task panel,
generate its full teacher cache without truncation, rerun the cross-encoder,
and only then test whether a field-compatible model retains its gain. It must
stop for review before behavioral training.

## Recovery Provenance

The append-only ledger has nine start/end pairs:

- failed: attempts 001, 003, 004, 006, 008;
- completed: attempts 002, 005, 007, 009;
- scientific parameter changed: false for every attempt.

Attempt 001 repaired serialization-tolerance handling. Attempts 003/004
repaired tokenizer-boundary validation. Attempt 006 completed all 4,579
atomic cross-encoder rows before a post-cache path failure; attempt 007 reused
all rows. Attempt 008 failed before training due an aggregate path; attempt
009 reused the validated Part-E artifact. Regression tests cover every repair.

The laptop/network disconnect did not create a duplicate run. Active attempt
wall time was `10,505.717 s` (`2.9183 h`). Artifact size is
`23,462,274,209 bytes` (`21.851 GiB`).

## Validation And Artifacts

- Root:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/interaction_representation_6c_20260814_001`.
- Parts A/B: `<root>/parts_a_b_summary.json` and `parts_a_b_report.md`.
- Parts C/D: `<root>/parts_c_d_summary.json` and `parts_c_d_report.md`.
- Part E: `<root>/part_e_summary.json`, `part_e_report.md`, and
  `<root>/part_e/cross_encoder_cache/`.
- Part F: `<root>/part_f_summary.json`, `part_f_report.md`, and
  `<root>/part_f/learning_curve_manifest.json`.
- Ledger/heartbeat: `<root>/attempts.jsonl`, `<root>/heartbeat.json`.
- Independent validation: `<root>/postrun_validation.json` and
  `<root>/postrun_validation.md`.

Independent validation passed with zero errors: 11 immutable hashes, all
multiview and cross-encoder rows/hashes, 105 learning-curve rows, exact cells,
and all nine attempts. Local tests passed `204 passed, 1 skipped`; strengthened
Lambda validator tests passed `6 passed`.

At final audit there was no tmux or experiment process. GPU allocation and
utilization were `0 MiB / 0%`. Lambda is safe to terminate after final Git
sync.
