# EXP-025D Clean Behavioral Teacher-Cache Preflight

Date: 2026-08-18  
Run UUID: `state_conditioned_transition_program_7d_20260818_001`  
Source commit: `cd5341412842137be0a250ff2f744c4f309e2b30`

## Required rows

- Unique scoreable state-transition pairs: `970`.
- Unique bare states needed by those pairs: `614`.
- Complete reusable clean top-64 rows: `0`.
- New complete top-64 rows required: `970`.
- Clean scalar-teacher overlap: `17`; these rows do not contain the sparse
  teacher distribution required by EXP-025D and are not counted as reusable.
- Reuse rejection evidence records two clean-lineage failures and two
  transition-content failures in the overlapping legacy caches.

Each new row must retain target-token identity, bare/raw target log probability,
bare/raw top-64 distributions, union support, logsumexp/other-vocabulary bucket,
`L0`, `L_raw`, sequence utility, target delta, renderer/model/tokenizer hashes,
and reconciled lineage. Exact key validation is required before resume skips a
row.

## Context contract

The frozen logical manifests contain `41` over-context occurrences (`37` unique
pairs). They remain explicit missing measurements. The preflight records zero
truncation, zero same-class substitution, and zero cross-class substitution.

No Qwen model was loaded and no forward pass was run in this preflight. The
expected teacher-cache scoring time is `2,930.4 s` (`0.814 h`) at the measured
planning rate. Atomic pair rows plus a bare-state cache provide idempotent resume.

Unique-row manifest:
`/lambda/nfs/rcmf-persist/project/runs/stage_c/state_conditioned_transition_program_7d_20260818_001/preflight/teacher_cache_unique_scoreable_pairs.jsonl`

