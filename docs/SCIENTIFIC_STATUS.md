# Scientific Status

Last verified: `2026-09-11T02:31:57Z`.

## Current cross-dataset boundary

Portable canonical V2.1 remains the engineering base. It is not claimed as
scientifically validated across datasets. AppWorld and WebShop now have
dataset-specific results; ALFWorld remains owned by its separate active task.

## Scientifically supported

- Fresh three-demo AppWorld positive control: bare `12/57`, correct `17/57`,
  matched shuffle `11/57`; D22 passed.
- Formal one-demo epoch-1 AppWorld continuation: correct `8/57`, matched
  shuffle `18/57`; a negative memory-specificity result.
- AgentBench-FC WebShop standard-200 completed all 600 frozen trajectories.
  B0/RCMF-C/RCMF-S mean raw rewards are `0.580250`, `0.581917`, and
  `0.580958`; exact-success counts are `39/200`, `44/200`, and `44/200`.

## Negative or inconclusive

- AppWorld R19 forced epoch-2 post-hoc diagnostic: correct `16/57`, matched
  shuffle `19/57`, classification `EPOCH2_DIAGNOSTIC_MIXED_INCONCLUSIVE`.
- WebShop RCMF-C minus B0 mean paired reward difference is `+0.0016667`, 95%
  bootstrap CI `[-0.0297500, 0.0315854]`; RCMF-C minus RCMF-S is
  `+0.0009583`, CI `[-0.0175000, 0.0187094]`. RCMF-C and RCMF-S both have
  `44/200` exact successes. Classification:
  `RCMF_WEBSHOP_STANDARD200_INCONCLUSIVE_NULL_RESULT`.

## Engineering verified

- Final Neutral Harness V1 is machine-locked to RCMF integration source
  `4e56702f...`.
- AgentBench-FC WebShop runtime/data/index/task/prompt/evaluator identities are
  frozen. The RCMF WebShop method retains an authoritative complete-transition
  ledger, independent feed-forward compilation, reversible contributions,
  fixed-size whole-bank read, frozen deployment, and no raw-memory prompt or
  runtime retrieval.
- WebShop final audit recomputed all 600 task hashes and all summary/analysis
  lock relationships. Six fixed fresh-session semantic reruns match exactly.

## Not claimed

- No task in WebShop `[200,500)` was run; there is no Test-500 result.
- No cross-method WebShop superiority is established.
- No raw WebShop product-data redistribution right is claimed.
- Portable V2.1 is not scientifically validated across all target datasets.

## Deferred

The AppWorld matched-shuffle anomaly remains
`DEFERRED_UNTIL_AFTER_2026-09-25_SUBMISSION`; see
`docs/deferred/SHUFFLE_ANOMALY_POST_SUBMISSION.md`.

Authoritative WebShop report:
`research/results/RCMF_WEBSHOP_AGENTBENCH_FC_STANDARD200_FINAL.md`.

Authoritative AppWorld reports:
`research/results/EXP_037A_R18_FORMAL_14N_TERMINAL_RESULT.md` and
`research/results/EXP_037A_R19_EPOCH2_SENSITIVITY.md`.
