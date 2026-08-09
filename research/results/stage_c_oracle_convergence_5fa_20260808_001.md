# Milestone 5F-A / EXP-016A Convergence-Corrected Direct-Delta Oracle

Date: 2026-08-09

Source commit: `451b7a763dd3ca0a08ff7cf430d2d2e5b16396c8`

Final record commit: pending at report creation time.

Artifact root:
`/lambda/nfs/rcmf-persist/project/runs/stage_c/oracle_convergence_5fa_20260808_001`

Run log:
`/lambda/nfs/rcmf-persist/runs/logs/stage_c_oracle_convergence_5fa_20260808_001.log`

## Scope

VERIFIED:

- Qwen3-8B remained frozen and only teacher-forced target scoring was used.
- The injection site remained the input embedding at `last_user_k`, with
  `K=4`. No new injection site was implemented.
- No memory compiler, signed selector, selector score/gate, empirical `mu_i`,
  or full-bank aggregation was trained or used.
- No Stage C2, end-to-end RCMF training, AppWorld generation, or AppWorld
  evaluation was started.
- Student prompts contained the unchanged full-demo baseline prompt and target
  IDs only. They contained no raw memory text and accessed no selector payload.

## Inputs And Validation

- Reused pair-response cache:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/pair_grounding_5d_20260807_001/pair_response_cache`.
- Cache validation passed for all `1,728 / 1,728` pairs with zero errors.
- The target-token teacher identity passed for all `1,728` pairs:
  `mean_t(log P_teacher(y_t) - log P_baseline(y_t)) == L0 - Lj_text`.
  Maximum absolute error was `1.001358e-06`.
- Zero DeltaE reproduced bare Qwen with maximum absolute student utility
  `1.192093e-07` on both the 64-pair pilot and 192-pair confirmation sets.
- The pilot used `64` deterministic pairs, balanced as
  positive/neutral/negative/random `16/16/16/16`, covering all `36` effective
  memories.
- Confirmation used the original `192` Stage-5E validation pairs, balanced as
  `48/48/48/48`, also covering all `36` effective memories.

## Update Accounting Repair

VERIFIED:

- Every pair-specific DeltaE is now an independent parameter. An unselected
  row receives neither a gradient nor an Adam optimizer update.
- Every checkpoint stores the per-pair counter map, optimizer state, current
  update round, fixed pair IDs, objective, and ratio budget.
- All completed checkpoints had exact equal accounting:
  `minimum_updates_per_pair == maximum_updates_per_pair ==
  mean_updates_per_pair == checkpoint update`.
- Confirmation runs each performed exactly `192 * 64 = 12,288` pair updates.
- Unit tests cover one versus N updates, unselected-row immobility, atomic
  resume with counters and optimizer state, and fixed-subset convergence
  metrics.

The original Stage-5E artifact is preserved. It is now interpreted as
`underoptimized_two_update_result`: each pair had two gradient-bearing visits.
The previous direct-channel capacity failure is superseded, while Stage 5E's
objective-mismatch evidence remains valid.

## Objective Definitions

The primary sequence utility was computed differentiably as:

```text
u_student = L0 - L_student
u_teacher = L0 - Lj_text = text_utility
L_sequence_utility = Huber(u_student - u_teacher)
```

The deterministic 64-pair pilot compared:

- `target_delta_huber`;
- `sequence_utility_huber`;
- `sequence_utility_plus_sparse_kl`, with sparse-KL weight `0.05`.

The old union-top64 sparse-delta Huber was not used as a primary candidate.

## 64-Pair Convergence Pilot

Each table entry is `Spearman / sequence-utility Huber`.

| Objective | u2 | u8 | u16 | u32 | u64 | u128 |
|---|---:|---:|---:|---:|---:|---:|
| target delta Huber | 0.767308 / 0.367571 | 0.881914 / 0.264563 | 0.888553 / 0.208966 | 0.898306 / 0.210469 | 0.967628 / 0.145611 | 0.933974 / 0.141987 |
| sequence utility Huber | 0.911905 / 0.323010 | 0.938965 / 0.201953 | 0.953342 / 0.172362 | 0.967262 / 0.130500 | 0.968452 / 0.095580 | 0.968269 / 0.083021 |
| sequence utility + sparse KL | 0.919551 / 0.323848 | 0.963874 / 0.195489 | 0.953938 / 0.178499 | 0.955357 / 0.147090 | 0.953526 / 0.141181 | not run after u64 plateau |

Additional u48/u80/u96/u112 checkpoints are retained in the Lambda artifact
because a 16-update window is required to assess the plateau rule.

Final pilot details:

| Objective | Updates/pair | Plateau | Sign agreement | Target-delta corr | Sparse KL |
|---|---:|---:|---:|---:|---:|
| target delta Huber | 128 | no | 0.978723 | 0.864091 | 0.152697 |
| sequence utility Huber | 128 | no | 1.000000 | 0.789841 | 0.194408 |
| sequence utility + sparse KL | 64 | yes | 0.978723 | 0.780094 | 0.173441 |

The predetermined lexicographic selection rule prioritized: utility gate,
documented plateau, Spearman, sign agreement, Huber reduction versus zero, and
lower Huber. It selected `sequence_utility_plus_sparse_kl` at 64 updates per
pair. The 192-pair confirmation was not used to change that choice.

## 192-Pair Confirmation Curves

### Ratio Budget 0.5

| Updates/pair | Spearman | Sign agreement | Sequence Huber | Target-delta corr | Sparse KL |
|---:|---:|---:|---:|---:|---:|
| 2 | 0.916569 | 0.956835 | 0.324698 | 0.672248 | 0.246854 |
| 8 | 0.955352 | 1.000000 | 0.207377 | 0.742018 | 0.222725 |
| 16 | 0.949609 | 0.992806 | 0.149481 | 0.751542 | 0.212348 |
| 32 | 0.939689 | 0.985612 | 0.130746 | 0.760207 | 0.198931 |
| 48 | 0.955987 | 0.992806 | 0.111087 | 0.797679 | 0.194798 |
| 64 | 0.955679 | 0.992806 | 0.103908 | 0.818925 | 0.188730 |

The final mean/max perturbation ratios were `0.495321 / 0.500000`. The Huber
loss improved another `6.4629%` from u48 to u64, so the documented plateau
condition was false.

### Ratio Budget 1.0

| Updates/pair | Spearman | Sign agreement | Sequence Huber | Target-delta corr | Sparse KL |
|---:|---:|---:|---:|---:|---:|
| 2 | 0.926561 | 0.978417 | 0.310481 | 0.694442 | 0.234544 |
| 8 | 0.889545 | 0.949640 | 0.210577 | 0.650374 | 0.259428 |
| 16 | 0.931205 | 0.985612 | 0.151871 | 0.759246 | 0.229986 |
| 32 | 0.963554 | 0.992806 | 0.102469 | 0.789927 | 0.212376 |
| 48 | 0.973641 | 0.992806 | 0.069959 | 0.831309 | 0.176823 |
| 64 | 0.976238 | 1.000000 | 0.054151 | 0.820359 | 0.166327 |

The final mean/max perturbation ratios were `0.973289 / 1.000000`. The Huber
loss improved another `22.5965%` from u48 to u64 and Spearman improved
`0.002597`; because both plateau conditions must hold, this run was clearly
not converged.

## Final Controls And Category Results

| Model | Spearman | Sign agreement | Sequence Huber | Target NLL | Target-delta corr | Sparse KL |
|---|---:|---:|---:|---:|---:|---:|
| zero DeltaE | 0.151501 | 0.575540 | 0.515256 | 0.777454 | 0.033224 | 0.282498 |
| matched random, ratio 0.5 | 0.178708 | 0.611511 | 0.514729 | 0.775436 | -0.017929 | 0.279346 |
| trained ratio 0.5 | 0.955679 | 0.992806 | 0.103908 | 0.790840 | 0.818925 | 0.188730 |
| matched random, ratio 1.0 | -0.001699 | 0.532374 | 0.516598 | 0.773647 | -0.052106 | 0.280230 |
| trained ratio 1.0 | 0.976238 | 1.000000 | 0.054151 | 0.790698 | 0.820359 | 0.166327 |

Ratio-1.0 category results over utility-defined categories:

- Positive: `77` pairs, mean `u_text=0.717161`, mean
  `u_student=0.598713`, positive sign rate `1.0`.
- Neutral: `53` pairs, mean `u_student=-0.000014`, mean absolute
  `u_student=0.000131`.
- Negative: `62` pairs, mean `u_text=-0.814601`, mean
  `u_student=-0.784564`, negative sign rate `1.0`.

Target NLL is reported but is not the capacity gate: the balanced diagnostic
contains deliberately harmful raw-memory teacher pairs, so reproducing their
negative utility can raise aggregate NLL. Utility reconstruction, sign, and
control-relative error are the primary oracle criteria.

## Capacity Gate And Decision

For ratio 1.0, seven of eight primary checks passed:

- Spearman `0.976238 >= 0.80`;
- sign agreement `1.0 >= 0.85`;
- sequence-Huber reduction versus zero `89.4905% >= 50%`;
- positive-pair mean utility was positive;
- negative-pair mean utility was negative;
- neutral mean absolute utility was `0.000131 <= 0.05`;
- perturbation ratio stayed within the ratio-1.0 numerical tolerance;
- documented plateau: failed because loss was still improving materially.

Reached branch: `oracle_not_converged_extend_updates`.

VERIFIED conclusion:

- The previous `direct_delta_fails` capacity interpretation is superseded.
- Stage 5E's objective-mismatch finding remains valid.
- The current immediate bottleneck is inadequate convergence, not a disproven
  K=4 input-embedding location.
- The optional 32-pair pair-z sanity check was correctly not triggered because
  the direct gate requires a documented plateau.
- No later-layer site redesign, Stage C2, or compiler training should start
  from this result.

Recommended next milestone:

- Run EXP-016B as a resumable convergence extension of the ratio-1.0,
  sequence-utility-plus-sparse-KL direct oracle.
- Continue from u64 to at least u128, with deterministic checkpoints every 16
  updates and a train-only, predetermined learning-rate stabilization rule if
  needed.
- Stop again if the curve is still improving. Only after a plateau passes the
  direct gate should a separately approved, properly optimized 128D
  decoder/injector capacity milestone begin.

## Runtime And Artifacts

- Total formal wall time: `83,929.064 s = 23.3136 H100 hours`.
- Pilot runtimes: target delta `4.1693 h`, sequence utility `4.1579 h`,
  sequence utility plus sparse KL `2.0937 h`.
- Confirmation runtimes: ratio 0.5 `6.4129 h`, ratio 1.0 `6.3995 h`.
- Summary:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/oracle_convergence_5fa_20260808_001/summary.json`.
- Generated report:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/oracle_convergence_5fa_20260808_001/report.md`.
- Selected pilot checkpoint:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/oracle_convergence_5fa_20260808_001/pilot/sequence_utility_plus_sparse_kl/checkpoints/direct_sequence_utility_plus_sparse_kl_ratio0.5_u064.pt`.
- Ratio-0.5 confirmation checkpoint:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/oracle_convergence_5fa_20260808_001/confirmation/ratio_0.5/checkpoints/direct_sequence_utility_plus_sparse_kl_ratio0.5_u064.pt`.
- Ratio-1.0 confirmation checkpoint:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/oracle_convergence_5fa_20260808_001/confirmation/ratio_1.0/checkpoints/direct_sequence_utility_plus_sparse_kl_ratio1.0_u064.pt`.

Post-run status: `EXIT_STATUS=0`, no tmux server, no active Stage-5F-A
process, GPU `0 MiB / 0%`. Safe to terminate: yes.
