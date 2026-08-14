# EXP-017 Decision-Transition Memory Unit Feasibility Pilot

Status: **completed**

Run ID: `stage_c_transition_memory_6a_20260814_001`

Final behavior source commit:
`88f9da7be7bcf6380d9df8ba1ce75b78bc14f9b6`

Branch: `research/v4-decision-transition-memory`

Artifact root:
`/lambda/nfs/rcmf-persist/project/runs/stage_c/transition_memory_6a_20260814_001`

Decision branch: `static_transition_program_insufficient`

## Scope

EXP-017 tested whether one complete AppWorld decision transition, consisting
of pre-action state, complete action, and complete observation, is a better
atomic memory unit than one whole successful trajectory. Qwen3-8B, the
EXP-016C u112 rank-128 no-bias linear decoder, K=4, and `last_user_k` remained
frozen. The run used teacher-forced target scoring only.

No signed selector, content compiler, production full bank, Stage C2, Qwen
fine-tuning, AppWorld generation/evaluation, or end-to-end RCMF training was
run. EXP-016D was not launched.

## V3 Freeze

| Item | Identity |
|---|---|
| V3 source state | `97ca723ad66597d2afcbbce1eb5466eb34c009f6` |
| V3 freeze commit | `2eb1281ff66792aeb082cce39f6a362697f132e6` |
| Annotated tag | `rcmf-v3-component-validated-pre-transition` |
| Tag object | `ee36ad8bad73858b7d6228febb4f90b6156d5979` |
| Tag peeled commit | `2eb1281ff66792aeb082cce39f6a362697f132e6` |
| Archive branch | `archive/rcmf-v3-component-validated` at `2eb1281...` |
| Initial V4 candidate | `research/v4-decision-transition-memory` at `2eb1281...` |

All refs were verified remotely. The V3 tag and archive branch were not moved.
The version manifest is
`research/versions/RCMF_V3_COMPONENT_VALIDATED_PRE_TRANSITION.md`.

## Preflight And Extraction

| Quantity | Count |
|---|---:|
| Train parent trajectories | 37 |
| Excluded validation parents | 9 |
| Extracted transitions | 499 |
| Deterministic panel transitions | 148 |
| Train / held-out query states | 24 / 8 |
| Cartesian query-panel pairs | 4,736 |
| Illegal same task/episode/replay/lineage pairs | 96 |
| Exact legal pairs | 4,640 |
| Scoreable pairs | 4,579 |
| Over-context masked pairs | 61 |

Extraction reconstructed all 37 source trajectories exactly and produced 499
unique stable transition IDs. The panel covers all 37 parents and contains
64/34/50 early/middle/late transitions. Its action types are 61 API-doc, 14
mutation, 61 read/login, and 12 Python/reasoning transitions. The complete set
contains 37 completion transitions; the deterministic panel contains none
because the requested final non-completion position existed for every parent.

The previously excluded long parent
`076f5673-6565-5f20-aada-6f16a0f8d4b0` was decomposed and retained at the
transition level. It accounts for 49 of the 61 over-context pairs, while its
remaining legal transition pairs are usable. Nothing was truncated.

The projected GPU costs were 7.596 best-case, 8.687 expected, and 12.270 H100
hours conservative. The 12-hour value was clarified as a preflight review
threshold only. It never gated u128 continuation or reduced the design.

## Raw-Transition Teacher

The teacher cache contains all 4,640 legal pairs. Validation found 4,579
finite scored rows, 61 correctly masked over-context rows, zero duplicates,
and zero errors.

| Utility statistic | Value |
|---|---:|
| Positive (> 0.01) | 2,271 (49.60%) |
| Neutral | 941 (20.55%) |
| Negative (< -0.01) | 1,367 (29.85%) |
| Mean / std | 0.059851 / 0.289576 |
| Min / p05 / median / p95 / max | -1.652619 / -0.345125 / 0.008705 / 0.535514 / 1.681496 |

Mean utility by source-step bucket was 0.042587 early, 0.076038 middle, and
0.071572 late. Mean utility by app ranged from 0.022439 for file-system
transitions to 0.095922 for phone transitions. Same-app transition pairs did
not dominate: their mean was 0.047129 versus 0.062066 cross-app.

The largest absolute measured length/overlap correlation was only 0.105110.
Exact normalized target substrings occurred in 586/4,579 rows (12.80%), but
their utility correlation was -0.069662. There were 1,949 non-copy positive
rows; exact matches were 14.18% of positive rows and 10.70% of the top utility
decile. Representative serialization and deterministic rescoring checks
passed. Teacher gate 1 passed every check.

### Transition Versus Parent Utility

There were 1,120 matched state-parent teacher comparisons in 1,160
state-parent groups; 40 parent rows were unavailable. The best child
transition beat the corresponding whole-trajectory teacher in 885/1,120
(79.02%) comparisons, with mean best-transition-minus-parent utility 0.092779.
Helpful parent groups contained 1.958 helpful transitions on average. There
were 218 groups where a helpful parent also contained a harmful child
transition, and median positive top-1 concentration was 0.456240. Thus useful
teacher signal is often transition-local and a whole trajectory mixes helpful
and harmful steps.

## Algebra And Decoder

Transition add/remove, parent deletion/restoration/replacement, arbitrary
insertion order, V/G restoration, and field-read equivalence all passed. A
parent ledger item can be deleted by subtracting all child transition deltas.

The frozen decoder basis has shape `[128,16384]`; its SHA256 is
`123ecbf3e6e91bedab4c6669c969901bfe41c9ee955e52e9f2fb32eabf01d38d`.
The hash was unchanged after every run. EXP-016C source-state overlap was
empty. Zero latent produced zero DeltaE, and maximum bare-Qwen NLL deviation
was `2.3842e-7` under a `2e-4` tolerance.

## Pair-Oracle Reachability

The deterministic balanced set contains 64 pairs, 16 from each
positive/neutral/negative/random selection category.

| Updates/pair | Utility Spearman | Sequence Huber |
|---:|---:|---:|
| 2 | 0.912821 | 0.080640 |
| 8 | 0.965110 | 0.054042 |
| 16 | 0.971795 | 0.038053 |
| 32 | 0.985027 | 0.015241 |
| 64 | 0.957418 | 0.028181 |

u64 deteriorated beyond the best-value guard, so the pre-registered train-only
rule stopped before u128. The formal u64 evaluation still passed every pair
capacity gate:

- Spearman 0.957418 and Pearson 0.754638;
- positive/negative sign agreement 0.976744;
- sequence Huber 0.028181 versus zero 0.105161, a 73.20% reduction;
- neutral mean absolute student utility 0.004907;
- maximum perturbation ratio 1.0000001;
- correct-minus-zero Huber -0.076980, paired-bootstrap 95% CI
  `[-0.142226,-0.004085]`.

Gate 2 passed. The conditional direct-DeltaE oracle was therefore not run.

## Static Transition Programs

Twenty-four transition identities covering at least 12 parents were selected
using train labels only. They used 574 train and 191 held-out pairs. Every
identity received exactly 64 updates; u128 was rejected because u64 Huber
deteriorated beyond the train-only best guard.

| Held-out control | Spearman | Sign agreement | Sequence Huber |
|---|---:|---:|---:|
| Correct transition | 0.123261 | 0.543103 | 0.052237 |
| Shuffled | 0.209712 | 0.629310 | 0.050341 |
| Fixed random | 0.150892 | 0.534483 | 0.029014 |
| Mean | 0.006314 | 0.594828 | 0.028996 |
| Zero | -0.124598 | 0.405172 | 0.028513 |
| Different-transition swap | 0.115846 | 0.560345 | 0.053383 |

Correct transition latents were worse than zero by +0.023724 Huber, 95% CI
`[0.009385,0.041153]`, and worse than random by +0.023223, CI
`[0.009094,0.040916]`. Correct-minus-shuffled and correct-minus-swap CIs both
crossed zero. Positive teacher pairs received mean compiled utility -0.011255,
despite mean text utility +0.087363. No held-out task was positive.

Latents did not geometrically collapse: centered effective rank was 19.578,
mean pairwise cosine 0.08645, and maximum perturbation ratio was 1.0. The
failure is behavioral, not a constant-vector collapse. Gate 3 failed.

## Whole-Trajectory Static Baseline

One long parent had no valid whole-trajectory label and remained in the
transition ledger but was masked from this baseline. The baseline used 23
parent identities, 548 train pairs, and 180 held-out pairs, with 64 equal
updates per identity.

| Held-out control | Spearman | Sign agreement | Sequence Huber |
|---|---:|---:|---:|
| Correct trajectory | 0.337996 | 0.707965 | 0.092187 |
| Shuffled | 0.155441 | 0.610619 | 0.096727 |
| Fixed random | 0.191051 | 0.584071 | 0.043891 |
| Mean | -0.248209 | 0.451327 | 0.046665 |
| Zero | -0.182951 | 0.345133 | 0.043670 |
| Different-trajectory swap | 0.232980 | 0.663717 | 0.092942 |

The correct trajectory latent was worse than zero by +0.048518 Huber, 95% CI
`[0.013317,0.090175]`, and worse than random by +0.048296, CI
`[0.014266,0.090617]`. It passed the standalone Spearman and sign thresholds
but failed Huber, control, and held-out-task checks. Its centered effective
rank was 19.151, so this also was not a simple geometric collapse.

## Granularity And Decision

Transition minus trajectory deltas were:

- utility Spearman: -0.214735;
- sign agreement: -0.164861;
- normalized Huber reduction: +0.278954;
- swap sensitivity: +0.000392;
- held-out-task consistency: 0.

Only one of five material-advantage checks passed; at least two were required.
Transition granularity was **not behaviorally validated** under a static
state-independent program. The reached branch is:

`static_transition_program_insufficient`

This does not invalidate the transition teacher. It shows that the pair effect
is reachable through the decoder but cannot be compressed into one fixed
latent per transition across query states. The next reviewed milestone should
test an explicitly state-conditioned transition program `p(s,m_transition)`
or equivalent state-program interaction. It should not return directly to a
larger static encoder.

## Runtime And Recovery

- Preflight CPU runtime: 358.665 seconds.
- Teacher GPU runtime: 8,189.753 seconds (2.275 H100 hours).
- Successful resumed behavior runtime: 7,962.770 seconds (2.212 H100 hours).
- Canonical teacher plus final resumed behavior accounting: 4.487 H100 hours.
- Operational active-GPU estimate including two preserved failed attempts:
  6.586 H100 hours; recovery overhead was about 2.099 hours.
- Final artifact size: 1,187,801,083 bytes (about 1.106 GiB).

Attempt 1 stopped after the complete pair oracle because the static runner
signature omitted `expected_validation_task_ids`. Attempt 2 stopped before the
first static update because an unused copy of that argument remained on the
internal evaluator. Both exit codes and logs were preserved. Commits
`4fb41e5` and `88f9da7` added regression coverage; no pair IDs, labels,
architecture, loss, K, injection site, ratio, update schedule, or gate changed.

## Validation And Artifacts

Independent post-run validation passed with zero errors. It verified 4,640
teacher rows and response caches of 64 pair-oracle, 765 static-transition, and
728 trajectory-baseline rows. Local tests passed `162 passed, 1 skipped`; the
Lambda suite passed `163 passed`.

Key paths under the artifact root:

- `teacher_cache.jsonl` and `teacher_summary.json`;
- `behavior/pair_oracle/pair_oracle_summary.json`;
- `behavior/static_transition/summary.json`;
- `behavior/trajectory_baseline/summary.json`;
- `behavior/granularity_advantage.json`;
- `postrun_validation.json`;
- pair/static/trajectory u64 checkpoints under each behavior subdirectory.

Final status: no tmux server, no active experiment process, GPU 0 MiB / 0%,
and the Lambda instance is safe to terminate after final Git synchronization.
