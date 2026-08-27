# EXP-031B Stage 8A Cached Diagnostics

Status: **VERIFIED cached diagnostics complete; live benefit-preservation gate not yet run.**

## Identity

- Run UUID: `rcmf_benefit_preserving_calibration_9b_20260827_001`
- Global seed: `25101`
- Source checkpoint SHA256: `d11e9d8ea28348148dd8919144c64ea69dbf187864a0e42fbdcc69f32241a5f1`
- Deployment field SHA256: `5fe48fc206c592fdbe899a2b4923b4eccc950210fd4a843de681c77c573e0b5e`
- Calibration semantic SHA256: `f1d0b1b8553f008423d4c00a4637e0f9d1c01444820f6652ac519a39710b7a8c`
- Aggregate summary SHA256: `239238a8fc95d38ffa292a3ddc49a4f689082d90cd4fae9861d84efaa713d7cf`
- Git-safe row-ledger SHA256: `9f3a8989fac18ec41477c147132dc847602d91987ceacdf23bb0cfd06d2cdf90`
- Git-safe logical row SHA256: `a20e175a524a9e1dc4d830ecf362c350ebee2f7846c5f736ba53e16527727402`

## Accounting

- Candidate formulas: `22`
- Heldout train-validation states: `98`
- Exact critical first37 states: `14`
- Logical cached conditions: `2464/2464`
- Exceptions: `0`
- Duplicate keys: `0`
- Candidate outcomes used to define formulas: `false`
- Runtime retrieval/per-memory scoring/raw memory in student prompt: `false/false/false`
- Scientific diagnosis wall time: `5931.047868` seconds (`1.647513` H100 hours)

The first diagnosis attempt failed before writing any candidate row because a
Flash-only SDPA context rejected a padded two-row forward. The recovery restored
the parent EXP-031A math-SDPA behavior, passed a non-scientific padded-forward
smoke, and then completed all conditions. No scientific parameter changed.

## Calibration Lock

- Layer order: `[7, 14, 21, 28]`
- `C50`: `[0.0509615541, 0.0207946487, 0.0251163971, 0.0902228504]`
- `C75`: `[0.0695108548, 0.0268012378, 0.0335493609, 0.1356399208]`
- `C90`: `[0.1073048413, 0.0345023572, 0.0483336486, 0.1722555161]`
- `Q50/Q75/Q90` tau: `41.4566193 / 13.8188731 / 4.60629103`
- Raw-field RMS CV: `0.803213`
- Raw-field RMS p90/p10: `12.2912`
- Route-D spread decision: `PROCEED`

## Cached Result

The heldout NLL margin is `shuffle NLL - candidate NLL`; positive values favor
the candidate over the frozen key/payload shuffle.

| Candidate | Heldout NLL | Shuffle margin | Critical NLL | Critical KL | Max ratio | Cap fraction |
|---|---:|---:|---:|---:|---:|---:|
| R0-original / G100 | 0.099314 | -0.002160 | 0.291645 | 0.000000 | 0.300666 | 0.000000 |
| G075 | 0.099298 | -0.002144 | 0.317008 | 0.001782 | 0.227724 | 0.000000 |
| G050 | 0.106223 | -0.009069 | 0.338425 | 0.004846 | 0.150734 | 0.000000 |
| G025 | 0.134021 | -0.036867 | 0.354282 | 0.010452 | 0.075292 | 0.000000 |
| L1 | 0.106480 | -0.009326 | 0.317564 | 0.001348 | 0.249064 | 0.000000 |
| L2 | 0.111675 | -0.014521 | 0.326512 | 0.001760 | 0.249064 | 0.000000 |
| L3 | 0.125410 | -0.028256 | 0.339226 | 0.003571 | 0.249064 | 0.000000 |
| L4 | 0.094965 | +0.002189 | 0.301805 | 0.002513 | 0.301877 | 0.000000 |
| LOO7 | 0.092063 | +0.005091 | 0.304373 | 0.004049 | 0.300690 | 0.000000 |
| LOO14 | 0.099557 | -0.002403 | 0.305625 | 0.002286 | 0.303201 | 0.000000 |
| LOO21 | 0.104772 | -0.007618 | 0.312405 | 0.001357 | 0.303599 | 0.000000 |
| LOO28 | 0.132349 | -0.035195 | 0.335123 | 0.004482 | 0.249064 | 0.000000 |
| C50 | 0.111589 | -0.014435 | 0.321079 | 0.003150 | 0.090557 | 0.499954 |
| C75 | 0.102950 | -0.005796 | 0.303386 | 0.001773 | 0.136141 | 0.247734 |
| C90 | 0.100002 | -0.002848 | 0.296276 | 0.000898 | 0.172807 | 0.099315 |
| Q50 | 0.099616 | -0.002462 | 0.293380 | 0.000359 | 0.299727 | 0.000000 |
| Q75 | 0.099322 | -0.002168 | 0.292962 | 0.000326 | 0.301110 | 0.000000 |
| Q90 | 0.099227 | -0.002073 | 0.291242 | 0.000349 | 0.301552 | 0.000000 |
| E-positive | 0.102895 | -0.005741 | 0.301935 | 0.004595 | 0.197272 | 0.000000 |

`LOO7` is a preregistered critical diagnostic and is not automatically eligible
for first37 selection. The cached results do not satisfy or replace the hard
live benefit-preservation gate. In particular, critical KL measures drift from
the original D1 policy and is not a correctness score for loss tasks.

## Artifacts

- Aggregate: `research/analysis/exp031b_stage8a_candidate_summary.json`
- Per-state/candidate Git-safe ledger: `research/analysis/exp031b_stage8a_candidate_rows_git_safe.jsonl`
- Immutable raw root: `/lambda/nfs/rcmf-persist/project/runs/stage_c/rcmf_benefit_preserving_calibration_9b_20260827_001/stage_8a`

## Decision

**VERIFIED:** exact G100/bare equivalence, unlabeled calibration, and all cached
diagnostics passed.

**UNVERIFIED:** no calibration candidate has yet preserved the six original
gains and two retained successes in fresh same-world replay. Stage 8B remains
mandatory before heldout-live or first37 work.
