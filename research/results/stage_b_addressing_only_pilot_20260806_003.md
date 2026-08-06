# Milestone 4 Stage-B Addressing-Only Pilot

Date: 2026-08-06.

## VERIFIED

- Final source commit:
  `9f84b77dfb2e42ef3ec32a51567f376379ee352a`.
- Label artifact:
  `/lambda/nfs/rcmf-persist/project/runs/stage_b/student_labels_20260806_002`.
- Pilot artifact:
  `/lambda/nfs/rcmf-persist/project/runs/stage_b/addressing_only_pilot_20260806_003`.
- Teacher cache:
  `/lambda/nfs/rcmf-persist/project/runs/teacher/raw_text_full_cache_20260805_001`.
- Teacher cache version: `raw_text_memory_teacher_full_cache_v1`.
- No program-head training, additive-token injector training, Qwen action loss,
  full-bank end-to-end RCMF training, or AppWorld agent evaluation was run.

## Label Compiler

- Split manifest seed: 13.
- Train tasks/states: 37 / 499.
- Validation tasks/states: 9 / 139.
- Effective train-memory bank size: 36.
- Excluded memories: 10.
- Exclusion reasons: 9 validation-task memories; 1 train-task memory with zero
  valid Stage-B train labels.
- Special memory:
  `076f5673-6565-5f20-aada-6f16a0f8d4b0`, task `afc0fce_1`,
  `eligible_for_stage_b=false`.
- Label validation passed with error count 0.
- Missing legal teacher pair count: 0.
- Masked own-task pair count: 463.
- Masked over-context pair count: 789.

Label counts:

| split | states | valid rows | positive | neutral | negative | strong+ | strong- | no-positive | all-missing |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| train | 499 | 16,786 | 8,230 | 3,067 | 5,489 | 6,599 | 4,386 | 83 | 8 |
| validation | 139 | 4,930 | 2,412 | 850 | 1,668 | 1,882 | 1,307 | 24 | 0 |

All-missing Stage-B training states:

- `appworld:trace:afc0fce_1:step:29:line:85`
- `appworld:trace:afc0fce_1:step:30:line:86`
- `appworld:trace:afc0fce_1:step:31:line:87`
- `appworld:trace:afc0fce_1:step:32:line:88`
- `appworld:trace:afc0fce_1:step:33:line:89`
- `appworld:trace:afc0fce_1:step:34:line:90`
- `appworld:trace:afc0fce_1:step:35:line:91`
- `appworld:trace:afc0fce_1:step:36:line:92`

Threshold coverage:

| split | threshold | states with >=1 | rows >= threshold |
| --- | ---: | ---: | ---: |
| train | 0.01 | 408 | 8,230 |
| train | 0.05 | 369 | 6,599 |
| train | 0.10 | 340 | 5,475 |
| validation | 0.01 | 115 | 2,412 |
| validation | 0.05 | 103 | 1,882 |
| validation | 0.10 | 91 | 1,466 |

Thresholds were fixed before training and were not selected using validation
labels.

## Training

- Model score:
  `q(s, i) = rho_i * dot(b(s), alpha_i)`.
- Program head: frozen, max absolute delta `0.0` for all seeds.
- Injector: not constructed.
- Qwen action loss: disabled.
- Batch mode: real multi-state task-balanced batches.
- Seeds: 1, 2, 3.
- Early stopping metric: held-out-task validation NDCG@4.
- Tiny overfit: 8 train states, best NDCG@4 `0.616348`.
- Short smoke: best validation NDCG@4 `0.429261`.

## Validation Metrics

Three-seed mean/std:

| metric | learned mean | learned std |
| --- | ---: | ---: |
| NDCG@1 | 0.371993 | 0.015983 |
| NDCG@4 | 0.386161 | 0.042185 |
| NDCG@8 | 0.413739 | 0.046103 |
| best recall@1 | 0.074341 | 0.003391 |
| best recall@4 | 0.175060 | 0.013566 |
| best recall@8 | 0.256595 | 0.057654 |
| positive mass@1 | 0.050589 | 0.007686 |
| positive mass@4 | 0.147272 | 0.010647 |
| positive mass@8 | 0.244730 | 0.025002 |
| MRR | 0.165514 | 0.013510 |
| positive-vs-negative pairwise accuracy | 0.184593 | 0.261054 |
| Spearman | 0.088391 | 0.0 over the single defined seed |
| read mass, positive states | 0.013335 | 0.018858 |
| read mass, no-positive states | 0.010802 | 0.015277 |
| false activation, no-positive states | 0.0 | 0.0 |

Baseline means on the same validation rows/bank:

| model | NDCG@4 | positive mass@4 | MRR |
| --- | ---: | ---: | ---: |
| learned | 0.386161 | 0.147272 | 0.165514 |
| global train utility | 0.453376 | 0.141993 | 0.170227 |
| rho-only | 0.370048 | 0.115186 | 0.125411 |
| frozen-Qwen hidden cosine | 0.366233 | 0.129411 | 0.114033 |
| deterministic random | 0.366264 | 0.116719 | 0.117391 |
| shuffled validation states | 0.386161 | 0.147272 | 0.165514 |

## Geometry

Three-seed geometry means:

- State-address pairwise cosine mean: `0.996045`.
- State-address centered effective rank: `2.269852`.
- State-address top-1 basis load fraction: `1.0`.
- Alpha pairwise cosine mean: `0.997041`.
- Alpha centered effective rank: `2.427664`.
- Alpha top-1 basis load fraction: `1.0`.
- Mean rho: `0.278455`.
- Correct-vs-shuffled score absolute delta mean: `0.000113`.

## Scientific Gate

Failed.

Reason:

- Learned NDCG@4 `0.386161` did not exceed the strongest state-independent
  baseline, global train utility NDCG@4 `0.453376`.
- Shuffled validation-state performance was identical to correct-state
  performance on NDCG@4 and positive mass@4.
- Geometry diagnostics show state-address and alpha collapse.

## Artifacts

- Label summary:
  `/lambda/nfs/rcmf-persist/project/runs/stage_b/student_labels_20260806_002/summary.json`.
- Label report:
  `/lambda/nfs/rcmf-persist/project/runs/stage_b/student_labels_20260806_002/report.md`.
- Pilot summary:
  `/lambda/nfs/rcmf-persist/project/runs/stage_b/addressing_only_pilot_20260806_003/summary.json`.
- Pilot report:
  `/lambda/nfs/rcmf-persist/project/runs/stage_b/addressing_only_pilot_20260806_003/report.md`.
- Seed checkpoints:
  `/lambda/nfs/rcmf-persist/project/runs/stage_b/addressing_only_pilot_20260806_003/seed_1/checkpoint_best.pt`,
  `/lambda/nfs/rcmf-persist/project/runs/stage_b/addressing_only_pilot_20260806_003/seed_2/checkpoint_best.pt`,
  `/lambda/nfs/rcmf-persist/project/runs/stage_b/addressing_only_pilot_20260806_003/seed_3/checkpoint_best.pt`.

## INFERENCES

- The implementation has a live gradient path because tiny overfit improves,
  but the current objective/parameterization does not learn a useful
  state-conditioned ranking on held-out tasks.
- The failure is not merely a rho-only issue: seed 2 improves over rho-only in
  some metrics, but the aggregate still fails global utility and shuffled-state
  controls.
- The next work should diagnose address collapse before adding program or
  injector training.

## UNVERIFIED

- Whether a simpler direct scorer over frozen state/memory representations can
  beat the global utility baseline.
- Whether anti-collapse or contrastive regularization would fix Stage B without
  harming downstream AppWorld behavior.
