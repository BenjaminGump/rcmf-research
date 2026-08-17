# EXP-025A Identity-Reconciled Source Corpus Audit

## Outcome

- Run UUID: `appworld_identity_reconciliation_7a_20260817_001`
- Starting and archive SHA: `4472a06d64a3104e5d48a17b30bb795ecd1fb61f`
- Source commit used for final analysis: `bc4e1fbf8d74d693fb2cef129402b19e7e9dd05b`
- Archive branch: `archive/v4-candidate-pre-identity-reconciliation`
- Working branch: `research/v4-identity-reconciled-corpus`
- Decision: `identity_reconciled_corpus_replay_failure`
- Structurally reconciled corpus candidate: ready
- Formally replay-validated clean corpus: not ready
- Generation and training remain blocked: true

No Qwen model was imported or run, no H100 was used, no model was trained,
and no AppWorld agent generation or full task evaluation was performed.

## Attempt Ledger

The append-only ledger contains `17` unique attempts and `34` start/end rows.
Every attempt has a terminal row and no scientific parameter changed. Three
failed implementation attempts are retained:

- `exp025a-affected-replay-001`: the legacy subprocess rejected a schema
  placeholder in an `access_token` field;
- `exp025a-sensitivity-001`: saved EXP-021 rows used a different utility key;
- `exp025a-sensitivity-002`: saved control rows used a different per-state
  schema.

Each failure received a regression test and resumed from the latest compatible
atomic output. The final attempt was `exp025a-analysis-001`, ending normally at
`2026-08-17T13:29:27.488052Z`. No laptop or monitoring interruption created a
duplicate run UUID or duplicate state row.

## Corpus-Builder Root Cause

The exact root cause was reproduced:

`official_trace_ingestion_rebuilt_query_from_unpinned_active_task_snapshot`

The historical ingestion path in
`scripts/prepare_appworld_official_traces.py` retained archived trajectory
actions and observations but reconstructed each query header through a live
`Task.load` lookup. The active task snapshot was not pinned to the trajectory's
execution snapshot. The two incorrect headers exactly match the currently
active project task specifications, while the verified AppWorld 0.1.0 capsule
and immutable 0.1.0 backup agree with each other and disagree with those
headers.

The corrected builder now requires a pinned task snapshot and validates all 46
task identities. No additional mismatched task was found, so the failure was
not a systematic task-ID, suffix, positional-offset, or adjacent-row join.
Historical source artifacts remain immutable.

## Affected-Task Forensics

| Task | Split | Decisions | Official refs | Bad-header refs | Classification |
|---|---|---:|---:|---:|---|
| `b0a8eae_2` | validation | 18 | 5 | 0 | `source_query_header_only_corruption` |
| `b0a8eae_3` | train | 17 | 8 | 0 | `source_query_header_only_corruption` |

For `b0a8eae_3`, all identity-sensitive actions, login calls, supervisor
responses, authenticated ownership, contacts/messages, and completion evidence
are coherent with the official identity. There are zero references to the
incorrect source-header identity, zero third-identity references, and no mixed
account or database ownership. Task ID and instruction also match official
metadata. This satisfies the preregistered header-only standard rather than a
majority-neutral shortcut.

## Candidate Semantic Replay

Candidate contracts changed only the query identity header to official
AppWorld 0.1.0 metadata. Recorded actions and observations were unchanged.

| Task | States | Prior observations | Complete | Exceptions | Non-temporal mismatches |
|---|---:|---:|---:|---:|---:|
| `b0a8eae_2` | 18 | 153 | 18/18 | 0 | 0 |
| `b0a8eae_3` | 17 | 136 | 17/17 | 0 | 0 |

Both tasks therefore receive the pre-registered remediation
`repair_query_header_to_official_metadata`. The remediation policy was frozen
before downstream sensitivity metrics were inspected.

## Reconciled Structural Corpus

The new immutable version is
`appworld_successful_trajectory_identity_reconciled_v1`.

- Tasks: `46` (`37` train, `9` validation)
- Decisions: `638`
- Extracted transitions: `638`
- Unaffected tasks byte-identical: `44/44`
- Repaired actions and observations unchanged: yes
- Reconciled task identities matching official metadata: `46/46`
- Duplicate decision/transition IDs: `0`
- Parent, lineage, leakage, and transition reconstruction checks: passed
- Corpus lineage SHA256:
  `f3389f8ddcc2de5f7b7807a6a8ef37ca38d3df3cde4155f01220240e65140dbb`

Seventeen `b0a8eae_3` transition IDs changed because the canonical query header
participates in transition identity. This is a structural lineage change, not
an action or observation edit.

## Dependency And Contamination Graph

The graph covers `27` old artifacts. Classification counts are:

| Classification | Artifacts |
|---|---:|
| `training_rows_invalidated` | 1 |
| `incremental_cache_recompute_required` | 5 |
| `model_retraining_required` | 15 |
| `report_recompute_required` | 6 |

Direct row effects are:

- source corpus: `37` affected decision rows;
- state representations: `35` rows;
- memory representations: `2` rows;
- raw-text teacher cache: `2,781/28,710` rows;
- Stage-B labels/manifests: `638` rows carry affected query or memory lineage;
- Stage-C1 response cache: `60/638` rows;
- Stage-5D pair cache: `121/1,728` rows;
- EXP-017 transition manifest: `17` transition rows;
- EXP-017 transition teacher cache: `696/4,640` rows;
- EXP-018 through EXP-021 structural/model artifacts: `52` directly affected
  rows in each lineage before model influence is considered.

Ten named checkpoint families require retraining: Stage-B addressing-only,
Stage-4B, Stage-4C, EXP-013 selector repair, Stage-C1, EXP-014, EXP-015,
EXP-016A, EXP-016B, and EXP-016C. In addition, trained EXP-017 through EXP-021
artifacts are not clean merely because invalid evaluation rows can be masked.
The train-side `b0a8eae_3` influence remains in their parameters.

## Minimum Recompute Estimate

- Qwen scoring rows: `3,658`
- State representation rows: `35`
- Memory representation rows: `2`
- Transition representation rows: `17`
- Expected new storage: `759,120,307` bytes
- H100 hours best/expected/conservative:
  `1.1255 / 1.4069 / 2.1104`
- Wall hours best/expected/conservative:
  `1.1255 / 1.6883 / 2.8138`
- Full historical V3 rerun required: no

The minimum V4 chain is affected representation regeneration, invalid
raw-teacher-row recomputation, label/manifest rebuild, retraining only current
V4-required models, procedural-coverage rerun, and one-step audit resumption
only after semantic replay passes.

## No-Retraining Sensitivity

This is explicitly a `contaminated-checkpoint sensitivity analysis`.

- No historical scientific branch is retroactively changed.
- No previously blocked EXP-018 through EXP-021 interaction gate passes its
  non-bootstrap checks after removing `b0a8eae_2` evaluation rows and masking
  `b0a8eae_3` candidates where saved predictions permit it.
- The overall V4-blocked conclusion is unchanged.
- EXP-022 fixed-panel coverage is numerically fragile: B coverage changes from
  `12/18 = 0.6667` to `10/16 = 0.6250` after provenance filtering.
- EXP-023 full-bank deployment coverage remains high at `15/16 = 0.9375`, but
  this is not a clean-model result.
- Aggregate-only Stage-B/Stage-4C artifacts cannot reproduce per-pair ranking
  metrics without checkpoint forwards, and every such checkpoint retains the
  `b0a8eae_3` train influence.

No checkpoint is declared clean and no historical failed gate is relabeled.

## Reconciled Replay Gate

The corrected 13-state sentinel passed twice:

- identity: `13/13`;
- complete histories: `13/13`;
- prior observations: `102/102`;
- targets: `13/13`;
- exceptions/non-temporal mismatches: `0/0`.

The complete 45-state replay did not pass the immutable semantic-v2 gate:

| Metric | Result |
|---|---:|
| Identity | 45/45 |
| Complete semantic histories | 42/45 |
| Prior semantic observations | 369/372 |
| Semantic targets | 45/45 |
| Complete semantic replay | 42/45 |
| Exceptions | 0 |
| Non-temporal JWT mismatches | 0 |

The three failures are `82e2fac_3` steps 6, 7, and 10, all at prior history
step 5. `spotify.login` returns a JWT as the root observation (`$`) rather than
inside the only preregistered token field, `access_token`. Expected and actual
tokens have matching headers and stable `sub` claims and differ only in `exp`
by 191 seconds. The semantic contract was deliberately not broadened after
observing this schema location. Thus the structural corpus is valid, but the
formal replay-clean corpus gate remains failed.

## Decision

Reached branch: `identity_reconciled_corpus_replay_failure`.

The immediate next review should preregister a narrow root-login-JWT semantic
contract and rerun replay validation before any model work. Only after that
gate passes should EXP-025B execute the minimum incremental cache/retraining
chain. EXP-024A generation, Qwen work, procedural field/program training,
Stage C2, end-to-end RCMF, and V4 tagging remain blocked.

## Reproducibility

- Artifact root:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/identity_reconciliation_7a_20260817_001`
- Structural corpus root:
  `/lambda/nfs/rcmf-persist/project/runs/appworld/appworld_successful_trajectory_identity_reconciled_v1_20260817_001`
- Artifact size: `71,361,903` bytes (`68M` filesystem display)
- Attempt ledger: `attempts.jsonl`
- Final summary: `final_exp025a_summary.json`
- Validation: `postrun_validation.json` with zero errors
- Local tests before record commit: `361 passed, 1 skipped`
- Lambda focused tests before record commit: `17 passed`
- GPU use: `0%`, `0 MiB`; H100 hours: `0`
