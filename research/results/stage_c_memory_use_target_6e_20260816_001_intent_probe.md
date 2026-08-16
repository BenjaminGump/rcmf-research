# EXP-021 Query-Intent Probe And Calibration

The EXP-020 state-only probe was reproduced/reused and calibrated using train
decision examples only. The cache stores distributions for app, API, coarse
action type, and completion for all 92 query states. Held-out labels were used
only once for evaluation.

| Head | Accuracy | Shuffled | ECE | Brier | NLL | Temp | Coverage |
|---|---:|---:|---:|---:|---:|---:|---:|
| App | .944444 | .277778 | .076501 | .119439 | .476723 | .5 | 1.0 |
| API | .833333 | .222222 | .175951 | .327823 | 1.096768 | .5 | 1.0 |
| Action type | .888889 | .500000 | .120496 | .224034 | 1.005315 | .5 | 1.0 |
| Completion | 1.000000 | 1.000000 | .000360 | .000004 | .000361 | .5 | 1.0 |

The completion head's held-out set contains only false labels, so its perfect
accuracy carries no discriminative evidence. Confusion matrices and class
vocabularies are preserved in `intent_probe_calibration.json`.

VERIFIED: app/API/action-type intent generalizes much better than shuffled
state controls. VERIFIED: oracle and predicted intent compatibility both fall
below the locked transition-only NDCG@4 on D, so intent alone does not satisfy
the memory-use target gate.

