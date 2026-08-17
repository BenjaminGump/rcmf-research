# EXP-024R Structured Handoff

## State

- Branch: `research/v4-decision-transition-memory`
- Run UUID: `appworld_legacy_replay_6h1_20260817_001`
- Executable source commit: `9cf471f8675908cb115f97b8be366ef3b05a3e0b`
- Branch: `appworld_010_execution_semantics_or_normalization_mismatch`
- EXP-024A generation: blocked
- Behavioral `p(s,m_transition)`: blocked
- V4 status: candidate; no tag created or moved

## Verified Result

The isolated package/code/data/evaluation triple is exactly
`0.1.0/0.1.0/0.1.0`. Official verification passes 138/138 tests and 147/147
tasks. The corrected sentinel passes 3/13 states, 5/13 complete histories,
93/102 prior observations, and 11/13 targets. Both no-history states pass.

All 11 normalized observation differences are login JWT timing differences.
One immutable source query has a supervisor identity hash inconsistent with
its task metadata. Full 45-state replay was not run because sentinel validation
failed.

## Recovery History

Eight append-only attempts are preserved. Setup failures identified Python
3.11 and Click 8.1.7 compatibility requirements plus one lexical-path
validator error. Sentinel v1 exposed a full-query/core-instruction comparison
bug and is preserved as superseded; v2 fixed it without changing replay data.
The first analysis failed on a redundant history-count field; analysis 002
resumed from the unchanged v2 summary. Every attempt records no scientific
parameter change.

## Next Review

Do not continue EXP-024A generation. A separately reviewed diagnostic should:

1. recover the historical execution-time/randomness contract responsible for
   authentication-token expiration values;
2. audit source query/task identity consistency across all 45 states;
3. preserve the locked exact-normalization result as the historical comparator;
4. rerun the fixed sentinel first, then run all 45 only after 13/13 passes.

Do not silently redact JWT fields from the locked metric after observing this
failure. A semantic-token normalization can be considered only as a separately
preregistered secondary replay definition.

## Artifacts

- Run:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/appworld_legacy_replay_6h1_20260817_001`
- Capsule: `/lambda/nfs/rcmf-persist/appworld_legacy/0.1.0`
- Summary: `final_exp024r_summary.json`
- Corrected sentinel: `replay/sentinel_summary_v2.json`
- Validation: `postrun_validation.json`
- Ledger: `attempts.jsonl`

No replay/model process is active. The `exp024r` tmux session is an idle shell,
the H100 is at 0% and 0 MiB, and Lambda is safe to terminate after final Git
synchronization.
