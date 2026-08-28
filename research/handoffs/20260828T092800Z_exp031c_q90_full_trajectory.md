# EXP-031C Structured Handoff

## Identity

- Active branch: `research/v5-rcmf-q90-full-trajectory`
- Starting HEAD: `7207c8adf7df351d3365e956aa8f0f12cf423879`
- Final Git-safe record commit: `f3149a3ba245f537cd9746a4b4bd5a07c709d9de`
- Archive branch: `archive/exp031b-benefit-preserving-calibration-7207c8a`
- Annotated tag: `exp031b-benefit-preserving-calibration-verified-7207c8a`
- Run UUID: `rcmf_q90_full_trajectory_9c_20260828_001`
- Seed: `25101`
- Q90 tau: `4.606291029188367`
- Calibration SHA256: `f1d0b1b8553f008423d4c00a4637e0f9d1c01444820f6652ac519a39710b7a8c`

## Immutable Inputs

- EXP-031A source: `57d2a3479ff292dd8f89bdd0ea9f9417abc42a48`
- Checkpoint SHA256: `d11e9d8ea28348148dd8919144c64ea69dbf187864a0e42fbdcc69f32241a5f1`
- 401 correct field SHA256: `63929027dfddea722419024949e492d9477a5fd61a45fe4dbf40a07a3936fa79`
- 401 shuffle field SHA256: `a50f0f2c415433ebdb9b9a22624ea79f196c74181b6050c96b12235918402331`
- 499 deployment field SHA256: `5fe48fc206c592fdbe899a2b4923b4eccc950210fd4a843de681c77c573e0b5e`
- First37 manifest SHA256: `bea99f792ccf19a25c805642561eb987bd35de164ac9a51f3d486fda9a9928b9`

No parameter was trained or recalibrated. No retrieval, top-k, selected-memory runtime access, raw-memory prompt, gate, threshold, per-memory runtime score, or task-specific rule was used.

## Tests and Equivalence

- Local focused suite: `77 passed`.
- Lambda focused suite: `77 passed`.
- G100 exact slots/logits/tokens/response/action: passed.
- Zero/bare exact tokens/response/action: passed.
- Q90 exact formula and frozen identity: passed.
- Complete fresh-world deterministic repeats: passed on two representative tasks, twice each.
- Qwen and reader parameters: unchanged and frozen.
- Final redaction scan: 160 text files, zero raw JWT matches, zero registered sensitive-observation leaks.
- Final detailed audit: 114 JSONL files and 2,689 step rows.

## Heldout Complete-Trajectory Result

Task IDs:

`76f2c72_1`, `76f2c72_2`, `7d7fbf6_1`, `b7a9ee9_2`, `c901732_1`, `c901732_3`, `e7a10f8_1`, `e7a10f8_3`.

- H0 bare: `3/8`
- H1 original correct: `5/8`
- H2 original shuffle: `3/8`
- H3 Q90 correct: `6/8`
- H4 Q90 shuffle: `4/8`
- H3-H0: `+3`
- H3-H1: `+1`
- H3-H4: `+2`
- Decision: `PROCEED`

H3 successes:

`76f2c72_1`, `7d7fbf6_1`, `b7a9ee9_2`, `c901732_1`, `c901732_3`, `e7a10f8_1`.

## First37 Result

- D0 bare: `8/37`
- D1 original correct: `8/37`
- D2 original shuffle: `5/37`
- Q1 Q90 correct: `5/37`
- Q2 Q90 shuffle: `3/37`
- Q1-D0: `-3`
- Q1-D1: `-3`
- Q1-Q2: `+2`

Q1 successes:

`325d6ec_2`, `325d6ec_3`, `634f342_2`, `8749218_1`, `8749218_2`.

Q2 successes:

`29a7b7e_3`, `325d6ec_2`, `325d6ec_3`.

Benefit preservation:

- retained original gains: `325d6ec_2`, `325d6ec_3`, `634f342_2` (`3/6`);
- lost original gains: `0d01c76_3`, `634f342_1`, `634f342_3` (`3/6`);
- gain families retained: Spotify and one exact-set task;
- cross-app import family retained: no;
- retained successes preserved: only `8749218_2` (`1/2`);
- recovered loss: `8749218_1`;
- equivalent new gains: none.

Mechanical label: `LIVE_MEMORY_SPECIFIC_SIGNAL`.

Scientific branch: `STOP_ROUTE` due `two_or_more_original_gains_lost` and `original_retained_success_lost`.

## Scientific Interpretation

VERIFIED:

- Q90 correct beats its matched shuffled control by two tasks.
- Q90 correct is three tasks below both bare and original correct.
- Q90 loses three of six original gains, the cross-app family, and one retained success.
- No infrastructure error, evaluator mismatch, prompt change, or complexity violation occurred.

INFERENCE:

- Q90 changes live trajectories in a memory-specific way but does not preserve useful task-level behavior reliably.
- Cheap proxy and small heldout trajectory gates remain insufficient predictors of first37 benefit preservation.

UNVERIFIED:

- No statistical generalization claim is available from one seed and an exposed official test-normal pool.

## Runtime and Attempts

- Attempts: `19` unique / `38` ledger rows.
- Failed attempts: `3`; all failed before accepted scientific outcomes.
- Open attempts: `0`.
- Formal trajectory H100-active time: `4.2070 h`.
- Total H100-active estimate including equivalence/determinism: `4.2905 h`.
- Ledger wall span: `6.0974 h`.
- Q1 wall: `5409.950 s`; Q2 wall: `6472.243 s`.
- Final raw artifact bytes: `1,435,395,137`.

Notable deviations:

- three append-only schema-compatibility failures were preserved;
- equivalence attempt 002 had a post-ledger stdout Tensor-serialization failure, while attempt 003 is the accepted clean run;
- the Windows `apply_patch` helper was unavailable, so guarded exact UTF-8 replacements were used and tested;
- passive watchdog SSH connections reset, but tmux jobs and atomic outputs remained healthy and were never restarted.

## Artifacts

Git-safe report:

`research/results/EXP_031C_RCMF_Q90_FULL_TRAJECTORY.md`

Machine results:

`research/results/exp031c_rcmf_q90_full_trajectory/`

Detailed audit:

`research/audits/rcmf_q90_full_trajectory_9c_20260828_001/`

Raw Lambda root:

`/lambda/nfs/rcmf-persist/project/runs/stage_c/rcmf_q90_full_trajectory_9c_20260828_001`

Lambda-only query/slot tensor bundle:

`/lambda/nfs/rcmf-persist/project/runs/stage_c/rcmf_q90_full_trajectory_9c_20260828_001/git_safe_exports/final/audit/field_tensors/query_and_slots.pt`

SHA256: `dbac5bf33dcac711d115ca19abcf28719f5f38c92667d0b0668a2e8be96194de`.

## Next Action

Freeze EXP-031A/B/C and stop calibration search. Use the committed reports and audits to write claim-calibrated paper tables, limitations, and reproducibility material. Keep the positive memory-specific effect separate from the failed absolute and benefit-preservation results. Any new scientific run requires a new reviewed contract.

Do not create or move a V5 release tag. Do not start retraining, retrieval, gating, new calibration, portability, or broader evaluation from this handoff.