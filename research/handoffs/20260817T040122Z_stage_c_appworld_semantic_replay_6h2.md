# EXP-024R2 Structured Handoff

## State

- Branch: `research/v4-decision-transition-memory`
- Run UUID: `appworld_semantic_replay_6h2_20260817_001`
- Final executable/validator source:
  `ad6ce7f110d147f05abac8ce9b1080ea2f151cde`
- Decision: `source_query_task_identity_snapshot_unresolved`
- JWT semantic contract: validated for the 11 observed pairs
- Semantic replay: not validated
- EXP-024A generation: blocked
- V4 status: candidate; no tag created or moved

## Verified Result

AppWorld 0.1.0 generates HS256 JWTs with `sub=<app>+<username>` and a single
time-dependent `exp` claim. All 11 expected/actual pairs have matching headers
and stable claims; both sides validate under installed AppWorld, actual tokens
work for subsequent authenticated calls, and there are zero non-temporal
differences. Ten expiration deltas are 191 seconds and one is 834 seconds.

The all-45 identity audit passes 40 states. All five failures are states from
`b0a8eae_2`. The source decision text, raw trajectory, and replay contract
agree, but their supervisor identity differs from both retained official
0.1.0 task snapshots. No matching immutable snapshot was found.

## Gate Result

The identity prerequisite failed, so the repeated 13-state semantic sentinel
and full 45-state semantic replay were not run. This is not a replay failure
count. Semantic replay remains unvalidated, and no Qwen or memory condition
ran.

## Recovery History

Six append-only attempts use one run UUID. Four preflight attempts exposed and
preserved immutable-summary, schema-placeholder JWT, JWT-index, and optional
manifest-coverage bugs. Attempt 005 reused the validated identity-probe
checkpoint and completed; analysis 001 completed. All attempts record no
scientific parameter change. Postrun validation passes 23/23.

## Next Review

Do not resume EXP-024A generation. First locate or prove unavailable the exact
historical `b0a8eae_2` task/supervisor snapshot matching the source-query hash,
or formally adjudicate the source trajectory as identity-inconsistent. Do not
edit identities or substitute states. Only after 45/45 identity provenance may
the fixed sentinel run twice under semantic v2, followed by the full 45.

## Artifacts

- Run:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/appworld_semantic_replay_6h2_20260817_001`
- Capsule: `/lambda/nfs/rcmf-persist/appworld_legacy/0.1.0`
- Gate decision: `preflight_decision.json`
- Final summary: `final_exp024r2_summary.json`
- Validation: `postrun_validation.json`
- Ledger: `attempts.jsonl`

No experiment process is active. The `exp024r2` tmux session is an idle shell,
the GPU is idle, and Lambda is safe to terminate after final Git sync.
