# Milestone 5C / EXP-013 Selector Repair Result

## Scope

- Run ID: `stage_b_selector_repair_5c_20260807_001`
- Source commit: `5e5c74c43b43dff9a8c2f3d5a054917849b33e29`
- Lambda artifact:
  `/lambda/nfs/rcmf-persist/project/runs/stage_b/selector_repair_5c_20260807_001`
- Log:
  `/lambda/nfs/rcmf-persist/runs/logs/stage_b_5c_selector_repair_20260807_001.log`
- Runtime: `3,687.92` seconds, about `1.02` H100 hours
- Hard scope respected: selector-only retraining; no Stage-C training, no
  injector training, no Stage-C2, no AppWorld generation/evaluation, and no
  Qwen forward passes during selector training.

## Tests

- Local: `python -m pytest -q` -> `84 passed`
- Lambda targeted: `tests/test_selector_repair_5c.py tests/test_stage_c1.py`
  -> `15 passed`
- Smoke with real artifacts: 1 seed, 1 epoch, skipped Stage-C1 projection,
  completed successfully.

## 5-Fold CV Ablation

| Config | CV Pass | R@4 | R@8 | NDCG@4 | NDCG-Global | Delta NDCG@4 | Spearman | Neg-Best |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `C_top_listwise_temp0p03` | false | 0.387077 | 0.616991 | 0.523613 | 0.083399 | 0.131188 | 0.119677 | 0.203096 |
| `C_top_listwise_temp0p05` | false | 0.396569 | 0.593695 | 0.524095 | 0.083880 | 0.143791 | 0.137610 | 0.230109 |
| `C_top_listwise_temp0p10` | false | 0.379966 | 0.589021 | 0.525503 | 0.085289 | 0.127220 | 0.172208 | 0.186612 |
| `E_gap_top_sign_nearbest` | false | 0.371615 | 0.569289 | 0.532469 | 0.092255 | 0.140670 | 0.277150 | 0.247024 |
| `D_gap_top_sign` | false | 0.347010 | 0.544993 | 0.532722 | 0.092507 | 0.140920 | 0.306204 | 0.311096 |
| `A_stage4c_original` | false | 0.311027 | 0.513385 | 0.494348 | 0.054133 | 0.080042 | 0.231299 | 0.179719 |
| `B_gap_all_pairs_gap0p10` | false | 0.240753 | 0.438465 | 0.484661 | 0.044447 | 0.077345 | 0.249964 | 0.299978 |
| `B_gap_all_pairs_gap0p02` | false | 0.236949 | 0.449843 | 0.477503 | 0.037289 | 0.075364 | 0.232498 | 0.382219 |
| `B_gap_all_pairs_gap0p05` | false | 0.233732 | 0.438233 | 0.469699 | 0.029484 | 0.062006 | 0.226191 | 0.332688 |

Selected configuration: `C_top_listwise_temp0p03`.

The selected config improved Recall@8 and preserved NDCG/state-dependence, but
failed the CV gate because Recall@4 was below `0.40`, Spearman did not improve
over the reproduced Stage-4C original baseline, and negative signed scores for
teacher-best memories remained frequent.

## Continuity Split

The selected config was frozen from CV and evaluated on the original 9
validation tasks.

- Raw-teacher-best Recall@1/2/4/8:
  `0.179710 / 0.266667 / 0.359420 / 0.582609`
- Near-best Recall@4/8: `0.646377 / 0.782609`
- Top utility mass@4/8: `0.209168 / 0.353910`
- Teacher-best rank distribution:
  mean `10.034783`, median `7`, p75 `14`, p95 `30`
- Teacher-best negative-score fraction: `0.176812`
- Strong-positive negative-score fraction: `0.407899`
- Raw utility vs signed score Spearman: `0.174524`
- NDCG@1/4/8: `0.584086 / 0.581587 / 0.594970`
- MRR: `0.289532`
- Positive-vs-negative pairwise accuracy: `0.592371`
- Correct-minus-shuffled NDCG@4: `0.206391`
- Correct-minus-shuffled Recall@4/8: `0.226087 / 0.324638`
- Correct-minus-shuffled utility mass@4/8: `0.088429 / 0.122101`

Continuity gate: failed. NDCG@4 and correct-minus-shuffled NDCG@4 passed, but
Recall@8, Recall@4, median rank, negative-score fraction, and Spearman missed
their thresholds.

## Geometry

- Interaction variance: `1.506124`
- Prior variance: `0.001998`
- Correct-vs-shuffled valid interaction delta: `1.276319`
- q centered effective rank: `36.530959`
- k centered effective rank: `17.653024`
- q norm mean: `6.822313`
- k norm mean: `8.971748`
- q pairwise cosine mean: `0.235055`
- k pairwise cosine mean: `0.144642`

The signed field did not collapse.

## Eval-Only Stage-C1 Projection

This diagnostic used existing Stage-C1 content program/injector checkpoints and
replaced only the selector payload.

- Positive validation states: 115 per seed
- State-seed rows: 345
- Teacher-best signed-score rank: mean `10.069565`, median `7`, p75 `15`,
  p95 `29`
- Teacher-best contribution rank: mean `12.576812`, median `10`, p75 `20`,
  p95 `33`
- Teacher-best LOO effect: mean `0.010726`, CI `[0.003277, 0.019094]`
- Selector-top LOO effect: mean `0.027432`, CI `[0.017361, 0.039065]`
- Teacher-best minus selector-top LOO: mean `-0.016706`
- Raw utility versus analytic delta-z norm:
  Pearson `0.062450`, Spearman `0.047117`

Projection interpretation: memory-specific effect magnitude increased relative
to 5B, but selector-top memory still dominates teacher-best memory, and utility
does not correlate enough with analytic contribution.

## Decision

- Branch: `selector_capacity_or_representation_tradeoff`
- CV gate: failed
- Continuity gate: failed
- Stage C remains blocked.
- Next milestone should use explicit pair-level or single-memory behavioral
  grounding before any repeated full-bank Stage-C1 training.

