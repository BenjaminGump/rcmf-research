# EXP-024A Signature-Balanced Oracle One-Step Causal Audit

## Status

- Run UUID: `procedural_causal_audit_6h_20260817_001`.
- Source commit: `982b1cd3a3d1f1d4b47742ccdf7bfb8d21fd6bd0`.
- Decision branch: `appworld_one_step_replay_invalid`.
- Raw transition content was not behaviorally validated.
- Procedural field and behavioral `p(s,m_transition)` training remain blocked.
- No RCMF V4 tag was created or moved.

## VERIFIED Preflight

- The immutable bank contains 499 transitions in 150 canonical signature
  classes. Of these, 349 transitions occur in 54 duplicate classes and 210
  are API-documentation transitions.
- Signature class sizes are: 96 singleton classes; 27 of size 2; 12 of size 3;
  three each of sizes 4 and 5; one of size 6; two of size 9; and one each of
  sizes 13, 14, 19, 26, 51, and 139.
- The corrected 45-state audit label space has 22,455 legal pairs, 21,624
  scoreable pairs, and 831 over-context pairs. Every state has 499 legal
  candidates; no prompt or memory was truncated.
- Audit strata A/B/C/D/E are `9/23/12/1/0`. The primary non-documentation
  Tier-3/4 set contains 32 states across all 9 held-out tasks, clearing the
  preregistered 18-state/6-task generation precondition.
- The immutable condition manifest contains 323 conditions:
  C0-C5 45 each, C6 37, C7 15, and C8 1. Prompt kinds are 45 bare, 233 raw
  transition, and 45 signature card.
- Maximum prompt length is 40,162 tokens. One condition has only 798 available
  completion tokens under the 40,960-token context limit; neither prompt nor
  memory was truncated.
- Projected H100 time was best/expected/conservative
  `1.7944/4.0375/8.0750` hours, below the 12-hour review threshold. Projected
  generated artifact size was 762,052,608 bytes (0.7097 GiB).

## VERIFIED Exact Replay Failure

- Fresh isolated AppWorld instances were created for all 45 selected states.
  Exact replay passed `0/45` states.
- Only `2/45` states reproduced every prior history observation. Across all
  histories, `81/372 = 21.7742%` observations matched.
- The target-step observation matched in `23/45` states, but no state passed
  both history and target requirements.
- First history divergence occurred at step 1 for 38 states and step 2 for 5
  states. The remaining 2 states had no history; both failed their target-step
  observation check.
- Each of the 9 held-out tasks contributed 5 failed states. No selected task
  passed the replay gate.
- Replay wall time was 89.2913 seconds. Preflight wall time was 14.2779
  seconds. Actual Qwen generations, candidate action executions, and H100
  hours are all zero.

## Version Provenance

VERIFIED:

- Every immutable official trajectory used in the 45-state audit records
  AppWorld code, data, and evaluation version `0.1.0`.
- Lambda currently has AppWorld `0.2.0.dev0`, installed from upstream `main`
  commit `a072b7a86e7c1d5b1d7175659d750ebb9b79f10a`.
- The installed and source trajectory versions do not match.

INFERENCE:

- The AppWorld code/data contract mismatch is the leading explanation for the
  immediate replay divergence. This is not yet a proven causal diagnosis; an
  isolated, hash-verified AppWorld 0.1.0 replay is required to establish it.

## Behavior Comparisons

The replay gate blocked generation before any candidate action. Therefore the
following are `not_run_replay_gate_failed`, not zero-valued results:

- bare Qwen;
- raw procedural oracle;
- signature-only card;
- same-intent hard negative;
- signature-popularity control;
- unrelated transition;
- alternate same-signature exemplar;
- strict-B oracle;
- exact-API Tier-2 diagnostic.

No raw-versus-metadata comparison, same-signature consistency result,
documentation stratification, raw-NLL/outcome correlation, per-task behavioral
confidence interval, or one-step causal gate result exists for this run.

## Recovery and Validation

- `exp024a-preflight-001` failed because the first implementation reused an
  EXP-020-only label subset and omitted 27 immutable one-step states. The
  failed output is preserved. A regression-tested fix recomputed all 45-state
  labels without changing the scientific definition.
- `exp024a-preflight-002` completed the corrected preflight.
- `exp024a-replay-001` recorded the preregistered replay-gate failure.
- `exp024a-finalize-001` wrote this stopped-run diagnosis without crossing the
  gate. All four attempts use one run UUID and record
  `scientific_parameter_changed=false`.
- Independent post-run validation passed `132/132` checks. Local tests passed
  `281` with one skip; Lambda focused tests passed `13`, and the Lambda full
  suite passed `282`.
- Final artifact size is 48,298,504 bytes. Artifact root:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/procedural_causal_audit_6h_20260817_001`.

## Decision

Stop with `appworld_one_step_replay_invalid`. Reproduce the immutable
AppWorld 0.1.0 code/data environment in a separate isolated environment and
rerun only exact replay validation first. Do not resume EXP-024A generation,
train a field/program/injector/selector, start Stage C2, or run AppWorld task
evaluation until all 45 replay states pass under the documented normalization.
