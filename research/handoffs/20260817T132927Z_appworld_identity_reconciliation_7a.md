# EXP-025A Structured Handoff

## Identity

- Run: `appworld_identity_reconciliation_7a_20260817_001`
- Branch: `research/v4-identity-reconciled-corpus`
- Starting/archive SHA: `4472a06d64a3104e5d48a17b30bb795ecd1fb61f`
- Final-analysis source SHA: `bc4e1fbf8d74d693fb2cef129402b19e7e9dd05b`
- Artifact:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/identity_reconciliation_7a_20260817_001`
- Decision: `identity_reconciled_corpus_replay_failure`

## VERIFIED

- The old builder reconstructed headers from an unpinned active AppWorld task
  snapshot while retaining archived trajectory actions/observations.
- `b0a8eae_2` and `b0a8eae_3` are both
  `source_query_header_only_corruption`; no other task is affected.
- Official-header candidate replay passes `18/18` and `17/17` states with no
  exception or non-temporal mismatch.
- Both tasks receive `repair_query_header_to_official_metadata` under the
  pre-registered policy.
- The reconciled structural corpus has 46 tasks, 638 decisions, 638
  transitions, and lineage
  `f3389f8ddcc2de5f7b7807a6a8ef37ca38d3df3cde4155f01220240e65140dbb`.
- Structural validation passes. Actions/observations are unchanged and all 44
  unaffected tasks are byte-identical.
- The dependency graph contains 27 artifacts. There are 3,658 Qwen scoring
  rows and 35/2/17 state/memory/transition representation rows to recompute;
  ten named Stage-B through EXP-016C checkpoint families require retraining.
- The twice-run 13-state sentinel passes fully.
- Full replay is 42/45 complete with 369/372 prior observations and 45/45
  targets. The three failures are root-level Spotify login JWT observations
  differing only in the allowed temporal meaning, but outside the frozen
  `access_token` schema location.
- No Qwen, H100, model training, generation, full task evaluation, or V4 tag
  occurred.

## INFERENCE

- The repaired corpus is structurally suitable for a future clean rebuild.
- The old checkpoints remain scientifically contaminated by train-side
  `b0a8eae_3` even where evaluation-row masking is possible.
- A narrowly preregistered root-login-JWT semantic contract is likely to make
  replay pass, but this was not assumed or changed post hoc.

## UNVERIFIED

- No clean checkpoint has yet been retrained on the reconciled corpus.
- No one-step memory condition has been generated or executed.
- No claim that the reconciled structural corpus improves model performance is
  available.

## Next Preconditions

1. Review and preregister root-level login-JWT semantic equivalence without
   broad timestamp/JWT normalization.
2. Rerun the fixed sentinel and all 45 states; require a strict pass.
3. Regenerate only affected representations and teacher rows.
4. Rebuild labels/manifests and retrain only checkpoints required by the
   current V4 hypothesis.
5. Rerun procedural coverage before any one-step causal audit.

Do not automatically rerun all historical V3 experiments. Generation and
training remain blocked until replay validation passes.
