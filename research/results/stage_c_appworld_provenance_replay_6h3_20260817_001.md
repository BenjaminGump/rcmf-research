# EXP-024R3 Source-Snapshot Provenance Resolution

## Outcome

- Run UUID: `appworld_provenance_replay_6h3_20260817_001`
- Starting commit: `f5cfb8d4d00294c3fbc8721cf8409ecae3f528a9`
- Source commit: `b20ba47b060a09360bb89d0832a54a25aae9aee2`
- Decision: `source_dataset_identity_consistency_failure`
- Corpus identity match: `44/46` tasks
- Semantic replay: not run by the corpus-identity gate
- EXP-024A generation remains blocked: true

No Qwen model was imported or run, no memory condition was executed, no
AppWorld replay action was executed, and no model was trained.

## Corpus Identity Audit

The audit covered all `46` successful task trajectories, all `638` decision
examples, all `37` EXP-017 transition parents, all `92` EXP-020 query states,
and all `45` EXP-024A audit states. Every decision example was accounted for,
and every decision query agrees with its parent raw trajectory query.

| Task | Split | Decisions | EXP-020 | EXP-024A | Parent | Mismatched fields |
|---|---|---:|---:|---:|---|---|
| `b0a8eae_2` | validation | 18 | 2 | 5 | no | first/last/email/phone |
| `b0a8eae_3` | train | 17 | 2 | 0 | yes | first/last/email/phone |

The reconstructed capsule and immutable backup agree for both tasks. The
mismatch is not confined to the original five EXP-024A states.

## b0a8eae_2 Forensics

All immutable source layers agree on the source query. The recorded trajectory
behavior contains five references to the official supervisor identity and
zero references to the source-query identity, with no mixed-identity step.
The verified classification is `source_query_header_only_corruption`.

A task spec matching the source-query identity was found, but it is not a
coherent historical snapshot for this trajectory. Its task-root path hash is
`0d1e1559817488b374fb94a8a9ffdae4a986b1124f9b1812cf3df4caaa22db8a` and
its spec hash is
`3067026bc4925692faebba81e1e97aa5f436420cd3ed428ab95f97e13f0c7c88`.

## Bounded Snapshot Search

The deterministic search covered Persistent Filesystem project runs, legacy
roots/backups, historical outputs, task/database manifests, Git and Git-LFS
objects, and transfer-bundle inventory. Three task roots were enumerated.
One matching-identity task spec was found, but zero exact coherent historical
snapshots were found. Result: `exact_historical_snapshot_not_found`.

## Training Contamination

`b0a8eae_2` is held-out-only and does not occur in Stage-B train labels,
teacher-source memories, or EXP-017 transition parents. In contrast,
`b0a8eae_3` occurs in the Stage-B train split, Stage-B train labels,
teacher-source memories, and EXP-017 transition source parents.

A `b0a8eae_2`-only 40-state quarantine therefore cannot define a
provenance-valid corpus. The multiple-task hard stop takes precedence over
the recovered-snapshot and held-out-only quarantine paths.

## Replay and JWT Status

The EXP-024R2 JWT contract remains valid evidence: `11/11` token pairs differ
only in the allowed temporal `exp` claim and have zero non-temporal
mismatches. EXP-024R3 generated no new replay observations.

- Original 13-state sentinel: not run
- Provenance-valid sentinel: not created or run
- Original 45-state semantic replay: not run
- 40-state quarantine replay: not created or run

These are gate-controlled missing measurements, not zero-valued results.

## Prior-Result Sensitivity

Status: `not_run_source_dataset_identity_consistency_failure`. With more than
one unresolved task and train-side contamination, deleting only `b0a8eae_2`
would not yield a provenance-valid corpus. EXP-018 through EXP-023 metrics and
branches remain immutable and are not retroactively changed, but their
provenance scope now requires corpus-level review.

## Attempts and Recovery

One run UUID and four append-only completed attempts are preserved:

- `exp024r3-preflight-001`: source-file visibility was incorrectly coupled to
  source-layer agreement; preserved and superseded;
- `exp024r3-preflight-002`: found both mismatch tasks but overclassified a
  matching task spec as coherent; preserved and superseded;
- `exp024r3-preflight-003`: applied the corrected behavioral-coherence rule
  and produced the final scientific decision;
- `exp024r3-analysis-001`: finalized the branch from the validated decision.

All attempts exit `0`, record `scientific_parameter_changed=false`, and are
linked by parent-attempt and validated-checkpoint identity. Superseded outputs
remain under `attempt_snapshots/` with hashes.

## Validation

- Focused source/tests: `60 passed` locally and on Lambda
- Postrun validation: `28/28` checks passed
- Qwen imports/forwards/generations: `0/0/0`
- Memory-condition executions: `0`
- Model training runs: `0`
- Scientific parameters changed: false

## Interpretation

VERIFIED:

- Two source tasks have supervisor-identity inconsistencies.
- `b0a8eae_2` is source-query-header-only corruption relative to behavior.
- No exact coherent snapshot was recovered for `b0a8eae_2`.
- `b0a8eae_3` contaminates train-side memory and supervision.

INFERENCE:

- Corpus construction likely combined query headers from a different task
  metadata source with trajectories executed against official task identity.
  The exact upstream construction error is not yet proven.

UNVERIFIED:

- Semantic replay has not been validated for either 45 or 40 states.
- No prior scientific gate has been recomputed on a remediated corpus.
- EXP-024A raw-transition behavioral causality remains untested.

## Decision and Next Review

Stop at `source_dataset_identity_consistency_failure`. The next milestone must
be a corpus-level source-identity reconciliation and training-contamination
review before any replay, quarantine, retraining, generation, or behavioral
claim.

## Artifacts

- Run root:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/appworld_provenance_replay_6h3_20260817_001`
- Attempt ledger: `attempts.jsonl`
- Corpus audit: `corpus_identity_consistency.json`
- Decision rows: `decision_example_identity_rows.jsonl`
- b0 forensics: `b0a8eae_2_forensic_provenance.json`
- Snapshot search: `bounded_snapshot_search.json`
- Training audit: `training_contamination_audit.json`
- Gate decision: `preflight_decision.json`
- Final summary: `final_exp024r3_summary.json`
- Validation: `postrun_validation.json`

EXP-024A generation, replay, memory-condition execution, field/program work,
Stage C2, end-to-end RCMF, and V4 tagging remain blocked.
