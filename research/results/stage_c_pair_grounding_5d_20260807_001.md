# Milestone 5D / EXP-014 Pair-Level Grounding Result

## Scope

- Run ID: `stage_c_pair_grounding_5d_20260807_001`
- Source commit:
  `f8cc37547ec6c3e404f84c726efa01e4c8ccb9f9`
- Lambda artifact:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/pair_grounding_5d_20260807_001`
- Log:
  `/lambda/nfs/rcmf-persist/runs/logs/stage_c_pair_grounding_5d_20260807_001.log`
- Runtime: `57,098.83` seconds, about `15.86` H100 hours
- Hard scope respected: selector bypassed on purpose, no selector training,
  no selector score/gate/mu/full-bank aggregation in the primary model, Qwen
  frozen, teacher-forced target scoring only, no AppWorld generation/evaluation,
  no Stage C2, and no end-to-end RCMF training.

The primary Stage-5D model deliberately uses the single-memory read
`z(s,i)=p_i`. This is a causal-isolation diagnostic for the program/injector
channel, not a replacement for the signed Stage-B selector field.

## Tests

- Local targeted:
  `tests/test_pair_grounding_5d.py` -> `5 passed`
- Local Stage-C1 plus Stage-5D targeted:
  `tests/test_stage_c1.py tests/test_pair_grounding_5d.py` -> `16 passed`
- Lambda targeted:
  `tests/test_pair_grounding_5d.py tests/test_stage_c1.py` -> `16 passed`
- Zero-program bare-Qwen equivalence: passed.
- Tiny overfit: passed.

## Pair Response Cache

- Cache version: `stage_c_pair_response_cache_5d_v1`
- Scoring definition: `single_raw_memory_pair_target_top64_delta_v1`
- Pair selection version: `stage_c_pair_selection_5d_v1`
- Pair cache:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/pair_grounding_5d_20260807_001/pair_response_cache`
- Selected pairs: `1,728`
- Train pairs: `1,152`
- State-held-out validation pairs: `576`
- Reused compatible Stage-C1 rows: `88`
- Newly scored rows: `1,640`
- Validation passed: `true`
- Missing category slot count: `0`

Category coverage:

| Split | Positive | Neutral | Negative | Random |
| --- | ---: | ---: | ---: | ---: |
| train | 288 | 288 | 288 | 288 |
| validation | 144 | 144 | 144 | 144 |

Validation checks passed for pair identity, target identity, raw-memory hash,
leakage exclusion, no truncation, L0/Lj utility reproduction, and
top-K-plus-other probability normalization.

## Trainable and Frozen Modules

Trainable:

- content-derived program head;
- free-ID and fixed-random diagnostic program paths where configured;
- additive-token injector with `position=last_user_k`, `K=4`.

Frozen or unused in the primary model:

- Qwen3-8B;
- signed Stage-B selector query/key networks;
- selector gates;
- empirical `mu_i`;
- full-bank aggregation.

Perturbation target selected from train-only smoke: `1.0`.

Train-only ratio smoke:

| Ratio Target | Smoke Score | Mean Delta Ratio |
| ---: | ---: | ---: |
| 1.0 | 2.631821 | 0.019312 |
| 2.0 | 2.654252 | 0.020020 |
| 0.5 | 2.655047 | 0.019165 |

## State-Held-Out Validation

| Model | Target NLL | Sparse KL | Delta Huber | u_text/u_program Spearman | Sign Agreement | Improved Fraction | Mean Delta Ratio |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| content | 0.665915 | 0.318875 | 2.207829 | -0.293472 | 0.403382 | 0.447917 | 1.054877 |
| free_id | 0.670063 | 0.323440 | 2.227599 | -0.162463 | 0.429952 | 0.335069 | 0.951755 |
| fixed_random | 0.669554 | 0.323243 | 2.200430 | -0.357470 | 0.359903 | 0.427083 | 1.040215 |

Content controls, reported as content minus control:

| Control | Target NLL | Sparse KL | Delta Huber |
| --- | ---: | ---: | ---: |
| bare_qwen_zero_field | +0.031912 | +0.009394 | -0.532686 |
| shuffled_program | -0.000166 | +0.000064 | +0.001846 |
| fixed_random_program | +0.032528 | +0.010575 | -0.505043 |
| mean_program | -0.002776 | +0.000277 | -0.000792 |
| zero_program | +0.031912 | +0.009394 | -0.522676 |
| memory_swap | -0.000081 | +0.000281 | +0.000043 |

The content-derived program reduced behavioral-delta Huber versus the zero
field and fixed random program, but it did not improve target NLL over bare
Qwen, did not beat shuffled/mean/memory-swap by a meaningful margin, and had
negative raw-utility versus compiled-utility correlation.

## Memory-Held-Out 5-Fold CV

Content compiler summary over held-out memories:

- Target NLL: mean `0.757031`, std `0.035778`
- Sparse teacher KL: mean `0.363415`, std `0.015975`
- Behavioral-delta Huber: mean `2.323665`, std `0.086253`
- Behavioral-delta MSE: mean `14.118964`, std `0.973639`
- u_text/u_program Spearman: mean `-0.189175`, std `0.052868`
- Positive/negative sign agreement: mean `0.443038`, std `0.017735`
- Improved fraction: mean `0.395759`, std `0.041905`
- Folds with positive u_text/u_program Spearman: `0/5`

Free-ID mean fallback over held-out memories:

- Target NLL: mean `0.760937`, std `0.035079`
- Sparse teacher KL: mean `0.365786`, std `0.015596`
- Behavioral-delta Huber: mean `2.299864`, std `0.075438`
- u_text/u_program Spearman: mean `-0.287924`, std `0.070701`
- Positive/negative sign agreement: mean `0.409309`, std `0.032548`
- Improved fraction: mean `0.400446`, std `0.028949`

The content compiler did not show memory-held-out generalization. It retained
no positive Spearman folds and did not clearly beat the held-out free-ID
fallback on the primary behavioral-delta metric.

## Program Geometry

| Model | Effective Rank | Pairwise Cosine Mean | Norm Mean |
| --- | ---: | ---: | ---: |
| content | 12.712268 | 0.998634 | 11.313701 |
| free_id | 22.146244 | 0.751117 | 11.313632 |
| fixed_random | 33.632120 | -0.006114 | 11.313703 |

The content programs are highly aligned with one another, which is consistent
with weak memory identity specificity.

## Decision

Decision branch: `program_injector_behavioral_channel_insufficient`.

Scientific gates:

- Channel capacity gate: failed. Even though the tiny overfit and zero-delta
  checks passed, trainable program models did not significantly beat
  zero/random/shuffled controls on useful teacher behavioral-delta
  reconstruction.
- Memory-content grounding gate: failed. Content Spearman was `-0.293472`,
  sign agreement was `0.403382`, and memory-swap degradation was effectively
  zero.
- Compiler generalization gate: failed. Held-out-memory content Spearman was
  negative on average, with `0/5` positive folds.
- Pair-level grounding passed: no.
- Stage C2 allowed from this run: no.

Recommended repair:

- Stop before Stage C2.
- Diagnose the additive-token/program channel with an even simpler
  teacher-forced capacity test, such as an oracle trainable per-pair vector or
  free per-pair `z`, before attempting memory-content compilation again.
- If the oracle channel can reproduce target-position teacher deltas, then
  revisit memory-program compiler capacity and representation choice; if it
  cannot, redesign the injector or behavioral loss before using any full-bank
  aggregation.

## Artifacts

- Summary:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/pair_grounding_5d_20260807_001/summary.json`
- Report:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/pair_grounding_5d_20260807_001/report.md`
- Pair cache:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/pair_grounding_5d_20260807_001/pair_response_cache`
- State-held-out checkpoints:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/pair_grounding_5d_20260807_001/state_heldout`
- Run log:
  `/lambda/nfs/rcmf-persist/runs/logs/stage_c_pair_grounding_5d_20260807_001.log`

## Runtime Status

- No active `stage5d_exp014` tmux session remained after completion.
- GPU memory/utilization checked post-run: `0 MiB / 0%`.
- Safe to terminate the Lambda process.
