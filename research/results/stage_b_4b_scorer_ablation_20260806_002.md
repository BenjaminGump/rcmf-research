# Milestone 4B Scorer Ablation Report

Date: 2026-08-06.

## VERIFIED

- Source commit:
  `e61981fdd10514ba3250f32176f45ea21c2d0661`.
- Artifact:
  `/lambda/nfs/rcmf-persist/project/runs/stage_b/addressing_4b_20260806_002`.
- Effective memory bank size: 36.
- Split: 37 train tasks / 499 train states and 9 validation tasks / 139
  validation states.
- Seeds: 1, 2, 3.
- No Stage C, program-head training, injector, Qwen action loss, full RCMF
  training, or AppWorld evaluation was run.

## Models

- Global memory prior: `score(s,i)=mu_i`, where `mu_i` is estimated from train
  labels only.
- State-only residual head:
  `score(s,i)=mu_i+MLP(state_representation)_i`.
- Signed two-tower residual scorer:
  `score(s,i)=mu_i+dot(f_s, f_i)/sqrt(d)`.
- Current hard-top-k Stage-B control:
  `q(s,i)=rho_i*dot(b(s), alpha_i)`.
- Dense separate-head address:
  dense-softmax `b/alpha`, frozen `mu_i`, signed residual dot, separate gate.
- Dense shared-head address:
  same as dense separate-head but with state and memory address heads
  identically initialized.

## Validation Metrics

Three-seed mean/std:

| model | NDCG@1 | NDCG@4 | NDCG@8 | Recall@1 | Recall@4 | Recall@8 | PosMass@1 | PosMass@4 | PosMass@8 | MRR | Pairwise | Spearman |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| global prior | 0.437061/0.373199 | 0.453376/0.304515 | 0.490111/0.266428 | 0.064748/0.246081 | 0.158273/0.364997 | 0.359712/0.479916 | 0.042724/0.051097 | 0.141993/0.128717 | 0.288748/0.183200 | 0.170227/0.240080 | 0.609083/0.213639 | 0.150585/0.248227 |
| state-only residual | 0.563085/0.016642 | 0.571722/0.015435 | 0.590859/0.027885 | 0.129496/0.005874 | 0.304556/0.033401 | 0.465228/0.041258 | 0.066557/0.014641 | 0.214541/0.006305 | 0.357572/0.026763 | 0.247222/0.007280 | 0.690849/0.047173 | 0.254761/0.068279 |
| signed two-tower | 0.525731/0.029833 | 0.547162/0.026890 | 0.569990/0.015785 | 0.103118/0.003391 | 0.268585/0.008973 | 0.407674/0.020629 | 0.066443/0.004458 | 0.204190/0.003365 | 0.343570/0.002078 | 0.219770/0.004413 | 0.675477/0.012826 | 0.234346/0.039392 |
| current hard-top-k | 0.371993/0.015983 | 0.386161/0.042185 | 0.413739/0.046103 | 0.074341/0.003391 | 0.175060/0.013566 | 0.256595/0.057654 | 0.050589/0.007686 | 0.147272/0.010647 | 0.244730/0.025002 | 0.165514/0.013510 | 0.184593/0.261054 | 0.088391/0.000000 |
| dense separate-head | 0.437061/0.000000 | 0.453376/0.000000 | 0.490111/0.000000 | 0.064748/0.000000 | 0.158273/0.000000 | 0.359712/0.000000 | 0.042724/0.000000 | 0.141993/0.000000 | 0.288748/0.000000 | 0.170227/0.000000 | 0.609083/0.000000 | 0.150585/0.000000 |
| dense shared-head | 0.437061/0.000000 | 0.453376/0.000000 | 0.490111/0.000000 | 0.064748/0.000000 | 0.158273/0.000000 | 0.359712/0.000000 | 0.042724/0.000000 | 0.141993/0.000000 | 0.288748/0.000000 | 0.170227/0.000000 | 0.609083/0.000000 | 0.150585/0.000000 |

## Correct-Versus-Shuffled Deltas

Three-seed mean/std:

| model | delta NDCG@4 | delta PosMass@4 | delta MRR | delta Spearman |
| --- | ---: | ---: | ---: | ---: |
| state-only residual | 0.189911/0.024032 | 0.093684/0.016867 | 0.110690/0.014025 | 0.206033/0.053551 |
| signed two-tower | 0.144968/0.042046 | 0.060004/0.007301 | 0.067382/0.019487 | 0.194256/0.075450 |
| current hard-top-k | 0.000000/0.000000 | 0.000000/0.000000 | 0.000000/0.000000 | 0.000000/0.000000 |
| dense separate-head | 0.000000/0.000000 | 0.000000/0.000000 | 0.000000/0.000000 | 0.000000/0.000000 |
| dense shared-head | 0.000000/0.000000 | 0.000000/0.000000 | 0.000000/0.000000 | 0.000000/0.000000 |

Representative seed-1 paired bootstrap 95% CIs for NDCG@4:

- state-only residual minus global: `[0.043629, 0.160099]`;
  state-only residual minus shuffled: `[0.133228, 0.244065]`.
- signed two-tower minus global: `[0.079347, 0.189871]`;
  signed two-tower minus shuffled: `[0.135080, 0.262828]`.
- current hard-top-k minus global: `[-0.160751, -0.035321]`;
  current hard-top-k minus shuffled: `[0.0, 0.0]`.
- dense separate-head minus global: `[0.0, 0.0]`;
  dense separate-head minus shuffled: `[0.0, 0.0]`.

## Residual Prediction

Representative seed-1 residual stats:

| model | best epoch | residual MSE | Huber | residual corr | interaction variance |
| --- | ---: | ---: | ---: | ---: | ---: |
| state-only residual | 8 | 0.127376 | 0.020842 | 0.285458 | 0.071971 |
| signed two-tower | 45 | 0.502934 | 0.050534 | 0.289186 | 0.505124 |
| dense separate-head | 1 | 0.105900 | 0.017463 | 0.167137 | 3.47e-11 |
| dense shared-head | 1 | 0.105908 | 0.017467 | 0.206638 | 1.37e-10 |

Dense residual MSE looks superficially small because the residual prediction is
almost constant near zero and Huber is dominated by small residuals. Ranking
metrics show the dense interaction contributed essentially nothing beyond
`mu_i`.

## Geometry

Current hard-top-k control best checkpoints:

- seed 1: zero support overlap `1.0`, zero dot `1.0`, state/alpha top-1 load
  `1.0/1.0`.
- seed 2: zero support overlap `0.0`, every pair had support intersection 1,
  state/alpha top-1 load `1.0/1.0`.
- seed 3: zero support overlap `1.0`, zero dot `1.0`, state/alpha top-1 load
  `1.0/1.0`.

Dense address variants:

- Dense separate-head: zero top-k support overlap `1.0` for all seeds, but
  dense softmax raw dots were nonzero; state-interaction variance was near
  zero and metrics exactly matched global prior.
- Dense shared-head: same qualitative result. Identical initialization was not
  enough to recover a state-conditioned residual interaction.

Gate behavior for dense seed 1:

- Dense separate-head gate mean `0.758072`; no-positive gate mean `0.753746`;
  false activation at 0.5 was `1.0`.
- Dense shared-head gate mean `0.795971`; no-positive gate mean `0.792928`;
  false activation at 0.5 was `1.0`.

## Decision Tree

Branch reached: `dense_rcmf_address_failed`.

Reason:

- State-only residual succeeds, so the state representation and labels have
  held-out-task signal.
- Signed two-tower succeeds, so frozen memory representations can participate
  in a state-conditioned scorer.
- Dense RCMF-compatible address fails and collapses to the global prior, so
  the current address parameterization is the bottleneck.

Stage C remains blocked regardless of the diagnostic successes.

