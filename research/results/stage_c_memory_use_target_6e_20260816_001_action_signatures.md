# EXP-021 Deterministic Action Signatures

## Extraction

The parser records apps, APIs, coarse action type, API-documentation,
authentication, read/query, write/mutation, completion, Python reasoning, and
AST/function-call names. Query signatures come from the ground-truth next
action and are restricted to labels and oracle diagnostics. Transition
signatures come from legal offline memory content.

## Coverage

| Query action type | Count |
|---|---:|
| API documentation | 35 |
| Authentication | 6 |
| Python reasoning | 8 |
| Read/query | 37 |
| Write/mutation | 6 |

| Transition action type | Count |
|---|---:|
| API documentation | 61 |
| Authentication | 8 |
| Python or reasoning | 1 |
| Python reasoning | 12 |
| Read/query | 36 |
| Write/mutation | 30 |

All 92 queries and 148 transitions have deterministic records in
`action_signatures.jsonl`. No held-out query action signature enters a
deployable model input.

## Pair Coverage

Gap-qualified within-state comparisons cover 722,854 pairs at `.02`, 602,989
at `.05`, and 458,076 at `.10`; each threshold covers 87 of 92 states. T7's
same-intent hard comparisons cover 110,888 pairs and 86 states. Missing strata
were reported rather than fabricated.

