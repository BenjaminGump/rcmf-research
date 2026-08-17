# EXP-024R Exact AppWorld 0.1.0 Replay Validation

## Outcome

- Run UUID: `appworld_legacy_replay_6h1_20260817_001`
- Initial source commit: `8867d19b7ac059728cf2911add7a3849ea2c9cc9`
- Final executable source commit: `9cf471f8675908cb115f97b8be366ef3b05a3e0b`
- Decision: `appworld_010_execution_semantics_or_normalization_mismatch`
- Full 45-state replay: `not_run_blocked_by_sentinel`
- AppWorld version mismatch causally confirmed: `false`
- EXP-024A generation remains blocked: `true`

The exact 0.1.0 capsule materially improved replay relative to 0.2.0.dev0, but
the preregistered exact gate did not pass. No Qwen model was imported or run,
no memory condition was executed, and no generated action was evaluated.

## Verified Environment

- Python: `3.11.15`
- Python executable:
  `/home/ubuntu/venvs/appworld-0.1.0-replay-py311-click817/bin/python`
- AppWorld CLI:
  `/home/ubuntu/venvs/appworld-0.1.0-replay-py311-click817/bin/appworld`
- Package/code/data/evaluation: `0.1.0/0.1.0/0.1.0`
- Isolated APPWORLD_ROOT:
  `/lambda/nfs/rcmf-persist/appworld_legacy/0.1.0/root`
- Wheel SHA256:
  `128bdb088bd1c76b8ac763e831334f0843507e9e5a5e2e88ec4e2949e2e5d476`
- Dependency-wheel manifest SHA256:
  `6c1b52d1d833c4c88986ea8334e64049a20e3d7222c081f565ea0b8651c9c664`
- `pip freeze` SHA256:
  `9d2fcf8a8b2009eb142d84aba677839e8d7caa1dfc646ed5aaa41a2f05c74644`
- Root manifest: 17,995 files, 176,204,608 bytes, SHA256
  `b998bc041922cae81059321e5019dd40836fefc310921a9b797f71e47e574122`
- Official verification: tests `138/138`; tasks `147/147`.

The wheel metadata allows Python 3.10, but the official 0.1.0 source imports
`typing.Self`; the isolated runtime therefore uses Python 3.11. The release
contains no dependency lock. Click 8.4.2 breaks the official Typer 0.12.5 CLI,
so release-era Click 8.1.7 is pinned. This is recorded as an infrastructure
compatibility constraint, not a scientific parameter change.

## Sentinel Replay

The immutable sentinel has 13 states across all 9 held-out tasks, including
both no-history states and two old step-2 divergence states.

| Metric | AppWorld 0.1.0 |
|---|---:|
| Initial task identity | 12/13 |
| Complete prior history | 5/13 |
| Prior observations | 93/102 |
| Target observations | 11/13 |
| Complete replay | 3/13 |
| No-history complete replay | 2/2 |

First divergence steps were: step 4 for 2 states, step 5 for 6, step 6 for 1,
step 7 for 1, and no divergence for 3.

All 11 locked-normalization mismatches are authentication JWT differences.
Ten JWT expiration values differ by 191 seconds; one later login differs by
834 seconds. The stable JWT payload and non-token response fields match. The
locked normalization was not changed.

One initial identity mismatch remains at
`appworld:trace:b0a8eae_2:step:7:line:284`: task ID, task instruction, and DB
version match, but the immutable source query's supervisor identity hash does
not match the 0.1.0 task metadata hash. Raw credentials are not copied into
the Git record.

## Paired Comparison

The paired figures below use the exact same 13 sentinel states.

| Environment | Complete | Histories | Prior observations | Targets |
|---|---:|---:|---:|---:|
| AppWorld 0.2.0.dev0 | 0/13 | 2/13 | 27/102 | 6/13 |
| AppWorld 0.1.0 | 3/13 | 5/13 | 93/102 | 11/13 |

The immutable 0.2.0.dev0 full-45 reference remains 0/45 complete, 2/45
complete histories, 81/372 prior observations, and 23/45 targets. No matched
0.1.0 full-45 result exists because the sentinel gate blocked it.

## Attempts And Validation

Eight append-only attempts are preserved under one run UUID:

- setup 001: failed because the official source requires Python 3.11;
- setup 002: failed because unconstrained Click 8.4.2 breaks the legacy CLI;
- setup 003: failed because a validator dereferenced the venv Python symlink;
- setup 004: completed the verified capsule;
- sentinel 001: completed but used an invalid full-query/core-instruction
  identity comparison and is preserved as superseded;
- sentinel 002: completed with the corrected v2 identity contract;
- analysis 001: failed on a missing redundant history-count field;
- analysis 002: completed from the unchanged v2 sentinel checkpoint.

Every attempt records `scientific_parameter_changed=false`. Active process
time was 515.760 seconds; successful-attempt time was 422.879 seconds. The
wall span, including source fixes and synchronization, was 2,689.049 seconds.
H100 use was zero. Post-run validation passed every check.

## Interpretation

VERIFIED:

- Exact package/data/evaluation version matching is necessary and substantially
  improves replay.
- It is not sufficient for the locked exact-observation gate.
- The residual normalized differences are time-dependent login-token outputs,
  plus one source-query/task-supervisor identity inconsistency.

INFERENCE:

- Historical execution-time state or a source snapshot detail beyond the
  released 0.1.0 bundle is the leading remaining explanation.

UNVERIFIED:

- The 0.2.0.dev0 versus 0.1.0 mismatch was not confirmed as the sole causal
  reason for EXP-024A failure.
- Full 45-state exact replay under 0.1.0 was not run.

## Artifacts

- Run root:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/appworld_legacy_replay_6h1_20260817_001`
- Reproducibility capsule:
  `/lambda/nfs/rcmf-persist/appworld_legacy/0.1.0`
- Corrected sentinel summary: `replay/sentinel_summary_v2.json`
- Preserved superseded summary: `replay/sentinel_summary.json`
- Final summary: `final_exp024r_summary.json`
- Validation: `postrun_validation.json`
- Attempt ledger: `attempts.jsonl`

Artifact-root size is 6,277,720 bytes. The external isolated data root is
176,204,608 bytes. Behavioral program training, field training, generation,
Stage C2, and V4 tagging remain blocked.
