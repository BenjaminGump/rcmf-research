# EXP-031C: Q90 Full-Trajectory Validation and Conditional First37 Audit

## Status

- Run UUID: `rcmf_q90_full_trajectory_9c_20260828_001`
- Branch: `research/v5-rcmf-q90-full-trajectory`
- Starting commit: `7207c8adf7df351d3365e956aa8f0f12cf423879`
- Global seed: `25101`
- Scientific decision: `STOP_ROUTE`
- Mechanical label: `LIVE_MEMORY_SPECIFIC_SIGNAL`
- Training or parameter updates: none
- First37 status: exposed single-seed development data, not a statistical generalization result

## Frozen Candidate

Q90 was the only candidate:

```text
raw = B + einsum(q, A)
confidence = raw_rms / (raw_rms + tau_Q90)
slots = RMSNorm(raw) * confidence
```

The immutable values were:

- `tau_Q90 = 4.606291029188367`
- calibration semantic SHA256: `f1d0b1b8553f008423d4c00a4637e0f9d1c01444820f6652ac519a39710b7a8c`
- EXP-031A checkpoint SHA256: `d11e9d8ea28348148dd8919144c64ea69dbf187864a0e42fbdcc69f32241a5f1`
- 401-memory correct field SHA256: `63929027dfddea722419024949e492d9477a5fd61a45fe4dbf40a07a3936fa79`
- 401-memory shuffle field SHA256: `a50f0f2c415433ebdb9b9a22624ea79f196c74181b6050c96b12235918402331`
- 499-memory deployment field SHA256: `5fe48fc206c592fdbe899a2b4923b4eccc950210fd4a843de681c77c573e0b5e`
- first37 logical manifest SHA256: `bea99f792ccf19a25c805642561eb987bd35de164ac9a51f3d486fda9a9928b9`

No tau, scale, checkpoint, field, selector, prompt, evaluator, memory ledger, or generation setting was changed after outcomes were observed.

## Git Provenance

- Archive branch: `archive/exp031b-benefit-preserving-calibration-7207c8a`
- Annotated tag: `exp031b-benefit-preserving-calibration-verified-7207c8a`
- Tag object: `59d14b9bc98930ab5c3970a7329958820c3756fd`
- Contract commit: `a9a892da74e3948d8bfe063683f39e43e5ab0b56`
- Initial implementation commit: `509d3a6a0963dfeaaf8b1a5f1da26433063e7e61`
- Heldout execution source commit: `0644d0c94fd3f0624eac58fc5cd7a3299654e47d`
- Q1 execution source commit: `e2092480d8f674746372fcba7163025ca42ab94f`
- Q2 execution source commit: `07fd093a1dc8a69a5af3d1cbb966400634a7f2fc`
- Q2 result checkpoint: `7c2d1c2a1fce236b11bfec6b62470191f03d4184`

## Validation Gates

VERIFIED:

- G100 exact slot, logit, deterministic token, response, and action equivalence passed.
- Zero-field exact bare token, response, and action equivalence passed.
- Q90 formula and confidence identity passed.
- Qwen and reader parameters remained frozen.
- Complete fresh-world deterministic replay passed twice on each of two representative tasks.
- Fixed field and slot shapes passed.
- Runtime retrieval, memory scans, and per-memory scoring were absent.
- Correct and shuffled conditions applied the same Q90 formula to their own frozen raw fields.
- All task-condition rows began from fresh isolated AppWorld worlds.
- Final focused test suites passed locally and on Lambda: `77 passed` each.

The final Git-safe exporter independently verified 8 heldout tasks, 37 first37 tasks, 2,689 step rows, zero raw JWT matches, and zero registered sensitive-observation leaks.

## Heldout Full Trajectories

The immutable eight heldout train tasks were:

`76f2c72_1`, `76f2c72_2`, `7d7fbf6_1`, `b7a9ee9_2`, `c901732_1`, `c901732_3`, `e7a10f8_1`, `e7a10f8_3`.

| Condition | Success | Success IDs | Steps | Wall seconds |
|---|---:|---|---:|---:|
| H0 bare | 3/8 | `7d7fbf6_1`, `b7a9ee9_2`, `c901732_1` | 141 | 614.126 |
| H1 original correct G100 | 5/8 | `b7a9ee9_2`, `c901732_1`, `c901732_3`, `e7a10f8_1`, `e7a10f8_3` | 184 | 753.552 |
| H2 original shuffled G100 | 3/8 | `7d7fbf6_1`, `b7a9ee9_2`, `c901732_1` | 221 | 886.006 |
| H3 Q90 correct | 6/8 | `76f2c72_1`, `7d7fbf6_1`, `b7a9ee9_2`, `c901732_1`, `c901732_3`, `e7a10f8_1` | 122 | 539.609 |
| H4 Q90 shuffled | 4/8 | `7d7fbf6_1`, `b7a9ee9_2`, `c901732_1`, `e7a10f8_1` | 99 | 469.759 |

Paired differences:

- H3 - H0: `+3`
- H3 - H1: `+1`
- H3 - H4: `+2`
- H3 gains versus H1: `76f2c72_1`, `7d7fbf6_1`
- H3 loss versus H1: `e7a10f8_3`
- H3 gains versus H4: `76f2c72_1`, `c901732_3`
- H3 losses versus H4: none

H3 did not collapse to H0. Infrastructure and evaluator identities passed. The preregistered heldout decision was `PROCEED`; this eight-task result is descriptive and not statistically conclusive.

## First37 Full Trajectories

The conditional first37 manifest froze 37 exposed development tasks and 74 Q1/Q2 task-condition slots before generation.

| Condition | Success | Success IDs | Steps | Wall seconds | Exceptions |
|---|---:|---|---:|---:|---:|
| D0 immutable bare | 8/37 | `0d01c76_1`, `0d01c76_2`, `29a7b7e_3`, `325d6ec_1`, `8749218_1`, `8749218_2`, `8749218_3`, `d6ac34d_2` | immutable | immutable | validated |
| D1 immutable original correct | 8/37 | `0d01c76_3`, `325d6ec_2`, `325d6ec_3`, `634f342_1`, `634f342_2`, `634f342_3`, `8749218_2`, `8749218_3` | immutable | immutable | validated |
| D2 immutable original shuffle | 5/37 | `0d01c76_2`, `325d6ec_1`, `8749218_1`, `8749218_2`, `d6ac34d_1` | immutable | immutable | validated |
| Q1 Q90 correct | 5/37 | `325d6ec_2`, `325d6ec_3`, `634f342_2`, `8749218_1`, `8749218_2` | 928 | 5409.950 | 0 |
| Q2 Q90 shuffle | 3/37 | `29a7b7e_3`, `325d6ec_2`, `325d6ec_3` | 993 | 6472.243 | 0 |

Paired aggregate differences:

- Q1 - D0: `-3`
- Q1 - D1: `-3`
- Q1 - Q2: `+2`

Benefit preservation:

- Original EXP-031A gains retained: `325d6ec_2`, `325d6ec_3`, `634f342_2` (`3/6`).
- Original EXP-031A gains lost: `0d01c76_3`, `634f342_1`, `634f342_3` (`3/6`).
- Gain-family coverage:
  - cross-app import: none;
  - Spotify state machine: `325d6ec_2`, `325d6ec_3`;
  - exact-set migration: `634f342_2`.
- Original retained successes preserved: only `8749218_2`; `8749218_3` was lost.
- Original loss recovered: `8749218_1`.
- Equivalent new gains: none.

## Decision

The mechanical label is `LIVE_MEMORY_SPECIFIC_SIGNAL` because Q1 exceeds the matched Q2 shuffle by two tasks.

The scientific decision is `STOP_ROUTE` because:

- Q1 is `5/37`, below both bare and original correct at `8/37`;
- three original gains were lost, violating the at-least-5/6 preservation requirement;
- the cross-app import family disappeared;
- one of the two locked retained successes was lost;
- no equivalent new multi-family gain compensated for the losses.

VERIFIED: Q90 has a full-trajectory correct-versus-shuffle signal in this single-seed exposed development run.

VERIFIED: Q90 does not preserve or improve the complete first37 task-success profile and must not be reported as a successful benefit-preserving calibration.

INFERENCE: the confidence scaling changes field behavior in a memory-specific way, but its trajectory-level utility remains task- and family-sensitive.

UNVERIFIED: no statistical generalization claim is available because the official test-normal pool is exposed and only one seed was used.

## Runtime and Attempts

- Unique attempts: `19`
- Ledger rows: `38`
- Failed attempts: `3`
- Open attempts: `0`
- Formal trajectory H100-active time: `4.2070 h`
- Equivalence plus determinism H100 time: `0.0835 h`
- Total measured H100-active estimate: `4.2905 h`
- Ledger wall span: `6.0974 h`
- Raw Lambda artifact bytes at final export: `1,435,395,137`

Failed attempts were append-only and produced no accepted scientific outcome:

- heldout manifest attempt 001: calibration-lock schema key mismatch;
- equivalence attempt 001: immutable parent manifest used condition rows rather than a top-level task list;
- first37 preflight attempt 001: the same parent-row schema mismatch in reused-control validation.

Equivalence attempt 002 completed scientific checks and wrote an atomic report, but its process later failed while serializing a Tensor to stdout after the ledger had already closed successfully. The raw report was quarantined under the run artifact tree; attempt 003 is the clean accepted equivalence execution.

Both long Q1/Q2 watchdog SSH connections reset after about one hour. The tmux jobs, heartbeat, atomic outputs, and GPU work continued normally; neither batch was restarted or modified.

## Audit and Artifacts

Git-safe:

- `research/audits/rcmf_q90_full_trajectory_9c_20260828_001/index.json`
- `research/audits/rcmf_q90_full_trajectory_9c_20260828_001/heldout/`
- `research/audits/rcmf_q90_full_trajectory_9c_20260828_001/first37/`
- `research/audits/rcmf_q90_full_trajectory_9c_20260828_001/comparisons/`
- `research/results/exp031c_rcmf_q90_full_trajectory/summary.json`
- `research/results/exp031c_rcmf_q90_full_trajectory/heldout_per_task.jsonl`
- `research/results/exp031c_rcmf_q90_full_trajectory/first37_per_task.jsonl`
- `research/results/exp031c_rcmf_q90_full_trajectory/comparisons.jsonl`
- `research/results/exp031c_rcmf_q90_full_trajectory/attempts.jsonl`
- `research/results/exp031c_rcmf_q90_full_trajectory/complexity.json`

Lambda-only raw artifact root:

`/lambda/nfs/rcmf-persist/project/runs/stage_c/rcmf_q90_full_trajectory_9c_20260828_001`

The model-derived compact query/slot tensor bundle remains Lambda-only:

- path: `/lambda/nfs/rcmf-persist/project/runs/stage_c/rcmf_q90_full_trajectory_9c_20260828_001/git_safe_exports/final/audit/field_tensors/query_and_slots.pt`
- bytes: `33,382,807`
- SHA256: `dbac5bf33dcac711d115ca19abcf28719f5f38c92667d0b0668a2e8be96194de`

It was intentionally not committed. The Git-safe index records its exact path, size, and hash.

## Implementation Deviations

- The Windows `apply_patch` sandbox helper failed with `helper_unknown_error`. Narrow guarded UTF-8 replacements were used only after the helper failed; every edit was reviewed, whitespace-checked, and covered by tests.
- Parent artifact schemas were consumed through compatibility helpers without changing parent artifacts or scientific semantics.
- No historical artifact was rewritten. No V5 release tag was created or moved.

## Recommendation

Freeze Q90 and the complete EXP-031A/B/C result set. The submission should distinguish the verified constant-size memory-specific field effect from the failed task-level benefit-preservation claim. The next action should be manuscript tables, claim calibration, limitations, and reproducibility packaging from committed artifacts. Any new calibration, retraining, retrieval, gating, portability, or broader evaluation requires a separately reviewed contract.