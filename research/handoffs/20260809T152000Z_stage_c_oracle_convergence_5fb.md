# Handoff: Milestone 5F-B / EXP-016B

## Status

- Milestone: completed.
- Branch: `workflow/research-loop`.
- Source commit used by Lambda:
  `02f13ec2bba7600441b565cd97884fc23f9fdbc9`.
- Result record commit: the commit containing this handoff; report its exact
  SHA from Git after committing.
- Scientific branch:
  `input_embedding_channel_capacity_passed_after_convergence`.
- Pair-z, decoder, compiler, selector, Stage C2, AppWorld generation, and
  end-to-end RCMF work remain unstarted.

## Objective

Resume the exact Stage-5F-A ratio-1.0 direct DeltaE state from u64, retain the
same 192 pairs, Adam state, objective, K=4, `last_user_k`, and learning rate,
and continue until the first formal plateau at or after u128 or u256.

## Resume Verification

- Source checkpoint:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/oracle_convergence_5fa_20260808_001/confirmation/ratio_1.0/checkpoints/direct_sequence_utility_plus_sparse_kl_ratio1.0_u064.pt`.
- Source checkpoint SHA256:
  `26993056d9ac06d6fb43316fdd8ce4cc2557497d994a62500dbc6d16193ea840`.
- Source DeltaE SHA256:
  `897db72059a5cb5e8a38beb28b618bc3a7906ce6b973e8d601bd685ce8150424`.
- Pair-manifest SHA256:
  `b4868b7b384c099ed929dc1c8cb4d9db608843bdeab70717aaccfb57848f7c4d`.
- Exact ordered pair count: 192.
- Source updates per pair: min/max/mean `64/64/64.0`.
- Adam states: 192; learning rate `0.05`.
- u64 metric reproduction maximum absolute difference: `0.0`.

## Implementation Note

The immutable Stage-5F-A checkpoint has no embedded DeltaE/config/model/cache
hashes. EXP-016B does not rewrite it. It validates an external immutable
sidecar against the independently audited file and normalized DeltaE hashes.

The first launch at `b0037568a3decb8661c58630f73ad14c1fd539c6`
aborted before u65 because the new and old hash functions framed identical
tensor bytes differently. Commit `02f13ec2bba7600441b565cd97884fc23f9fdbc9`
made the runtime function exactly match the source-audit algorithm and added a
regression test. No training state was changed by the aborted launch.

## Convergence

| Updates | Spearman | Sign | Sequence Huber | Target-delta corr | Sparse KL |
|---:|---:|---:|---:|---:|---:|
| 64 | 0.976238 | 1.000000 | 0.054151 | 0.820359 | 0.166327 |
| 80 | 0.957625 | 0.992806 | 0.065346 | 0.849818 | 0.141278 |
| 96 | 0.982333 | 1.000000 | 0.034719 | 0.879554 | 0.115651 |
| 112 | 0.984810 | 0.992806 | 0.029525 | 0.892731 | 0.096622 |
| 128 | 0.979465 | 0.992806 | 0.034512 | 0.875870 | 0.096836 |

u112 is the numerically best sequence-utility point. At u128, sequence Huber
worsened `16.8911%` and Spearman changed `-0.005346` versus u112. The supplied
plateau rule tests whether improvement is `<1%`, so negative improvement also
passes. Because u128 is the first eligible checkpoint, the formal run stopped
there. Record this nonmonotonic-rule caveat in any analysis or paper account.

## Final Gate

- Spearman / Pearson: `0.979465 / 0.982839`.
- Sign agreement: `0.992806`.
- Sequence MAE / MSE / Huber:
  `0.048244 / 0.019760 / 0.034512`.
- Positive / negative mean student utility:
  `+0.667495 / -0.753849`.
- Neutral mean absolute student utility: `0.000135`.
- Ratio mean / max: `0.975180 / 1.0000001`.
- Huber reduction versus zero: `93.3019%`.
- All eight utility-capacity checks passed.

## Controls And Uncertainty

- Zero Huber / Spearman / sign: `0.515256 / 0.151501 / 0.575540`.
- Matched-random: `0.515793 / 0.062822 / 0.546763`.
- Stage-5E two-update: `0.473661 / 0.641904 / 0.776978`.
- Stage-5F-A u64: `0.054151 / 0.976238 / 1.000000`.
- Final-minus-zero Huber CI:
  `[-0.546267, -0.414451]`.
- Final-minus-random Huber CI:
  `[-0.551062, -0.414347]`.
- Final-minus-u64 Huber CI:
  `[-0.046206, +0.005487]`; numerical improvement is not significant under
  this paired bootstrap.

## Artifacts

- Root:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/oracle_convergence_5fb_20260809_001`.
- Final checkpoint:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/oracle_convergence_5fb_20260809_001/checkpoints/direct_sequence_utility_plus_sparse_kl_ratio1.0_u128.pt`.
- Checkpoint SHA256:
  `a31d53f426aeea9f01ea9def68c66004f49143d68e37f215efe0e9d2564e27f4`.
- DeltaE SHA256:
  `bf501c69bc7f800ed7f03debf65bf9a84f8e043c7acd76426797d2486d31b2b8`.
- Summary: `<root>/summary.json`.
- Generated report: `<root>/report.md`.
- Resume validation: `<root>/resume_validation.json`.
- Source integrity: `<root>/source_integrity_manifest.json`.
- Post-run validation: `<root>/postrun_validation.json`.
- Log:
  `/lambda/nfs/rcmf-persist/runs/logs/exp016b_direct_oracle_20260809_001.log`.

## Validation And Runtime

- Local Stage-C tests: `36 passed`.
- Lambda Stage-C tests: `36 passed`.
- Independent artifact validation: `0` errors.
- Runtime: `23,495.135 s = 6.5264 H100 hours`.
- Active tmux/process: none.
- GPU: `0 MiB / 0%`.
- Lambda safe to terminate: yes.

## Next Reviewed Milestone

Test a properly optimized 128D pair-latent/shared-injector decoder, now that
the direct K=4 input-embedding channel has passed its sequence-utility capacity
gate. Preserve the fixed direct-oracle pair set and use enough updates per pair
to establish convergence. Do not redesign the injection site based on the
superseded Stage-5E result.
