# Scientific Status

Last verified: `2026-09-11T18:54:00Z`.

Portable canonical V2.1 is an engineering hardening milestone. Its bounded
AppWorld three-demo pilot completed all 12 portable phases, including real
training, field operations, Qwen generation, and typed AppWorld evaluation.
This is executable integration evidence only and has no accuracy threshold.
It does not alter or supersede formal 14n or the post-hoc R19 checkpoint
diagnostic. That frozen base milestone originally marked ALFWorld and WebShop
`NOT_EVALUATED`; this later dataset branch now contains one eligible ALFWorld
Track R paired result. WebShop remains outside this branch's scope.

## Engineering Verified

- Formal AppWorld 14n completed all 18 continuation stages with strict hashes.
- Portable v2 defines a dataset-independent adapter/schema/DAG/checkpoint base.
- ReAct ALFWorld and WebShop prompt assets are pinned and hash-verified.
- AppWorld compatibility and ALFWorld-like/WebShop-like bounded conformance are
  required release gates.
- Portable V2.1 local full tests passed `1070` with `3` skips; Lambda/CUDA full
  tests passed `1073`. The bounded AppWorld pilot passed P00-P11 with 20/20
  backward/optimizer steps and strict manifests.
- Final Neutral Harness V1 source `827ed6f394804834e93444c9bb02c435e9e238a3`
  is machine-locked to RCMF integration source
  `4e56702f467635bda120d118a5367c58e593ecef`. Actual lifecycle/result-bundle
  compatibility, source/schema mismatch rejection, and local/Lambda suites
  passed without model execution.

## Scientifically Supported

- Fresh three-demo AppWorld positive control: bare `12/57`, correct `17/57`,
  matched shuffle `11/57`; D22 passed.
- Formal one-demo epoch-1 AppWorld continuation: correct `8/57`, matched shuffle
  `18/57`; this is a negative memory-specificity result.

## Negative Or Inconclusive

- R19 forced epoch-2 post-hoc diagnostic: correct `16/57`, matched shuffle
  `19/57`, classification `EPOCH2_DIAGNOSTIC_MIXED_INCONCLUSIVE`.
- Epoch 2 remains diagnostic and does not replace formal epoch 1.
- ALFWorld action-dialect-v3 Track R completed matched 134-task bare and RCMF
  arms at 45/134 each, with 40 both correct, 84 both wrong, five gains, five
  losses, absolute accuracy delta 0, paired bootstrap 95% CI
  [-0.0447761194, 0.0447761194], and exact McNemar p=1.0. Both complete
  action/evaluator audits and P00-P11 pass. Decision:
  `COMPLETE_ELIGIBLE_TRACK_R_PAIRED_RESULT_NO_OBSERVED_RCMF_IMPROVEMENT`.
  This does not establish equivalence or a Track S result.

## Not Yet Verified

- Portable v2 has no WebShop scientific result.
- ALFWorld Track S has no scientific method result. The eligible result above
  is the distinct byte-exact upstream ReAct `valid_unseen` reference Track R.
- WebShop remains `STOP_WEBSHOP_DATA_IDENTITY_UNRESOLVED`: product,
  instruction, index, and setup-sample identities/terms are not sealed, and no
  benchmark lock exists.
- Portable v2 is not scientifically validated across datasets.

## Deferred

The unusually strong single matched-shuffle permutation is
`DEFERRED_UNTIL_AFTER_2026-09-25_SUBMISSION`; see
`docs/deferred/SHUFFLE_ANOMALY_POST_SUBMISSION.md`. No further shuffle run is
authorized by this milestone.

Authoritative reports are
`research/results/EXP_037A_R18_FORMAL_14N_TERMINAL_RESULT.md` and
`research/results/EXP_037A_R19_EPOCH2_SENSITIVITY.md`.
