# Handoff: EXP-024A Signature-Balanced Causal Audit

## Status

- Run UUID: `procedural_causal_audit_6h_20260817_001`.
- Source commit: `982b1cd3a3d1f1d4b47742ccdf7bfb8d21fd6bd0`.
- Final record commit: the commit containing this handoff.
- Branch: `appworld_one_step_replay_invalid`.
- Raw transition content is not behaviorally validated.
- Field and behavioral program training remain blocked.

## Verified Preflight

The 499 transitions form 150 signature classes; 349 transitions are in 54
duplicate classes and 210 are API-documentation actions. Corrected audit strata
A/B/C/D/E are `9/23/12/1/0`, with 32 primary states across all 9 tasks. The
manifest fixes 323 conditions. Projected best/expected/conservative cost was
`1.7944/4.0375/8.0750` H100 hours and 0.7097 GiB, below the 12-hour review
threshold.

## Replay Gate

Fresh isolated AppWorld replay passed `0/45` states. Complete histories matched
for 2 states, target observations for 23, and only 81/372 history observations
matched. Thirty-eight states diverged at history step 1, five at step 2, and
both zero-history states failed their target step.

All nine official source artifacts record AppWorld 0.1.0 for code, data, and
evaluation. Lambda runs AppWorld 0.2.0.dev0 from upstream commit
`a072b7a86e7c1d5b1d7175659d750ebb9b79f10a`. The version mismatch is verified;
its causal role is the leading inference, not yet a verified cause.

## Work Not Run

Qwen generations, candidate action executions, baseline/oracle/card/control
comparisons, same-signature consistency, documentation stratification,
raw-NLL/outcome correlation, and task bootstrap intervals are all
`not_run_replay_gate_failed`. Actual H100 hours are zero.

## Provenance and Validation

The append-only ledger has four closed attempts. Preflight attempt 001 exposed
an implementation-only omission of 27 one-step label states; attempt 002
recomputed all 45-state labels under the unchanged definition. Replay attempt
001 stopped at the registered gate. Finalization attempt 001 recorded the
failure without generation. No scientific parameter changed and no duplicate
run was created.

Post-run validation passed `132/132`. Local tests passed `281` with one skip;
Lambda focused tests passed `13`, and its full suite passed `282`. Artifact
size is 48,298,504 bytes. Root:
`/lambda/nfs/rcmf-persist/project/runs/stage_c/procedural_causal_audit_6h_20260817_001`.

## Next Review

Create an isolated, hash-pinned AppWorld 0.1.0 environment and rerun only the
45-state replay preflight. Do not start Qwen generation unless replay passes
45/45. Preserve the current `_001` artifact and attempt ledger; any repair must
use a new reviewed attempt or run identity with explicit environment hashes.
