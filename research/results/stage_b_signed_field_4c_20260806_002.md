# Milestone 4C Signed Residual Associative Field

Date: 2026-08-06.

## VERIFIED

- Source commit:
  `2fc95e2d41da933810df53e78a0eed62c972ee70`.
- Artifact:
  `/lambda/nfs/rcmf-persist/project/runs/stage_b/signed_field_4c_20260806_002`.
- Runtime: `907.20` seconds, about `0.252` H100 wall-clock hours.
- Local and Lambda tests passed: `69 passed`.
- Hard scope was preserved: no Stage C, no program-head training, no injector
  construction/training, no Qwen action loss, no full-bank RCMF training, and
  no AppWorld agent evaluation.
- Inputs:
  `/lambda/nfs/rcmf-persist/project/runs/stage_b/student_labels_20260806_002`,
  `/lambda/nfs/rcmf-persist/project/runs/teacher/raw_text_full_cache_20260805_001`,
  and cached Qwen state/memory representations under
  `/lambda/nfs/rcmf-persist/project/runs/experiments/appworld_qwen_repr_full_prompt_filtered_no_2a163ab3_20260803_101000/train/representation_cache`.

## Mechanism

- Frozen train-derived global prior:
  `score(s,i) = mu_i + residual(s,i)`.
- Core signed field:
  `q_s = state_query_network(h_s)`,
  `k_i = memory_key_network(h_i)`,
  `residual(s,i)=temperature * dot(q_s,k_i)/sqrt(rank)`.
- The residual interaction uses no softmax, top-k, sparsemax, sigmoid, ReLU,
  clamp, or rho multiplication.
- The activation gate is separate and does not multiply the ranking score in
  this milestone.

## Reference Reproduction

- Reference signed two-tower and refactored core signed field matched exactly
  after copied weights:
  residual, gate, q, and k max absolute errors were all `0.0`.
- Continuity split reference and core metrics were identical under the same
  seeds, confirming no refactor mismatch:
  NDCG@4 `0.555174/0.018107`.

## Continuity Split

Three-seed mean/std on the existing 37-train-task / 9-validation-task split:

| model | NDCG@4 | PosMass@4 | MRR | Spearman | delta NDCG@4 | delta PosMass@4 | residual MSE | residual corr |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| global memory prior | `0.453376/0.304515` | `0.141993/0.128717` | `0.170227/0.240080` | `0.150585/0.248227` | `NA` | `NA` | `NA` | `NA` |
| frozen-Qwen hidden cosine | `0.366233/0.268877` | `0.129411/0.151647` | `0.114033/0.108504` | `0.027727/0.246838` | `NA` | `NA` | `NA` | `NA` |
| state-only residual upper bound | `0.590775/0.016531` | `0.216996/0.004691` | `0.260659/0.020210` | `0.302775/0.024688` | `0.197830/0.019606` | `0.090412/0.014297` | not primary | not primary |
| signed two-tower reference r128 | `0.555174/0.018107` | `0.202908/0.008782` | `0.234205/0.011910` | `0.246267/0.019878` | `0.162368/0.025262` | `0.076659/0.005683` | `0.313298` | `0.279242` |
| signed core field r128 | `0.555174/0.018107` | `0.202908/0.008782` | `0.234205/0.011910` | `0.246267/0.019878` | `0.162368/0.025262` | `0.076659/0.005683` | `0.313298` | `0.279242` |
| signed core field r64 | `0.537045/0.013213` | `0.202676/0.007095` | `0.225747/0.016347` | `0.212661/0.011512` | `0.117082/0.019127` | `0.059837/0.002904` | see artifact | see artifact |
| normalized signed core r128 | `0.570537/0.015114` | `0.201243/0.002476` | `0.233043/0.006267` | `0.266473/0.034157` | `0.194207/0.019699` | `0.083000/0.009090` | `0.517521` | `0.319162` |
| learned-prior signed core r128 | `0.573485/0.018479` | `0.205699/0.011101` | `0.242195/0.011192` | `0.284761/0.023546` | `0.186268/0.023164` | `0.087546/0.013485` | `0.490553` | `0.306924` |

The core signed field r128 continuity gate passed:

- NDCG@4 improvement over global prior: `+0.101798`.
- Positive mass@4 improvement over global prior: `+0.060914`.
- Correct-minus-shuffled NDCG@4: `0.162368`.
- Correct-minus-shuffled positive mass@4: `0.076659`.
- Interaction variance: `0.291447`.
- Minimum per-seed paired bootstrap lower bound for NDCG@4 minus global:
  `0.028544`.
- Core NDCG@4 was exactly within the required `0.02` of the exact reference.

## Cross-Validation

Five deterministic task-grouped folds over the original 37 training tasks:

| fold | memory count | global NDCG@4 | core NDCG@4 | improvement | delta NDCG@4 |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 0 | 28 | `0.428311` | `0.589641` | `0.161331` | `0.203215` |
| 1 | 28 | `0.408065` | `0.546138` | `0.138073` | `0.107468` |
| 2 | 30 | `0.424144` | `0.536262` | `0.112118` | `0.150979` |
| 3 | 29 | `0.463005` | `0.462252` | `-0.000753` | `0.009391` |
| 4 | 29 | `0.477548` | `0.492174` | `0.014626` | `0.043004` |

Cross-validation gate passed:

- Mean NDCG@4 improvement over fold-specific global prior:
  `0.085079/0.065855`.
- Mean correct-minus-shuffled NDCG@4:
  `0.102811/0.070323`.
- Positive improvement in `4/5` folds.

## Gate And Geometry

Core signed field r128:

- Gate AUROC/AUPRC: `0.851812/0.004888`,
  `0.964167/0.001949`.
- Balanced accuracy: `0.711715/0.021149`.
- False activation: `0.472222/0.051967`.
- Positive-state gate mean: `0.908799/0.018320`.
- No-positive-state gate mean: `0.488292/0.043613`.
- Interaction variance: `0.291447`.
- Prior variance: retained from frozen train-derived `mu_i`.
- Correct-vs-shuffled valid interaction absolute delta and full signed
  geometry are in `summary.json`.

Learned prior ablation:

- Prior-head train MSE by seed:
  `0.00420031`, `0.00462545`, `0.00598329`.
- Prior-head train correlation by seed:
  `0.439933`, `0.490678`, `0.544720`.
- Learned-prior signed core r128 NDCG@4:
  `0.573485/0.018479`, so it did not fail the deployability ablation gate.

## Field Algebra

The signed associative-field algebra validation passed at rank `128`,
program dim `32`, and bank count `36` using float64:

- `q^T sum_i DeltaV_i == sum_i dot(q,k_i) p_i` max error:
  `8.53e-14`.
- `q^T sum_i DeltaG_i q == sum_i dot(q,k_i)^2` error: `0.0`.
- Add/remove exact norms: `0.0`, `0.0`.
- Replace max errors: `0.0`, `0.0`.
- Arbitrary add/remove order final norms:
  `1.05e-13`, `2.27e-13`.

The superseded `_001` run had the same model metrics but marked field algebra
as failed because the validation used float32 accumulation with an overly tight
absolute tolerance. The code and final `_002` run corrected the validation to
use float64 for the algebra proof.

## Decision

- Continuity split gate: passed.
- Cross-validation gate: passed.
- Reference reproduction: passed.
- Previous 4B signed two-tower reproduction: passed.
- Rank64 did not fail the simple improvement test, though rank128 remains the
  provisional default.
- Learned-prior ablation did not fail.
- Branch reached:
  `signed_core_field_passed_recommend_stage_c_pilot`.

Stage C remains intentionally unlaunched in this milestone. The next review
should decide whether to start a signed-program distillation pilot.

