# EXP-016C Low-Rank DeltaE Capacity

Status: **completed**

Run ID: `stage_c_oracle_decoder_5fc_20260810_003`

## Method

Each direct DeltaE tensor was flattened, reconstructed with an uncentered
float64 SVD at ranks 16/32/64/128/192, reshaped to K=4 input-embedding
deltas, and evaluated through frozen Qwen3-8B on the same 192 pairs. No
reconstruction was renormalized upward. Ratio overshoot was limited to the
documented floating-point tolerance.

## Primary u112 Results

| Rank | Relative Frobenius error | Mean row cosine reconstruction | Utility Spearman | Sign agreement | Sequence Huber | Neutral mean abs utility | Max ratio |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 16 | 0.865233 | 0.470334 | 0.718237 | 0.762590 | 0.359510 | 0.000149 | 0.949819 |
| 32 | 0.793428 | 0.592089 | 0.894576 | 0.920863 | 0.304591 | 0.000065 | 0.971167 |
| 64 | 0.664323 | 0.738630 | 0.928850 | 0.971223 | 0.209660 | 0.000053 | 0.977983 |
| 128 | 0.392795 | 0.909091 | 0.980704 | 0.992806 | 0.047138 | 0.000128 | 0.989685 |
| 192 | 0.000000 | 1.000000 | 0.984810 | 0.992806 | 0.029525 | 0.000126 | 1.000000 |

## Robustness u128 Results

| Rank | Relative Frobenius error | Mean row cosine reconstruction | Utility Spearman | Sign agreement | Sequence Huber | Neutral mean abs utility | Max ratio |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 16 | 0.865772 | 0.467546 | 0.750622 | 0.805755 | 0.377603 | 0.000143 | 0.950610 |
| 32 | 0.793848 | 0.591001 | 0.881246 | 0.920863 | 0.309249 | 0.000219 | 0.969260 |
| 64 | 0.666153 | 0.736868 | 0.953314 | 0.992806 | 0.209238 | 0.000148 | 0.976812 |
| 128 | 0.394455 | 0.908342 | 0.974470 | 0.992806 | 0.050873 | 0.000212 | 0.990356 |
| 192 | 0.000000 | 1.000000 | 0.979465 | 0.992806 | 0.034512 | 0.000135 | 1.000000 |

## Gate

Rank 128 passed on both targets:

- utility Spearman >= 0.90;
- sign agreement >= 0.90;
- sequence Huber at least 75% below zero;
- neutral mean absolute utility <= 0.05;
- perturbation ratio <= 1.0 within numerical tolerance;
- rank-128 Huber no more than 0.05 worse than the corresponding full direct
  checkpoint.

Rank 192 exactly reproduced both source DeltaE tensors and their Qwen behavior
within numerical tolerance. This rules out an SVD/evaluation implementation
failure and establishes that 128 dimensions are sufficient for a global
linear subspace over these 192 oracle solutions.

This global projection is not a held-out decoder test. The pair-grouped
decoder results are reported separately.
