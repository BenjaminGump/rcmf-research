# EXP-024R3 Structured Handoff

## State

- Branch: `research/v4-decision-transition-memory`
- Run UUID: `appworld_provenance_replay_6h3_20260817_001`
- Source: `b20ba47b060a09360bb89d0832a54a25aae9aee2`
- Decision: `source_dataset_identity_consistency_failure`
- Semantic replay: not run
- EXP-024A generation: blocked
- V4: candidate; no tag created or moved

## Verified Result

The audit covers `46` trajectories and `638` decisions. Identity matches
`44/46` tasks. `b0a8eae_2` is held-out-only, but `b0a8eae_3` is a train task,
transition parent, teacher-source memory, and Stage-B train-label source.
Both mismatch the agreeing official 0.1.0 capsule and backup.

`b0a8eae_2` is `source_query_header_only_corruption`: source layers agree on
the header, but behavior references only the official identity. One matching
task spec exists, but it is not a coherent snapshot. The bounded search found
no exact coherent historical snapshot.

## Gate Result

More than one task is inconsistent, so branch A stops the run. No quarantine,
sentinel, 45/40-state replay, or sensitivity recomputation was permitted.
Missing replay metrics are `not_run`, not zero. The inherited JWT component
remains 11 temporal-only differences and zero non-temporal differences.

## Recovery History

Four append-only attempts share one run UUID. The first two interpretations
are preserved and superseded; preflight 003 applies regression-tested rules;
analysis 001 finalizes the branch. All exit normally, preserve parent/resume
identity, and record no scientific parameter change. Validation passes 28/28.

## Next Review

Run a corpus-level identity reconciliation and train-contamination audit.
Trace both task headers to dataset construction and enumerate every artifact
derived from `b0a8eae_3`. Do not replay, generate, train, or silently remove
tasks before that decision.

## Artifacts

- Run:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/appworld_provenance_replay_6h3_20260817_001`
- Corpus: `corpus_identity_consistency.json`
- Forensics: `b0a8eae_2_forensic_provenance.json`
- Search: `bounded_snapshot_search.json`
- Training audit: `training_contamination_audit.json`
- Summary: `final_exp024r3_summary.json`
- Validation: `postrun_validation.json`
- Ledger: `attempts.jsonl`

No experiment process is active. Sync Lambda to the final record commit before
termination.
