# Scientific Status

Last verified: `PORTABLE_V2_LAST_VERIFIED_UTC`.

## Engineering Verified

- Formal AppWorld 14n completed all 18 continuation stages with strict hashes.
- Portable v2 defines a dataset-independent adapter/schema/DAG/checkpoint base.
- ReAct ALFWorld and WebShop prompt assets are pinned and hash-verified.
- AppWorld compatibility and ALFWorld-like/WebShop-like bounded conformance are
  required release gates.

## Scientifically Supported

- Fresh three-demo AppWorld positive control: bare `12/57`, correct `17/57`,
  matched shuffle `11/57`; D22 passed.
- Formal one-demo epoch-1 AppWorld continuation: correct `8/57`, matched shuffle
  `18/57`; this is a negative memory-specificity result.

## Negative Or Inconclusive

- R19 forced epoch-2 post-hoc diagnostic: correct `16/57`, matched shuffle
  `19/57`, classification `EPOCH2_DIAGNOSTIC_MIXED_INCONCLUSIVE`.
- Epoch 2 remains diagnostic and does not replace formal epoch 1.

## Not Yet Verified

- Portable v2 has no ALFWorld scientific result.
- Portable v2 has no WebShop scientific result.
- Neither dataset environment/data installation, official-trajectory replay
  corpus, tokenizer count contract, split manifest, runtime estimate, nor
  formal authorization has completed.
- Portable v2 is not scientifically validated across datasets.

## Deferred

The unusually strong single matched-shuffle permutation is
`DEFERRED_UNTIL_AFTER_2026-09-25_SUBMISSION`; see
`docs/deferred/SHUFFLE_ANOMALY_POST_SUBMISSION.md`. No further shuffle run is
authorized by this milestone.

Authoritative reports are
`research/results/EXP_037A_R18_FORMAL_14N_TERMINAL_RESULT.md` and
`research/results/EXP_037A_R19_EPOCH2_SENSITIVITY.md`.
