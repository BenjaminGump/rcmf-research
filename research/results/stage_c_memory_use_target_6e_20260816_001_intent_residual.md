# EXP-021 Intent-Only And Content-Residual Audit

## Intent Baselines

| Intent source | B NDCG@4 | C NDCG@4 | D NDCG@4 | D Spearman | D gap accuracy |
|---|---:|---:|---:|---:|---:|
| Oracle query intent | .204336 | .364124 | .337362 | .073997 | .526623 |
| Predicted query intent | .210625 | .328699 | .344384 | .034798 | .498028 |

The immutable transition-only D NDCG@4 is `.480274`. Oracle and predicted
intent gains are therefore `-.142912` and `-.135889`. Because the oracle gain
is negative, the requested positive-gain retention ratio is undefined and
cannot satisfy the 70% condition.

## Content Residual

Selected T4 field D NDCG@4 is `.433983`, a `.089599` gain over predicted
intent but a `.046291` loss against transition-only. T6 field reaches `.544606`
and residual Spearman `.281752`, but has per-state Spearman `.021913`, gap
accuracy `.559839`, and raw Huber `.584675`; it does not pass the joint gate.
T7 field reaches `.501103` but its residual Spearman and transition-shuffle
drop are only `.010895/.034805`.

VERIFIED: transition content can add isolated predictive signal beyond
predicted intent. VERIFIED: neither intent-only nor intent-plus-relative
residual satisfies the preregistered field-compatible D gate. The branch
`coarse_action_intent_explains_memory_use_signal` is therefore not selected.

